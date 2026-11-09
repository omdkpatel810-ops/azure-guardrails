"""The FinOps report: who spends what, how much spend has an owner, which budgets are in trouble,
and what's costing money for nothing.

    python -m finops.report --costs export.csv [--resources resources.json] [--advisor advisor.json]
                            [--html report.html] [--json report.json] [--markdown summary.md]

Spend is attributed to a team by its team tag; failing that by its cost centre or resource group,
matched against the environment requests in environments/. Anything left over is "No owner".
"""

from __future__ import annotations

import argparse
import calendar
import html
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from finops.costs import CostRow, read_exports
from finops.inventory import Finding, advisor_findings, idle_resources, load_json_rows

ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENTS = ROOT / "environments"
NO_OWNER = "No owner"
WATCH, OVER = 0.8, 1.0  # forecast share of budget where an environment needs watching, or is over


@dataclass
class Environment:
    name: str
    team: str
    budget: float
    spent: float
    forecast: float

    @property
    def status(self) -> str:
        if self.spent > self.budget or self.forecast > self.budget * OVER:
            return "Over budget"
        if self.forecast > self.budget * WATCH:
            return "Watch"
        return "On track"


@dataclass
class Report:
    month: str
    as_of: date
    days: int
    days_in_month: int
    total: float
    forecast: float
    owned: float  # share of spend on resources with an owner tag, 0 to 1
    by_team: list[tuple[str, float]]
    no_owner: list[tuple[str, str, float]]  # resource, resource group, spend
    environments: list[Environment]
    idle: list[Finding] = field(default_factory=list)
    advisor: list[Finding] = field(default_factory=list)

    @property
    def savings(self) -> float:
        return round(sum(f.monthly_cost for f in self.idle + self.advisor), 2)


def load_environments(directory: Path = ENVIRONMENTS) -> list[dict]:
    if not directory.is_dir():
        return []
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]


def _team(row: CostRow, by_cost_center: dict[str, str], by_group: dict[str, str]) -> str:
    if row.tags.get("team"):
        return row.tags["team"]
    if row.tags.get("cost-center") in by_cost_center:
        return by_cost_center[row.tags["cost-center"]]
    if row.resource_group in by_group:
        return by_group[row.resource_group]
    if row.tags.get("owner"):
        return row.tags.get("cost-center") or row.tags["owner"]
    return NO_OWNER


def build(
    rows: list[CostRow],
    resources: list[dict] | None = None,
    advisor: list[dict] | None = None,
    environments: list[dict] | None = None,
) -> Report:
    if not rows:
        raise ValueError("the export has no costs")
    environments = load_environments() if environments is None else environments
    as_of = max(r.day for r in rows)
    month_rows = [r for r in rows if (r.day.year, r.day.month) == (as_of.year, as_of.month)]
    days = as_of.day
    days_in_month = calendar.monthrange(as_of.year, as_of.month)[1]
    scale = days_in_month / days

    by_cost_center = {e["cost_center"]: e["team"] for e in environments}
    by_group = {f"rg-{e['team']}-{e['environment']}": e["team"] for e in environments}

    total = sum(r.cost for r in month_rows)
    teams: dict[str, float] = {}
    owned = 0.0
    unowned: dict[tuple[str, str], float] = {}
    per_resource: dict[str, float] = {}
    per_group: dict[str, float] = {}
    for r in month_rows:
        team = _team(r, by_cost_center, by_group)
        teams[team] = teams.get(team, 0) + r.cost
        per_resource[r.resource_id] = per_resource.get(r.resource_id, 0) + r.cost
        per_group[r.resource_group] = per_group.get(r.resource_group, 0) + r.cost
        if r.tags.get("owner"):
            owned += r.cost
        else:
            key = (r.name, r.resource_group)
            unowned[key] = unowned.get(key, 0) + r.cost

    envs = [
        Environment(
            name=f"rg-{e['team']}-{e['environment']}",
            team=e["team"],
            budget=float(e["monthly_budget"]),
            spent=round(per_group.get(f"rg-{e['team']}-{e['environment']}", 0), 2),
            forecast=round(per_group.get(f"rg-{e['team']}-{e['environment']}", 0) * scale, 2),
        )
        for e in environments
    ]
    order = {"Over budget": 0, "Watch": 1, "On track": 2}
    monthly = {rid: cost * scale for rid, cost in per_resource.items()}
    return Report(
        month=as_of.strftime("%B %Y"),
        as_of=as_of,
        days=days,
        days_in_month=days_in_month,
        total=round(total, 2),
        forecast=round(total * scale, 2),
        owned=owned / total if total else 1.0,
        by_team=sorted(((t, round(c, 2)) for t, c in teams.items()), key=lambda tc: (tc[0] == NO_OWNER, -tc[1])),
        no_owner=sorted(((n, g, round(c, 2)) for (n, g), c in unowned.items()), key=lambda x: -x[2]),
        environments=sorted(envs, key=lambda e: (order[e.status], -e.forecast / e.budget)),
        idle=idle_resources(resources or [], monthly),
        advisor=advisor_findings(advisor or []),
    )


