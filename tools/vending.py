"""Check environment requests before Terraform sees them, and summarise them for a pull request.

Each file in environments/ asks for one environment: a resource group with owner tags, a monthly
budget, access for Entra ID groups, and optionally its own address space.

    python tools/vending.py check            # exit 1 and list every problem, or print OK
    python tools/vending.py summary          # a Markdown table for the pull request

The checks here are the ones Terraform can't do on its own: across files (names and address
spaces must be unique) and against the guardrails themselves (the resource group a request
creates must pass every policy in the management group it lands in).
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.policy_eval import PARENTS, decide

ROOT = Path(__file__).resolve().parent.parent
REQUESTS_DIR = ROOT / "environments"
TFVARS = ROOT / "platform" / "guardrails.auto.tfvars"

ENVIRONMENTS = ("dev", "test", "prod")
BUDGET_CAPS = {"dev": 500, "test": 500, "prod": 5000}  # CAD a month; more needs a platform review
ADDRESS_POOL = ipaddress.ip_network("10.100.0.0/16")
PREFIXES = range(24, 27)  # /24 to /26
FIELDS = {"team", "environment", "owner", "cost_center", "region", "monthly_budget", "access", "address_space"}
REQUIRED = {"team", "environment", "owner", "cost_center", "monthly_budget"}

TEAM = re.compile(r"^[a-z][a-z0-9-]{1,20}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COST_CENTER = re.compile(r"^cc-\d{4}$")
GROUP = re.compile(r"^grp-[a-z0-9-]{2,60}$")


@dataclass
class Request:
    file: str
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return f"{self.data['team']}-{self.data['environment']}"

    @property
    def region(self) -> str:
        return self.data.get("region", "canadacentral")

    def access(self, role: str) -> list[str]:
        return list((self.data.get("access") or {}).get(role, []))

    def resource_group(self) -> dict[str, Any]:
        """The resource group Terraform will create, as Azure Policy will see it."""
        return {
            "type": "Microsoft.Resources/subscriptions/resourceGroups",
            "name": f"rg-{self.name}",
            "location": self.region,
            "tags": {
                "owner": self.data["owner"],
                "cost-center": self.data["cost_center"],
                "environment": self.data["environment"],
                "team": self.data["team"],
                "vended-by": "guardrails",
                "request": self.file,
            },
        }


def allowed_locations(tfvars: Path = TFVARS) -> list[str]:
    match = re.search(r"^allowed_locations\s*=\s*\[([^\]]*)\]", tfvars.read_text(), re.M)
    if not match:
        raise ValueError(f"no allowed_locations in {tfvars}")
    return re.findall(r'"([^"]+)"', match.group(1))


def load(directory: Path = REQUESTS_DIR) -> tuple[list[Request], list[str]]:
    requests, errors = [], []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            errors.append(f"{path.name}: not valid JSON ({e.msg}, line {e.lineno})")
            continue
        if not isinstance(data, dict):
            errors.append(f"{path.name}: should be a JSON object")
            continue
        requests.append(Request(path.name, data))
    return requests, errors


def check_one(r: Request, regions: list[str]) -> list[str]:
    d, problems = r.data, []
    missing = REQUIRED - set(d)
    if missing:
        return [f"missing {', '.join(sorted(missing))}"]
    unknown = set(d) - FIELDS
    if unknown:
        problems.append(f"unknown fields {', '.join(sorted(unknown))}")

    if not TEAM.match(str(d["team"])):
        problems.append("team: 2 to 21 lowercase letters, digits and hyphens, starting with a letter")
    if d["environment"] not in ENVIRONMENTS:
        problems.append(f"environment: one of {', '.join(ENVIRONMENTS)}")
        return problems
    if r.file != f"{r.name}.json":
        problems.append(f"name the file {r.name}.json, after its team and environment")
    if not EMAIL.match(str(d["owner"])):
        problems.append("owner: an email address, so budget alerts reach someone")
    if not COST_CENTER.match(str(d["cost_center"])):
        problems.append("cost_center: like cc-2040")
    if r.region not in regions:
        problems.append(f"region: {r.region} isn't allowed; use {', '.join(regions)}")

    budget, cap = d["monthly_budget"], BUDGET_CAPS[d["environment"]]
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget <= 0:
        problems.append("monthly_budget: a positive number of dollars")
    elif budget > cap:
        problems.append(f"monthly_budget: {d['environment']} environments can ask for up to {cap}; more needs a platform review")

    access = d.get("access") or {}
    if not isinstance(access, dict) or set(access) - {"contributors", "readers"}:
        problems.append("access: an object with contributors and readers lists")
    else:
        for role in ("contributors", "readers"):
            for group in r.access(role):
                if not GROUP.match(str(group)):
                    problems.append(f"access.{role}: {group!r} isn't a group name like grp-guardrails-payments")
        if d["environment"] == "prod" and r.access("contributors"):
            problems.append(
                "access.contributors: prod doesn't give people Contributor; changes go through pipelines. "
                "List the group under readers."
            )

    if "address_space" in d:
        try:
            net = ipaddress.ip_network(str(d["address_space"]))
        except ValueError:
            problems.append(f"address_space: {d['address_space']!r} isn't a network like 10.100.7.0/24")
        else:
            if net.version != 4 or not net.subnet_of(ADDRESS_POOL):
                problems.append(f"address_space: must be inside {ADDRESS_POOL}")
            elif net.prefixlen not in PREFIXES:
                problems.append("address_space: between /24 and /26")
    return problems


def check_guardrails(r: Request, group: str) -> list[str]:
    decision = decide(group, r.resource_group())
    verbs = {"Deny": "be blocked by", "Audit": "be reported by", "Modify": "be changed by"}
    return [f"the resource group would {verbs.get(res.effect, res.effect)} {res.rule}" for res in decision.results]


def check(directory: Path = REQUESTS_DIR, group: str = "online", tfvars: Path = TFVARS) -> list[str]:
    """Every problem with the requests in a directory, as 'file: problem' lines."""
    requests, errors = load(directory)
    regions = allowed_locations(tfvars)
    valid = []
    for r in requests:
        problems = check_one(r, regions)
        if not problems:
            problems = check_guardrails(r, group)
        errors += [f"{r.file}: {p}" for p in problems]
        if not problems:
            valid.append(r)

    networks: list[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str]] = []
    for r in valid:
        if "address_space" not in r.data:
            continue
        net = ipaddress.ip_network(r.data["address_space"])
        for other, owner in networks:
            if net.overlaps(other):
                errors.append(f"{r.file}: address_space {net} overlaps {owner}'s {other}")
        networks.append((net, r.file))
    return errors


def summary(directory: Path = REQUESTS_DIR) -> str:
    requests, _ = load(directory)
    lines = [
        "| Environment | Owner | Cost centre | Budget / month | Contributors | Readers | Network |",
        "|---|---|---|---|---|---|---|",
    ]
    total = 0.0
    for r in requests:
        d = r.data
        total += float(d.get("monthly_budget", 0))
        lines.append(
            f"| `rg-{r.name}` | {d.get('owner', '')} | {d.get('cost_center', '')} | ${d.get('monthly_budget', 0):,} "
            f"| {', '.join(r.access('contributors')) or '-'} | {', '.join(r.access('readers')) or '-'} "
            f"| {d.get('address_space', 'none')} |"
        )
    lines.append(f"\n{len(requests)} environments, ${total:,.0f} a month in budgets.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check or summarise environment requests.")
    parser.add_argument("command", choices=["check", "summary"])
    parser.add_argument("--dir", type=Path, default=REQUESTS_DIR)
    parser.add_argument("--group", default="online", choices=list(PARENTS), help="management group the subscription is in")
    args = parser.parse_args(argv)
    if args.command == "summary":
        print(summary(args.dir))
        return 0
    errors = check(args.dir, args.group)
    for e in errors:
        print(e)
    if not errors:
        print(f"OK: {len(load(args.dir)[0])} environment requests pass.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
