"""What the Space shows, as plain functions: no Gradio here, so the tests don't need it.

Everything comes from tools/policy_eval.py and the same policy JSON files Terraform deploys.
"""

from __future__ import annotations

import html
import json
import tempfile
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from finops import sample
from finops.costs import read_exports
from finops.inventory import load_json_rows
from finops.report import build, to_html
from tools.policy_eval import PolicyError, Result, decide, load

ROOT = Path(__file__).resolve().parent
# In the Space the examples sit next to this file; in the repo, one folder up.
EXAMPLES_DIR = next(d for d in (ROOT / "examples", ROOT.parent / "examples") if d.is_dir())
MAX_INPUT = 20_000  # characters; real resource definitions are far smaller

# Where a subscription can sit. The root and landing-zones groups only hold other groups.
GROUPS = {
    "online": "Online landing zone: internet-facing apps",
    "corp": "Corp landing zone: internal apps",
    "platform": "Platform: shared networking, identity, logs",
    "sandbox": "Sandbox: experiments",
    "decommissioned": "Decommissioned: retired subscriptions",
}

GROUP_NAMES = {
    "root": "Guardrails (root)",
    "platform": "Platform",
    "landing-zones": "Landing zones",
    "corp": "Corp",
    "online": "Online",
    "sandbox": "Sandbox",
    "decommissioned": "Decommissioned",
}

# label: (group, resource example, resource group example or None)
EXAMPLES: dict[str, tuple[str, str, str | None]] = {
    "Resource group with no tags": ("online", "rg-untagged", None),
    "The same, in the sandbox": ("sandbox", "rg-untagged", None),
    "VM with no tags, in a tagged group": ("online", "vm-untagged", "rg-tagged"),
    "VM in East US": ("online", "vm-us-region", None),
    "Storage open to the internet": ("online", "storage-public", "rg-tagged"),
    "Public IP address in Corp": ("corp", "public-ip", None),
    "Public IP address in Online": ("online", "public-ip", None),
    "Anything in a decommissioned subscription": ("decommissioned", "rg-tagged", None),
}

EFFECT_ORDER = {"Deny": 0, "Audit": 1, "Modify": 2}
EFFECT_MEANING = {
    "Deny": "blocked",
    "Audit": "allowed, but reported as non-compliant",
    "Modify": "changed on the way in",
}


@dataclass
class Outcome:
    allowed: bool | None  # None when the input couldn't be checked
    rows: list[list[str]] = field(default_factory=list)  # effect, rule and where it's set, why
    tags_before: dict[str, Any] | None = None
    tags_after: dict[str, Any] | None = None
    error: str | None = None


def example_text(name: str | None) -> str:
    if name is None:
        return ""
    return (EXAMPLES_DIR / f"{name}.json").read_text()


def load_example(label: str) -> tuple[str, str, str]:
    """The group, resource JSON and resource group JSON for an example."""
    group, resource, resource_group = EXAMPLES[label]
    return group, example_text(resource), example_text(resource_group)


def _parse(text: str, what: str, required: bool) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        if required:
            raise ValueError(f"Paste the {what} as JSON.")
        return None
    if len(text) > MAX_INPUT:
        raise ValueError(f"The {what} is longer than {MAX_INPUT:,} characters.")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"The {what} isn't valid JSON: {e.msg} (line {e.lineno}, column {e.colno}).") from e
    if not isinstance(value, dict):
        raise ValueError(f"The {what} should be a JSON object, like the examples.")
    if what == "resource" and not value.get("type"):
        raise ValueError('The resource needs a "type", such as "Microsoft.Storage/storageAccounts".')
    return value


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _title(definition: dict[str, Any], parameters: dict[str, Any]) -> str:
    title = _capital(definition["properties"]["displayName"].removeprefix("Guardrails: "))
    if "tagName" in parameters:
        title += f": {parameters['tagName']}"
    return title


def _why(result: Result, resource: dict[str, Any], resource_group: dict[str, Any] | None, fallback: str) -> str:
    """What exactly is wrong with (or changed on) this resource, in a sentence."""
    key = result.rule.split(" ")[0]
    params = result.parameters
    props = resource.get("properties") or {}
    if key in ("allowed-locations", "allowed-resource-group-locations"):
        allowed = ", ".join(params["allowedLocations"])
        return f"{resource.get('location') or 'No region'} isn't an allowed region. Allowed: {allowed}."
    if key == "require-resource-group-tag":
        return f"The resource group has no {params['tagName']} tag, so nobody would know who owns it or who pays."
    if key == "inherit-resource-group-tag":
        tag = params["tagName"]
        value = ((resource_group or {}).get("tags") or {}).get(tag, "")
        return f"The resource has no {tag} tag, so it gets the resource group's: {value}."
    if key == "deny-storage-public-access":
        return "allowBlobPublicAccess isn't false, so anyone on the internet could read its blobs without signing in."
    if key == "require-storage-secure-transfer":
        problems = []
        if str(props.get("supportsHttpsTrafficOnly")).lower() != "true":
            problems.append("accepts plain HTTP")
        tls = props.get("minimumTlsVersion")
        if tls not in ("TLS1_2", "TLS1_3"):
            problems.append(f"allows {tls}" if tls else "sets no minimum TLS version")
        return f"It {' and '.join(problems)}. Storage must use HTTPS only, with TLS 1.2 or newer."
    if key == "deny-public-ip":
        return "Corp apps are reached only over the private network, so public IP addresses aren't allowed here."
    if key == "deny-new-resources":
        return "This subscription is decommissioned: resources can be deleted, but nothing can be created or changed."
    return fallback


