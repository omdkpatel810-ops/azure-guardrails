"""Read Cost Management exports into one shape, whichever export format they came from.

Azure's export columns depend on the account type and the dataset: pay-as-you-go accounts get
"PreTaxCost" and "UsageDateTime", Microsoft Customer Agreement accounts get "costInBillingCurrency"
and "date", and FOCUS exports get "BilledCost" and "ChargePeriodStart". Tags come as JSON, or, in
older exports, as JSON without the surrounding braces.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

# Canonical field => the column names Azure uses for it, most specific first.
COLUMNS = {
    "date": ["date", "usagedatetime", "chargeperiodstart", "usagedate"],
    "cost": ["costinbillingcurrency", "pretaxcost", "billedcost", "cost", "effectivecost"],
    "resource_id": ["resourceid", "instanceid"],
    "resource_group": ["resourcegroupname", "resourcegroup", "x_resourcegroupname"],
    "service": ["metercategory", "servicename", "consumedservice"],
    "tags": ["tags"],
}
REQUIRED = ("date", "cost", "resource_id")


@dataclass(frozen=True)
class CostRow:
    day: date
    resource_id: str  # lower case, as Azure compares IDs without case
    resource_group: str
    service: str
    cost: float
    tags: dict[str, str] = field(default_factory=dict, hash=False, compare=False)

    @property
    def name(self) -> str:
        return self.resource_id.rstrip("/").rsplit("/", 1)[-1]


def parse_tags(text: str | None) -> dict[str, str]:
    """Tags as a dict, from '{"a": "b"}', '"a": "b"' (older exports) or nothing."""
    text = (text or "").strip()
    if not text:
        return {}
    if not text.startswith("{"):
        text = "{" + text + "}"
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return {str(k).lower(): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def parse_date(text: str) -> date:
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10] if fmt != "%Y%m%d" else text[:8], fmt).date()
        except ValueError:
            continue
    raise ValueError(f"can't read the date {text!r}")


def _columns(header: Iterable[str]) -> dict[str, str]:
    by_lower = {h.strip().lower().lstrip("﻿"): h for h in header}
    found = {}
    for canonical, names in COLUMNS.items():
        for name in names:
            if name in by_lower:
                found[canonical] = by_lower[name]
                break
    missing = [c for c in REQUIRED if c not in found]
    if missing:
        raise ValueError(f"not a cost export: no {', '.join(missing)} column")
    return found


def _resource_group(resource_id: str) -> str:
    parts = resource_id.split("/")
    for i, part in enumerate(parts[:-1]):
        if part == "resourcegroups":
            return parts[i + 1]
    return ""


def read_export(path: Path) -> list[CostRow]:
    """Every row with a cost, from one export file."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = _columns(reader.fieldnames or [])
        rows = []
        for raw in reader:
            cost = float(raw[cols["cost"]] or 0)
            if cost == 0:
                continue
            resource_id = (raw[cols["resource_id"]] or "").strip().lower()
            group = raw.get(cols.get("resource_group", ""), "") or _resource_group(resource_id)
            rows.append(
                CostRow(
                    day=parse_date(raw[cols["date"]]),
                    resource_id=resource_id,
                    resource_group=group.strip().lower(),
                    service=(raw.get(cols.get("service", ""), "") or "Other").strip(),
                    cost=cost,
                    tags=parse_tags(raw.get(cols.get("tags", ""), "")),
                )
            )
    return rows


def read_exports(paths: Iterable[Path]) -> list[CostRow]:
    """Rows from several files. Month-to-date exports repeat earlier days, so for each day the
    newest file (last in the list) wins."""
    newest: dict[date, list[CostRow]] = {}
    for path in paths:
        by_day: dict[date, list[CostRow]] = {}
        for row in read_export(path):
            by_day.setdefault(row.day, []).append(row)
        newest.update(by_day)
    return [row for day in sorted(newest) for row in newest[day]]
