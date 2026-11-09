"""A month of made-up but realistic data for the FinOps report: the five pretend teams'
environments from environments/, the platform's own resources, and the mess Guardrails exists to
catch. Written in the same shapes Azure produces, so the report reads it exactly as it reads real
exports.

    python -m finops.sample out/      # writes costs.csv, resources.json and advisor.json
"""

from __future__ import annotations

import csv
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

SUB = "/subscriptions/00000000-0000-0000-0000-000000000000"
MONTH_START = date(2026, 11, 1)
DAYS = 20  # the export is month-to-date, as of the 20th

TEAMS = {
    "payments": ("payments-lead@example.com", "cc-2040"),
    "storefront": ("storefront-lead@example.com", "cc-3110"),
    "data": ("data-lead@example.com", "cc-4500"),
    "support": ("support-lead@example.com", "cc-5200"),
    "marketing": ("marketing-lead@example.com", "cc-6100"),
}

# (resource group, team or None, environment, type, name, cost a day, extra Resource Graph fields)
RESOURCES: list[tuple[str, str | None, str, str, str, float, dict]] = [
    # payments
    (
        "rg-payments-dev",
        "payments",
        "dev",
        "Microsoft.Compute/virtualMachines",
        "vm-payments-api-dev",
        1.00,
        {"powerState": "PowerState/running"},
    ),
    (
        "rg-payments-dev",
        "payments",
        "dev",
        "Microsoft.Compute/disks",
        "vm-payments-api-dev-osdisk",
        0.16,
        {"diskState": "Attached"},
    ),
    ("rg-payments-dev", "payments", "dev", "Microsoft.Network/networkInterfaces", "nic-payments-api-dev", 0, {}),
    ("rg-payments-dev", "payments", "dev", "Microsoft.Storage/storageAccounts", "stpaymentsdev", 0.05, {}),
    ("rg-payments-dev", "payments", "dev", "Microsoft.KeyVault/vaults", "kv-payments-dev", 0.01, {}),
    ("rg-payments-dev", "payments", "dev", "Microsoft.Network/virtualNetworks", "vnet-payments-dev", 0, {}),
    ("rg-payments-dev", "payments", "dev", "Microsoft.Network/networkSecurityGroups", "nsg-payments-dev-workloads", 0, {}),
    ("rg-payments-prod", "payments", "prod", "Microsoft.Web/serverfarms", "asp-payments-prod", 4.30, {"numberOfSites": 2}),
    ("rg-payments-prod", "payments", "prod", "Microsoft.Web/sites", "app-payments-api", 0, {}),
    ("rg-payments-prod", "payments", "prod", "Microsoft.Web/sites", "app-payments-worker", 0, {}),
    ("rg-payments-prod", "payments", "prod", "Microsoft.Sql/servers/databases", "sqldb-payments", 2.43, {}),
    ("rg-payments-prod", "payments", "prod", "Microsoft.Storage/storageAccounts", "stpaymentsprod", 0.30, {}),
    ("rg-payments-prod", "payments", "prod", "Microsoft.Insights/components", "appi-payments-prod", 0.50, {}),
    ("rg-payments-prod", "payments", "prod", "Microsoft.Network/virtualNetworks", "vnet-payments-prod", 0, {}),
    ("rg-payments-prod", "payments", "prod", "Microsoft.Network/networkSecurityGroups", "nsg-payments-prod-workloads", 0, {}),
    # storefront
    (
        "rg-storefront-dev",
        "storefront",
        "dev",
        "Microsoft.Compute/virtualMachines",
        "vm-storefront-web-dev",
        2.30,
        {"powerState": "PowerState/running"},
    ),
    (
        "rg-storefront-dev",
        "storefront",
        "dev",
        "Microsoft.Compute/virtualMachines",
        "vm-storefront-search-dev",
        2.50,
        {"powerState": "PowerState/running"},
    ),
    (
        "rg-storefront-dev",
        "storefront",
        "dev",
        "Microsoft.Compute/disks",
        "vm-storefront-web-dev-osdisk",
        0.33,
        {"diskState": "Attached"},
    ),
    (
        "rg-storefront-dev",
        "storefront",
        "dev",
        "Microsoft.Compute/disks",
        "vm-storefront-search-dev-osdisk",
        0.33,
        {"diskState": "Attached"},
    ),
    ("rg-storefront-dev", "storefront", "dev", "Microsoft.Network/networkInterfaces", "nic-storefront-web-dev", 0, {}),
    ("rg-storefront-dev", "storefront", "dev", "Microsoft.Network/networkInterfaces", "nic-storefront-search-dev", 0, {}),
    ("rg-storefront-dev", "storefront", "dev", "Microsoft.Network/virtualNetworks", "vnet-storefront-dev", 0, {}),
    ("rg-storefront-dev", "storefront", "dev", "Microsoft.Network/networkSecurityGroups", "nsg-storefront-dev-workloads", 0, {}),
    ("rg-storefront-prod", "storefront", "prod", "Microsoft.ContainerService/managedClusters", "aks-storefront-prod", 6.90, {}),
    ("rg-storefront-prod", "storefront", "prod", "Microsoft.Network/loadBalancers", "kubernetes", 0.60, {}),
    (
        "rg-storefront-prod",
        "storefront",
        "prod",
        "Microsoft.Network/publicIPAddresses",
        "pip-storefront-ingress",
        0.12,
        {"ipConfiguration": {"id": "lb"}},
    ),
    ("rg-storefront-prod", "storefront", "prod", "Microsoft.Storage/storageAccounts", "ststorefrontprod", 0.40, {}),
    ("rg-storefront-prod", "storefront", "prod", "Microsoft.ContainerRegistry/registries", "crstorefront", 0.17, {}),
    ("rg-storefront-prod", "storefront", "prod", "Microsoft.Network/virtualNetworks", "vnet-storefront-prod", 0, {}),
    (
        "rg-storefront-prod",
        "storefront",
        "prod",
        "Microsoft.Network/networkSecurityGroups",
        "nsg-storefront-prod-workloads",
        0,
        {},
    ),
    # data: an oversized database pushes it over budget
    ("rg-data-dev", "data", "dev", "Microsoft.Sql/servers/databases", "sqldb-analytics", 15.50, {}),
    ("rg-data-dev", "data", "dev", "Microsoft.Sql/servers", "sql-data-dev", 0, {}),
    ("rg-data-dev", "data", "dev", "Microsoft.Storage/storageAccounts", "stdatalakedev", 0.60, {}),
    ("rg-data-dev", "data", "dev", "Microsoft.DataFactory/factories", "adf-data-dev", 0.35, {}),
    # support
    (
        "rg-support-test",
        "support",
        "test",
        "Microsoft.Compute/virtualMachines",
        "vm-support-tools-test",
        0.31,
        {"powerState": "PowerState/running"},
    ),
    (
        "rg-support-test",
        "support",
        "test",
        "Microsoft.Compute/disks",
        "vm-support-tools-test-osdisk",
        0.16,
        {"diskState": "Attached"},
    ),
    ("rg-support-test", "support", "test", "Microsoft.Network/networkInterfaces", "nic-support-tools-test", 0, {}),
    ("rg-support-test", "support", "test", "Microsoft.Network/virtualNetworks", "vnet-support-test", 0, {}),
    ("rg-support-test", "support", "test", "Microsoft.Network/networkSecurityGroups", "nsg-support-test-workloads", 0, {}),
    # marketing: a plan with nothing on it
    ("rg-marketing-dev", "marketing", "dev", "Microsoft.Web/serverfarms", "asp-marketing-dev", 0.43, {"numberOfSites": 0}),
    ("rg-marketing-dev", "marketing", "dev", "Microsoft.Web/staticSites", "stapp-marketing-site", 0, {}),
    ("rg-marketing-dev", "marketing", "dev", "Microsoft.Storage/storageAccounts", "stmarketingassets", 0.02, {}),
    # the platform
    ("rg-guardrails-tfstate", "platform", "prod", "Microsoft.Storage/storageAccounts", "stgrstate1a2b3c4d5e", 0.02, {}),
    ("rg-guardrails-tfstate", "platform", "prod", "Microsoft.OperationalInsights/workspaces", "log-guardrails-audit", 0.03, {}),
    ("rg-guardrails-finops", "platform", "prod", "Microsoft.Storage/storageAccounts", "stgrcost6f7a8b9c0d", 0.01, {}),
    # the mess: a forgotten proof of concept with no tags at all
    ("rg-legacy-poc", None, "", "Microsoft.Compute/virtualMachines", "vm-poc-01", 2.30, {"powerState": "PowerState/running"}),
    ("rg-legacy-poc", None, "", "Microsoft.Compute/virtualMachines", "vm-poc-02", 2.30, {"powerState": "PowerState/stopped"}),
    ("rg-legacy-poc", None, "", "Microsoft.Compute/disks", "vm-poc-01-osdisk", 0.65, {"diskState": "Attached"}),
    ("rg-legacy-poc", None, "", "Microsoft.Compute/disks", "vm-poc-02-osdisk", 0.65, {"diskState": "Attached"}),
    ("rg-legacy-poc", None, "", "Microsoft.Compute/disks", "disk-poc-data-old", 0.65, {"diskState": "Unattached"}),
    ("rg-legacy-poc", None, "", "Microsoft.Compute/disks", "disk-poc-backup", 0.65, {"diskState": "Unattached"}),
    ("rg-legacy-poc", None, "", "Microsoft.Network/networkInterfaces", "nic-poc-01", 0, {}),
    ("rg-legacy-poc", None, "", "Microsoft.Network/networkInterfaces", "nic-poc-02", 0, {}),
    ("rg-legacy-poc", None, "", "Microsoft.Network/publicIPAddresses", "pip-poc-01", 0.12, {"ipConfiguration": {"id": "nic"}}),
    ("rg-legacy-poc", None, "", "Microsoft.Network/publicIPAddresses", "pip-poc-old", 0.12, {}),
    ("rg-legacy-poc", None, "", "Microsoft.Network/publicIPAddresses", "pip-poc-test", 0.12, {}),
    ("rg-legacy-poc", None, "", "Microsoft.Storage/storageAccounts", "stpocexports", 0.20, {}),
    ("rg-legacy-poc", None, "", "Microsoft.Compute/virtualMachines", "vm-old-build", 0, {"powerState": "PowerState/deallocated"}),
    ("rg-legacy-poc", None, "", "Microsoft.Compute/disks", "vm-old-build-osdisk", 0.65, {"diskState": "Reserved"}),
    ("rg-legacy-poc", None, "", "Microsoft.Network/virtualNetworks", "vnet-poc", 0, {}),
]