# --- Output ----------------------------------------------------------------------------------------


def money(value: float) -> str:
    return f"${value:,.0f}" if abs(value) >= 100 else f"${value:,.2f}"


def to_json(report: Report) -> str:
    data = asdict(report)
    data["as_of"] = report.as_of.isoformat()
    data["savings"] = report.savings
    for env, e in zip(data["environments"], report.environments, strict=True):
        env["status"] = e.status
    return json.dumps(data, indent=2)


def to_markdown(report: Report) -> str:
    lines = [
        f"## FinOps: {report.month}, day {report.days} of {report.days_in_month}",
        "",
        f"- **Spent so far:** {money(report.total)}, heading for {money(report.forecast)} this month",
        f"- **Spend with an owner:** {report.owned:.0%} (target 100%)",
        f"- **Savings found:** {money(report.savings)} a month, from {len(report.idle)} idle resources "
        f"and {len(report.advisor)} Advisor recommendations",
        "",
        "| Team | Spent |",
        "|---|---|",
        *[f"| {t} | {money(c)} |" for t, c in report.by_team],
    ]
    trouble = [e for e in report.environments if e.status != "On track"]
    if trouble:
        lines += ["", "| Environment | Budget | Forecast | Status |", "|---|---|---|---|"]
        lines += [f"| {e.name} | {money(e.budget)} | {money(e.forecast)} | {e.status} |" for e in trouble]
    return "\n".join(lines) + "\n"


STYLE = """
.finops { color-scheme: light; --surface: #fcfcfb; --raised: #ffffff; --line: #e4e3df; --text: #0b0b0b;
  --text-2: #52514e; --muted: #75746f; --bar: #2a78d6; --track: #eceae5;
  --good: #0ca30c; --warning: #b77d00; --critical: #d03b3b;
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--text); background: var(--surface);
  padding: 20px; border-radius: 12px; }
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .finops { color-scheme: dark; --surface: #1a1a19; --raised: #222220;
    --line: #383835; --text: #f0efec; --text-2: #c3c2b7; --muted: #9a998f; --bar: #3987e5; --track: #2c2c2a;
    --warning: #fab219; }
}
:root[data-theme="dark"] .finops, .dark .finops { color-scheme: dark; --surface: #1a1a19; --raised: #222220;
  --line: #383835; --text: #f0efec; --text-2: #c3c2b7; --muted: #9a998f; --bar: #3987e5; --track: #2c2c2a;
  --warning: #fab219; }
.finops h2 { font-size: 1.25rem; margin: 0 0 2px; }
.finops h3 { font-size: 1rem; margin: 28px 0 10px; }
.finops .sub { color: var(--text-2); margin: 0 0 18px; }
.finops .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.finops .tile { background: var(--raised); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
.finops .tile b { display: block; font-size: 1.5rem; font-variant-numeric: tabular-nums; }
.finops .tile span { color: var(--text-2); font-size: 0.85rem; }
.finops .bars { display: grid; gap: 8px; max-width: 640px; }
.finops .row { display: grid; grid-template-columns: minmax(70px, 110px) 1fr; gap: 10px; align-items: center; }
.finops .row .label { text-align: right; overflow-wrap: anywhere; }
.finops .row .track { display: flex; align-items: center; gap: 8px; min-width: 0; }
.finops .row .bar { display: block; height: 16px; background: var(--bar); border-radius: 0 4px 4px 0;
  max-width: calc(100% - 64px); }
.finops .row:hover .bar { opacity: 0.8; }
.finops .row .value { color: var(--text-2); font-variant-numeric: tabular-nums; white-space: nowrap; }
.finops code { white-space: nowrap; }
.finops .by { display: block; color: var(--muted); font-size: 0.8rem; }
.finops .fix { display: block; color: var(--text-2); margin-top: 2px; }
.finops table { width: 100%; border-collapse: collapse; font-size: 0.9rem; background: transparent; }
.finops table, .finops tr, .finops th, .finops td { border: 0 !important; background: transparent; }
.finops th { text-align: left; color: var(--text-2); font-weight: 600; border-bottom: 1px solid var(--line) !important;
  padding: 6px 8px; }
.finops td { border-bottom: 1px solid var(--line) !important; padding: 6px 8px; vertical-align: top; }
.finops td.num, .finops th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.finops .status { white-space: nowrap; font-weight: 600; }
.finops .status::before { display: inline-block; width: 1.1em; }
.finops .status.good::before { content: "✓"; color: var(--good); }
.finops .status.warning::before { content: "!"; color: var(--warning); }
.finops .status.critical::before { content: "✕"; color: var(--critical); }
.finops .note { color: var(--muted); font-size: 0.85rem; margin-top: 18px; }
.finops .scroll { overflow-x: auto; }
"""

