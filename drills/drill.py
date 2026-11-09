"""The mess drill: seed a forgotten proof of concept, then time how long Guardrails takes to find it
and how long it takes to fix. Run in Azure Cloud Shell, as the subscription's owner, after bootstrap:

    python drills/drill.py seed --yes    # create the mess (about $0.30 an hour while it exists)
    python drills/drill.py measure       # start the clock: find it, fix it, time both
    python drills/drill.py clean --yes   # delete everything

`measure` removes the seeding exemption, asks Azure Policy to scan, and waits until every mess
resource is reported. Then it fixes things: remediation tasks copy the missing tags from the
resource group, and Terraform closes the public storage, deletes the idle disk, IP and plan, and
shrinks the database. It waits for the resource group to be fully compliant, prices what was
removed with Azure's public retail prices, and writes drills/results/<date>.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MESS = ROOT / "drills" / "mess"
RESULTS = ROOT / "drills" / "results"
KEY = Path.home() / ".ssh" / "guardrails-drill"
BASELINE = "landing-zones/gr-baseline"
INHERIT = ["inherit-owner", "inherit-cost-center", "inherit-environment"]
PRICES_API = "https://prices.azure.com/api/retail/prices"
HOURS_A_MONTH, DAYS_A_MONTH = 730, 30.4

# What fixing removes, priced from Azure's retail prices: (label, filter, count).
# A negative count is what's kept instead (the smaller database), so savings are the difference.
PRICED = [
    ("Unattached 32 GiB Standard HDD disk", "serviceName eq 'Storage' and skuName eq 'S4 LRS' and meterName eq 'S4 LRS Disk'", 1),
    ("Orphaned static public IP", "serviceName eq 'Virtual Network' and meterName eq 'Standard IPv4 Static Public IP'", 1),
    (
        "Empty App Service plan (B1 Linux)",
        "serviceName eq 'Azure App Service' and skuName eq 'B1' and productName eq 'Azure App Service Basic Plan - Linux'",
        1,
    ),
    (
        "Database at S3 (100 DTUs)",
        "serviceName eq 'SQL Database' and skuName eq 'S3' and productName eq 'SQL Database Single Standard'",
        1,
    ),
    (
        "Database at S0 (10 DTUs) instead",
        "serviceName eq 'SQL Database' and skuName eq 'S0' and productName eq 'SQL Database Single Standard'",
        -1,
    ),
]

Shell = Callable[..., str]


def run_shell(args: list[str], cwd: Path | None = None) -> str:  # pragma: no cover - runs real commands
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def fetch_json(url: str) -> dict:  # pragma: no cover - calls the public prices API
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


@dataclass
class Clock:
    now: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


def terraform(shell: Shell, *args: str) -> str:
    return shell(["terraform", f"-chdir={MESS}", *args])


def mess_vars(exempt: bool, fixed: bool) -> list[str]:
    return ["-var", f"exempt={str(exempt).lower()}", "-var", f"fixed={str(fixed).lower()}"]


# --- Seed and clean ---------------------------------------------------------------------------------


def seed(shell: Shell, key: Path | None = None) -> None:
    key = key or KEY
    assignments = json.loads(shell(["terraform", f"-chdir={ROOT / 'platform'}", "output", "-json", "assignment_ids"]))
    owner = shell(["az", "account", "show", "--query", "user.name", "-o", "tsv"]).strip()
    if not key.with_suffix(".pub").exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        shell(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "guardrails-drill", "-f", str(key)])
    settings = {
        "baseline_assignment_id": assignments[BASELINE],
        "owner": owner,
        "ssh_public_key": key.with_suffix(".pub").read_text().strip(),
    }
    (MESS / "drill.auto.tfvars.json").write_text(json.dumps(settings, indent=2))
    terraform(shell, "init", "-input=false")
    terraform(shell, "apply", "-auto-approve", "-input=false", *mess_vars(exempt=True, fixed=False))


def clean(shell: Shell) -> None:
    terraform(shell, "destroy", "-auto-approve", "-input=false", *mess_vars(exempt=False, fixed=True))


# --- Measure -----------------------------------------------------------------------------------------


@dataclass
class Result:
    started: str
    resource_group: str
    expected: int
    found: int = 0
    minutes_to_find: float | None = None
    rules_broken: dict[str, int] = field(default_factory=dict)
    remediation: dict[str, str] = field(default_factory=dict)
    minutes_to_fix: float | None = None
    still_non_compliant: int | None = None
    savings: dict[str, float | None] = field(default_factory=dict)

    @property
    def monthly_savings(self) -> float:
        return round(sum(v for v in self.savings.values() if v is not None), 2)


def non_compliant(shell: Shell, group: str) -> list[dict]:
    out = shell(
        [
            "az",
            "policy",
            "state",
            "list",
            "--resource-group",
            group,
            "--filter",
            "complianceState eq 'NonCompliant'",
            "--query",
            "[].{id: resourceId, rule: policyDefinitionReferenceId}",
            "-o",
            "json",
        ]
    )
    return json.loads(out or "[]")


def wait(check: Callable[[], bool], clock: Clock, interval: float, timeout: float) -> bool:
    deadline = clock.now() + timeout
    while True:
        if check():
            return True
        if clock.now() >= deadline:
            return False
        clock.sleep(interval)


def price_monthly(fetch: Callable[[str], dict], region: str, flt: str, currency: str = "CAD") -> float | None:
    """Monthly retail price of the first pay-as-you-go meter matching a filter, or None."""
    query = f"armRegionName eq '{region}' and priceType eq 'Consumption' and {flt}"
    url = f"{PRICES_API}?currencyCode='{currency}'&$filter={urllib.parse.quote(query)}"
    for item in fetch(url).get("Items", []):
        unit = str(item.get("unitOfMeasure", ""))
        price = float(item.get("retailPrice", 0))
        if "Hour" in unit:
            return round(price * HOURS_A_MONTH, 2)
        if "Day" in unit:
            return round(price * DAYS_A_MONTH, 2)
        if "Month" in unit:
            return round(price, 2)
    return None


def measure(
    shell: Shell,
    clock: Clock | None = None,
    fetch: Callable[[str], dict] = fetch_json,
    interval: float = 60,
    timeout: float = 3600,
    region: str = "canadacentral",
) -> Result:
    clock = clock or Clock()
    group = json.loads(terraform(shell, "output", "-json", "resource_group"))
    expected = {i.lower() for i in json.loads(terraform(shell, "output", "-json", "resource_ids"))}
    assignment = json.loads((MESS / "drill.auto.tfvars.json").read_text())["baseline_assignment_id"]
    result = Result(started=datetime.now(UTC).isoformat(timespec="seconds"), resource_group=group, expected=len(expected))

    # The clock starts when the exemption goes: from now on the guardrails apply to the mess.
    terraform(shell, "apply", "-auto-approve", "-input=false", *mess_vars(exempt=False, fixed=False))
    start = clock.now()
    shell(["az", "policy", "state", "trigger-scan", "--resource-group", group, "--no-wait"])

    flagged: list[dict] = []

    def all_found() -> bool:
        flagged[:] = non_compliant(shell, group)
        return expected <= {f["id"].lower() for f in flagged}

    if wait(all_found, clock, interval, timeout):
        result.minutes_to_find = round((clock.now() - start) / 60, 1)
    result.found = len(expected & {f["id"].lower() for f in flagged})
    for f in flagged:
        rule = f.get("rule") or "(whole assignment)"
        result.rules_broken[rule] = result.rules_broken.get(rule, 0) + 1

    # Fix: tags through remediation tasks, everything else through Terraform.
    for ref in INHERIT:
        shell(
            [
                "az",
                "policy",
                "remediation",
                "create",
                "--name",
                f"drill-{ref}",
                "--resource-group",
                group,
                "--policy-assignment",
                assignment,
                "--definition-reference-id",
                ref,
            ]
        )
    terraform(shell, "apply", "-auto-approve", "-input=false", *mess_vars(exempt=False, fixed=True))

    def remediated() -> bool:
        for ref in INHERIT:
            state = shell(
                [
                    "az",
                    "policy",
                    "remediation",
                    "show",
                    "--name",
                    f"drill-{ref}",
                    "--resource-group",
                    group,
                    "--query",
                    "provisioningState",
                    "-o",
                    "tsv",
                ]
            ).strip()
            result.remediation[ref] = state
        return all(s in ("Succeeded", "Failed", "Canceled") for s in result.remediation.values())

    wait(remediated, clock, interval, timeout)
    shell(["az", "policy", "state", "trigger-scan", "--resource-group", group, "--no-wait"])

    remaining: list[dict] = []

    def compliant() -> bool:
        remaining[:] = non_compliant(shell, group)
        return not remaining

    if wait(compliant, clock, interval, timeout):
        result.minutes_to_fix = round((clock.now() - start) / 60, 1)
    result.still_non_compliant = len({r["id"].lower() for r in remaining})

    for label, flt, count in PRICED:
        price = price_monthly(fetch, region, flt)
        result.savings[label] = None if price is None else round(price * count, 2)
    return result


def to_markdown(r: Result) -> str:
    def minutes(m: float | None) -> str:
        return "not within the time limit" if m is None else f"{m:g} minutes"

    lines = [
        f"### Mess drill, {r.started[:10]}",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Mess resources found by Azure Policy | {r.found} of {r.expected} |",
        f"| Time to find them all | {minutes(r.minutes_to_find)} |",
        f"| Time until the resource group was compliant | {minutes(r.minutes_to_fix)} |",
        f"| Still non-compliant afterwards | {r.still_non_compliant} |",
        f"| Monthly savings from the clean-up, at retail prices | ${r.monthly_savings:,.2f} |",
        "",
        "| Rule broken | Resources |",
        "|---|---|",
        *[f"| {rule} | {n} |" for rule, n in sorted(r.rules_broken.items())],
        "",
        "| Removed or resized | A month |",
        "|---|---|",
        *[f"| {label} | {'price not found' if v is None else f'${v:,.2f}'} |" for label, v in r.savings.items()],
    ]
    return "\n".join(lines) + "\n"


def save(r: Result, folder: Path | None = None) -> Path:
    folder = folder or RESULTS
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{r.started[:10]}.json"
    path.write_text(json.dumps({**asdict(r), "monthly_savings": r.monthly_savings}, indent=2) + "\n")
    return path


def main(argv: list[str] | None = None, shell: Shell = run_shell) -> int:
    parser = argparse.ArgumentParser(description="Seed, measure and clean up the mess drill.")
    parser.add_argument("step", choices=["seed", "measure", "clean"])
    parser.add_argument("--yes", action="store_true", help="confirm creating or deleting Azure resources")
    parser.add_argument("--interval", type=float, default=60, help="seconds between compliance checks")
    parser.add_argument("--timeout", type=float, default=3600, help="seconds to wait for each stage")
    args = parser.parse_args(argv)

    if args.step in ("seed", "clean") and not args.yes:
        print(
            f"This will {'create' if args.step == 'seed' else 'delete'} the mess in Azure"
            + (
                ": two B1s VMs, an S3 database, storage, a disk, a public IP and an App Service plan, "
                "about $0.30 an hour while it exists."
                if args.step == "seed"
                else "."
            )
            + " Run it again with --yes to go ahead."
        )
        return 2
    if args.step == "seed":
        seed(shell)
        print("Seeded. Run `python drills/drill.py measure` to start the clock.")
    elif args.step == "clean":
        clean(shell)
        print("The mess is gone.")
    else:
        result = measure(shell, interval=args.interval, timeout=args.timeout)
        path = save(result)
        print(to_markdown(result))
        print(f"Saved {path}. Run `python drills/drill.py clean --yes` to delete the mess.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