ADVISOR = [
    (
        "rg-data-dev",
        "Microsoft.Sql/servers/databases",
        "sqldb-analytics",
        321.0,
        "The database uses under 5% of its Premium P1 compute.",
        "Move it to Standard S2, or to serverless.",
    ),
    (
        "rg-legacy-poc",
        "Microsoft.Compute/virtualMachines",
        "vm-poc-01",
        69.0,
        "The VM's CPU stayed under 3% for 14 days.",
        "Shut it down, or resize it to a B-series VM.",
    ),
    (
        "rg-storefront-dev",
        "Microsoft.Compute/virtualMachines",
        "vm-storefront-search-dev",
        30.0,
        "The VM's CPU stayed under 10% for 14 days.",
        "Resize it from B2ms to B2s.",
    ),
]


def resource_id(group: str, rtype: str, name: str) -> str:
    if rtype == "Microsoft.Sql/servers/databases":
        return f"{SUB}/resourceGroups/{group}/providers/Microsoft.Sql/servers/sql-{group[3:]}/databases/{name}"
    return f"{SUB}/resourceGroups/{group}/providers/{rtype}/{name}"


def tags(team: str | None, environment: str) -> dict[str, str]:
    if team is None:
        return {}
    if team == "platform":
        return {"owner": "platform-team", "cost-center": "platform", "environment": environment}
    owner, cost_center = TEAMS[team]
    return {"owner": owner, "cost-center": cost_center, "environment": environment, "team": team}


