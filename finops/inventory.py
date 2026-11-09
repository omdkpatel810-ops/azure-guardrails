"""Find money left on the table: idle resources from Azure Resource Graph, and Azure Advisor's
cost recommendations (oversized VMs and databases, unused reservations)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Finding:
    resource_id: str
    name: str
    resource_group: str
    kind: str  # "Idle" or "Advisor"
    problem: str
    fix: str
    monthly_cost: float  # what it costs, or what fixing it saves, per month


IDLE_RULES = {
    "stopped": (
        "Stopped from inside the VM but still allocated, so the VM is billed as if running.",
        "Deallocate it (az vm deallocate), or delete it.",
    ),
    "deallocated": (
        "Deallocated: compute is free, but its disks are still billed.",
        "Delete it, keeping a snapshot if it might come back.",
    ),
    "unattached_disk": ("Not attached to any VM, and still billed.", "Snapshot it if anyone needs it, then delete it."),
    "reserved_disk": ("Belongs to a deallocated VM, and still billed.", "Delete it with its VM, or snapshot it first."),
    "orphan_ip": ("Not attached to anything; static public IPs are billed by the hour.", "Delete it."),
    "empty_plan": ("An App Service plan with no apps, still billed for its instances.", "Delete it, or move an app onto it."),
}


def load_json_rows(path: Path) -> list[dict[str, Any]]:
    """Rows from `az graph query` (which wraps them in {"data": [...]}) or `az advisor` (a list)."""
    value = json.loads(path.read_text())
    if isinstance(value, dict):
        value = value.get("data", [])
    return [row for row in value if isinstance(row, dict)]


def _kind(row: dict[str, Any]) -> str | None:
    rtype = str(row.get("type", "")).lower()
    if rtype == "microsoft.compute/virtualmachines":
        state = str(row.get("powerState") or "").lower()
        if state == "powerstate/stopped":
            return "stopped"
        if state == "powerstate/deallocated":
            return "deallocated"
    elif rtype == "microsoft.compute/disks":
        state = str(row.get("diskState") or "").lower()
        if state == "unattached":
            return "unattached_disk"
        if state == "reserved":
            return "reserved_disk"
    elif rtype == "microsoft.network/publicipaddresses":
        if not row.get("ipConfiguration") and not row.get("natGateway"):
            return "orphan_ip"
    elif rtype == "microsoft.web/serverfarms" and row.get("numberOfSites") == 0:
        return "empty_plan"
    return None


def idle_resources(resources: list[dict[str, Any]], monthly_cost: dict[str, float]) -> list[Finding]:
    """Resources that cost money while doing nothing, most expensive first."""
    findings = []
    for row in resources:
        kind = _kind(row)
        if kind is None:
            continue
        rid = str(row.get("id", "")).lower()
        problem, fix = IDLE_RULES[kind]
        findings.append(
            Finding(
                rid,
                str(row.get("name", "")),
                str(row.get("resourceGroup", "")).lower(),
                "Idle",
                problem,
                fix,
                round(monthly_cost.get(rid, 0.0), 2),
            )
        )
    # Something idle that costs nothing saves nothing: a deallocated VM's cost is in its disks,
    # which are listed on their own.
    return sorted((f for f in findings if f.monthly_cost > 0), key=lambda f: -f.monthly_cost)


def advisor_findings(recommendations: list[dict[str, Any]]) -> list[Finding]:
    """Azure Advisor's cost recommendations, with what each saves a month."""
    findings = []
    for rec in recommendations:
        if str(rec.get("category", "Cost")).lower() != "cost":
            continue
        extended = rec.get("extendedProperties") or {}
        if extended.get("savingsAmount") not in (None, ""):
            saving = float(extended["savingsAmount"])
        else:
            saving = float(extended.get("annualSavingsAmount") or 0) / 12
        source = (rec.get("resourceMetadata") or {}).get("resourceId") or rec.get("id", "")
        rid = str(source).lower().split("/providers/microsoft.advisor")[0]
        short = rec.get("shortDescription") or {}
        group = rid.split("/resourcegroups/")[1].split("/")[0] if "/resourcegroups/" in rid else ""
        findings.append(
            Finding(
                rid,
                str(rec.get("impactedValue") or rid.rsplit("/", 1)[-1]),
                group,
                "Advisor",
                str(short.get("problem", "")),
                str(short.get("solution", "")),
                round(saving, 2),
            )
        )
    return sorted(findings, key=lambda f: -f.monthly_cost)