STATUS_CLASS = {"On track": "good", "Watch": "warning", "Over budget": "critical"}


def _bars(rows: list[tuple[str, float]]) -> str:
    """Horizontal bars, one series: a label, a bar from zero, and its value at the end. Plain HTML,
    so the text stays readable at any width."""
    if not rows:
        return "<p>No spend yet.</p>"
    top = max(c for _, c in rows) or 1
    out = ['<div class="bars" role="list" aria-label="Spend by team">']
    for team, cost in rows:
        name = html.escape(team)
        width = max(cost / top * 100, 0.6)
        out.append(
            f'<div class="row" role="listitem" title="{name}: {money(cost)}"><span class="label">{name}</span>'
            f'<span class="track"><span class="bar" style="width:{width:.1f}%"></span>'
            f'<span class="value">{money(cost)}</span></span></div>'
        )
    out.append("</div>")
    return "".join(out)


def _table(headers: list[str], rows: list[list[str]], numeric: set[int]) -> str:
    head = "".join(f'<th class="num">{h}</th>' if i in numeric else f"<th>{h}</th>" for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f'<td class="num">{c}</td>' if i in numeric else f"<td>{c}</td>" for i, c in enumerate(row)) + "</tr>"
        for row in rows
    )
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def to_html(report: Report, title: str = "FinOps report", note: str = "") -> str:
    e = html.escape
    tiles = [
        (money(report.total), f"spent in {report.month} so far"),
        (money(report.forecast), "forecast for the month"),
        (f"{report.owned:.0%}", "of spend has an owner (target 100%)"),
        (money(report.savings), "a month in savings found"),
    ]
    out = [
        f'<div class="finops"><style>{STYLE}</style>',
        f"<h2>{e(title)}</h2>",
        f'<p class="sub">{e(report.month)}, day {report.days} of {report.days_in_month}. '
        "Forecasts assume the rest of the month spends like the days so far.</p>",
        '<div class="tiles">' + "".join(f'<div class="tile"><b>{v}</b><span>{e(t)}</span></div>' for v, t in tiles) + "</div>",
        "<h3>Spend by team this month</h3>",
        _bars(report.by_team),
    ]
    if report.environments:
        rows = [
            [
                f"<code>{e(env.name)}</code>",
                money(env.budget),
                money(env.spent),
                money(env.forecast),
                f'<span class="status {STATUS_CLASS[env.status]}">{env.status}</span>',
            ]
            for env in report.environments
        ]
        out += ["<h3>Budgets</h3>", _table(["Environment", "Budget", "Spent", "Forecast", "Status"], rows, {1, 2, 3})]
    if report.no_owner:
        rows = [[e(n), e(g), money(c)] for n, g, c in report.no_owner[:15]]
        out += [
            f"<h3>Spend with no owner tag: {money(sum(c for *_, c in report.no_owner))}</h3>",
            _table(["Resource", "Resource group", "Spent"], rows, {2}),
        ]
    findings = report.idle + report.advisor
    if findings:
        rows = [
            [
                f'{e(f.name)}<span class="by">{"Idle resource" if f.kind == "Idle" else "Azure Advisor"}</span>',
                f'{e(f.problem)}<span class="fix">Fix: {e(f.fix)}</span>',
                money(f.monthly_cost),
            ]
            for f in findings
        ]
        out += [
            f"<h3>Savings found: {money(report.savings)} a month</h3>",
            _table(["Resource", "Problem and fix", "A month"], rows, {2}),
        ]
    if note:
        out.append(f'<p class="note">{e(note)}</p>')
    out.append("</div>")
    return "\n".join(out)


def page(body: str, title: str) -> str:
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
        f'content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>'
        "<style>body{margin:0;padding:16px;background:#fcfcfb}@media (prefers-color-scheme: dark)"
        "{body{background:#1a1a19}}</style></head>"
        f"<body>{body}</body></html>"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the FinOps report from Cost Management exports.")
    parser.add_argument("--costs", type=Path, nargs="+", required=True, help="cost export CSVs, oldest first")
    parser.add_argument("--resources", type=Path, help="az graph query output for finops/queries/resources.kql")
    parser.add_argument("--advisor", type=Path, help="az advisor recommendation list --category Cost output")
    parser.add_argument("--environments", type=Path, default=ENVIRONMENTS)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)

    report = build(
        read_exports(args.costs),
        load_json_rows(args.resources) if args.resources else None,
        load_json_rows(args.advisor) if args.advisor else None,
        load_environments(args.environments),
    )
    if args.html:
        args.html.write_text(page(to_html(report), f"FinOps report, {report.month}"))
    if args.json:
        args.json.write_text(to_json(report))
    markdown = to_markdown(report)
    if args.markdown:
        args.markdown.write_text(markdown)
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