def evaluate(group: str, resource_text: str, resource_group_text: str = "") -> Outcome:
    """What Azure Policy does with this resource in this management group."""
    try:
        resource = _parse(resource_text, "resource", required=True)
        resource_group = _parse(resource_group_text, "resource group", required=False)
        assert resource is not None
        decision = decide(group, resource, resource_group)
    except (ValueError, PolicyError) as e:
        return Outcome(allowed=None, error=str(e))

    _, _, assignments = load()
    messages = {f"{a['scope']}/{a['name']}": a.get("nonComplianceMessage", "") for a in assignments}
    rows = []
    for r in sorted(decision.results, key=lambda r: EFFECT_ORDER.get(r.effect, 9)):
        scope = r.assignment.split("/")[0]
        fallback = messages.get(r.assignment) or r.definition["properties"]["description"]
        rows.append(
            [
                f"**{r.effect}**",
                f"{_title(r.definition, r.parameters)}<br><small>set at {GROUP_NAMES[scope]}</small>",
                _why(r, decision.resource, resource_group, fallback),
            ]
        )
    return Outcome(
        allowed=decision.allowed,
        rows=rows,
        tags_before=resource.get("tags") or {},
        tags_after=decision.resource.get("tags") or {},
    )


def verdict_html(outcome: Outcome) -> str:
    if outcome.error:
        return f'<div class="verdict error"><strong>Can\'t check that yet</strong><span>{html.escape(outcome.error)}</span></div>'
    counts: dict[str, int] = {}
    for effect, *_ in outcome.rows:
        name = effect.strip("*")
        counts[name] = counts.get(name, 0) + 1
    if outcome.allowed:
        title, kind = "Allowed", "allowed"
        if not counts:
            detail = "No rule applies: Azure creates it as it is."
        else:
            parts = [f"{n} {'rule' if n == 1 else 'rules'} {EFFECT_MEANING[e]}" for e, n in counts.items()]
            detail = "Created, with " + "; ".join(parts) + "."
    else:
        title, kind = "Denied", "denied"
        n = counts["Deny"]
        detail = f"{n} {'rule blocks' if n == 1 else 'rules block'} it. Azure refuses the request and nothing is created."
    return f'<div class="verdict {kind}"><strong>{title}</strong><span>{html.escape(detail)}</span></div>'


def tags_text(outcome: Outcome) -> str:
    if outcome.error or outcome.tags_after == outcome.tags_before:
        return ""
    return json.dumps(outcome.tags_after, indent=2)


def rules_rows() -> list[list[str]]:
    """Every assignment, top of the hierarchy first: where, what, and what it does."""
    definitions, initiatives, assignments = load()
    order = list(GROUP_NAMES)
    rows = []
    for a in sorted(assignments, key=lambda a: order.index(a["scope"])):
        kind, key = a["policy"].split(":")
        if kind == "initiative":
            names = [definitions[p["definition"]]["properties"]["displayName"] for p in initiatives[key]["policies"]]
        else:
            names = [definitions[key]["properties"]["displayName"]]
        covers = "; ".join(_capital(n.removeprefix("Guardrails: ")) for n in dict.fromkeys(names))
        effects = ", ".join(str(v) for k, v in a["parameters"].items() if k.lower().endswith("effect"))
        rows.append([GROUP_NAMES[a["scope"]], a["displayName"], covers, effects])
    return rows


HIERARCHY = """\
Guardrails (root)            data residency: Canada only, everywhere below
├── Platform                 baseline
├── Landing zones            baseline
│   ├── Corp                 + no public IP addresses
│   └── Online
├── Sandbox                  baseline, audit only
└── Decommissioned           clean-up only"""


@cache
def finops_html() -> str:
    """The FinOps report on the sample month: the same code the daily workflow runs on real exports."""
    with tempfile.TemporaryDirectory() as folder:
        paths = sample.write(Path(folder))
        report = build(read_exports([paths["costs"]]), load_json_rows(paths["resources"]), load_json_rows(paths["advisor"]))
    return to_html(
        report,
        title="FinOps report (sample data)",
        note=(
            "Made-up data in Azure's export format: the five pretend teams' environments, the platform, and a "
            "forgotten proof of concept with no tags. Once Guardrails runs in Azure, a daily workflow builds this "
            "same report from the real cost export, Resource Graph and Azure Advisor."
        ),
    )