def write(out: Path, seed: int = 2026) -> dict[str, Path]:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"costs": out / "costs.csv", "resources": out / "resources.json", "advisor": out / "advisor.json"}

    with paths["costs"].open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "resourceId", "resourceGroupName", "meterCategory", "costInBillingCurrency", "tags"])
        for day in range(DAYS):
            when = (MONTH_START + timedelta(days=day)).isoformat()
            for group, team, env, rtype, name, cost, _ in RESOURCES:
                if cost == 0:
                    continue
                daily = round(cost * rng.uniform(0.92, 1.08), 4)
                service = rtype.split("/")[0].removeprefix("Microsoft.")
                w.writerow(
                    [when, resource_id(group, rtype, name), group, service, daily, json.dumps(tags(team, env)) if team else ""]
                )

    graph = [
        {
            "id": resource_id(g, t, n),
            "name": n,
            "type": t.lower(),
            "location": "canadacentral",
            "resourceGroup": g,
            "tags": tags(team, env) or None,
            **extra,
        }
        for g, team, env, t, n, _, extra in RESOURCES
    ]
    paths["resources"].write_text(json.dumps({"count": len(graph), "data": graph}, indent=1))

    advisor = [
        {
            "category": "Cost",
            "impact": "High" if saving > 100 else "Medium",
            "impactedValue": name,
            "resourceMetadata": {"resourceId": resource_id(group, rtype, name)},
            "shortDescription": {"problem": problem, "solution": solution},
            "extendedProperties": {"annualSavingsAmount": str(saving * 12), "savingsCurrency": "CAD"},
        }
        for group, rtype, name, saving, problem, solution in ADVISOR
    ]
    paths["advisor"].write_text(json.dumps(advisor, indent=1))
    return paths


if __name__ == "__main__":
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "build/finops-sample")
    written = write(folder)
    print(f"{len(RESOURCES)} resources, {DAYS} days: " + ", ".join(str(p) for p in written.values()))
