"""FinOps: reading every export format, finding idle resources, and the report built from them."""

from __future__ import annotations

import json
from datetime import date

import pytest

from finops import sample
from finops.costs import CostRow, parse_date, parse_tags, read_export, read_exports
from finops.inventory import advisor_findings, idle_resources, load_json_rows
from finops.report import NO_OWNER, Environment, build, load_environments, main, to_html, to_json, to_markdown

RG = "/subscriptions/0/resourcegroups/rg-payments-dev/providers"


# --- Reading exports -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "tags"),
    [
        ('{"owner": "a@example.com", "Team": "payments"}', {"owner": "a@example.com", "team": "payments"}),
        ('"owner": "a@example.com","cost-center": "cc-1"', {"owner": "a@example.com", "cost-center": "cc-1"}),
        ("", {}),
        (None, {}),
        ("not json", {}),
        ("[1, 2]", {}),
    ],
)
def test_tags_in_every_shape(text, tags):
    assert parse_tags(text) == tags


@pytest.mark.parametrize("text", ["2026-11-03", "11/03/2026", "2026-11-03T00:00:00Z", "20261103", " 2026-11-03 "])
def test_dates_in_every_shape(text):
    assert parse_date(text) == date(2026, 11, 3)


def test_unreadable_dates():
    with pytest.raises(ValueError, match="can't read the date 'yesterday'"):
        parse_date("yesterday")


def write(path, header, *rows):
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def test_a_customer_agreement_export(tmp_path):
    f = write(
        tmp_path / "mca.csv",
        "﻿date,resourceId,resourceGroupName,meterCategory,costInBillingCurrency,tags",
        f'2026-11-02,{RG}/Microsoft.Compute/virtualMachines/VM-1,rg-payments-dev,Virtual Machines,1.25,"{{""owner"": ""a@x.com""}}"',
        f"2026-11-02,{RG}/Microsoft.Network/virtualNetworks/vnet-1,rg-payments-dev,Virtual Network,0,",
    )
    (row,) = read_export(f)
    assert row == CostRow(
        date(2026, 11, 2), f"{RG}/microsoft.compute/virtualmachines/vm-1", "rg-payments-dev", "Virtual Machines", 1.25
    )
    assert row.tags == {"owner": "a@x.com"} and row.name == "vm-1"


def test_a_pay_as_you_go_export(tmp_path):
    f = write(
        tmp_path / "payg.csv",
        "UsageDateTime,InstanceId,ResourceGroup,MeterCategory,PreTaxCost,Tags",
        f'11/05/2026,{RG}/x/disks/d1,RG-PAYMENTS-DEV,Storage,0.5,"""owner"": ""a@x.com"""',
    )
    (row,) = read_export(f)
    assert (row.day, row.resource_group, row.cost, row.tags) == (date(2026, 11, 5), "rg-payments-dev", 0.5, {"owner": "a@x.com"})


def test_a_focus_export_without_a_group_column(tmp_path):
    f = write(
        tmp_path / "focus.csv",
        "ChargePeriodStart,ResourceId,ServiceName,BilledCost,EffectiveCost,Tags",
        f"2026-11-07T00:00:00Z,{RG}/x/sites/app,App Service,3.5,3.1,",
    )
    (row,) = read_export(f)
    assert (row.cost, row.resource_group, row.service) == (3.5, "rg-payments-dev", "App Service")


def test_rows_without_a_service_or_group(tmp_path):
    (row,) = read_export(write(tmp_path / "bare.csv", "Date,ResourceId,Cost", "2026-11-07,/subscriptions/0/providers/x/y,2"))
    assert (row.service, row.resource_group) == ("Other", "")


def test_files_that_arent_exports(tmp_path):
    with pytest.raises(ValueError, match="not a cost export: no cost, resource_id column"):
        read_export(write(tmp_path / "x.csv", "date,amount", "2026-11-01,3"))


def test_month_to_date_exports_dont_count_days_twice(tmp_path):
    header = "date,resourceId,costInBillingCurrency"
    first = write(tmp_path / "1.csv", header, "2026-11-01,/a,1", "2026-11-02,/a,1")
    second = write(tmp_path / "2.csv", header, "2026-11-01,/a,1", "2026-11-02,/a,2", "2026-11-03,/a,3")
    rows = read_exports([first, second])
    assert [(r.day.day, r.cost) for r in rows] == [(1, 1.0), (2, 2.0), (3, 3.0)]


# --- Idle resources and Advisor ------------------------------------------------------------------------


def graph(rtype, **extra):
    return {"id": f"{RG}/{rtype}/thing", "name": "thing", "type": rtype, "resourceGroup": "RG-PAYMENTS-DEV", **extra}


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        (graph("Microsoft.Compute/virtualMachines", powerState="PowerState/stopped"), "Stopped from inside the VM"),
        (graph("Microsoft.Compute/virtualMachines", powerState="PowerState/deallocated"), "Deallocated"),
        (graph("Microsoft.Compute/disks", diskState="Unattached"), "Not attached to any VM"),
        (graph("Microsoft.Compute/disks", diskState="Reserved"), "Belongs to a deallocated VM"),
        (graph("Microsoft.Network/publicIPAddresses", ipConfiguration=None), "Not attached to anything"),
        (graph("Microsoft.Web/serverfarms", numberOfSites=0), "An App Service plan with no apps"),
    ],
)
def test_each_kind_of_idle_resource(row, problem):
    (finding,) = idle_resources([row], {f"{RG}/{row['type']}/thing".lower(): 30.0})
    assert finding.problem.startswith(problem) and finding.monthly_cost == 30.0
    assert finding.resource_group == "rg-payments-dev" and finding.kind == "Idle"


@pytest.mark.parametrize(
    "row",
    [
        graph("Microsoft.Compute/virtualMachines", powerState="PowerState/running"),
        graph("Microsoft.Compute/disks", diskState="Attached"),
        graph("Microsoft.Network/publicIPAddresses", ipConfiguration={"id": "nic"}),
        graph("Microsoft.Network/publicIPAddresses", natGateway={"id": "nat"}),
        graph("Microsoft.Web/serverfarms", numberOfSites=2),
        graph("Microsoft.Storage/storageAccounts"),
    ],
)
def test_resources_in_use_arent_idle(row):
    assert idle_resources([row], {f"{RG}/{row['type']}/thing".lower(): 30.0}) == []


def test_idle_resources_that_cost_nothing_arent_listed():
    row = graph("Microsoft.Compute/virtualMachines", powerState="PowerState/deallocated")
    assert idle_resources([row], {}) == []


def test_advisor_recommendations():
    recs = [
        {
            "category": "Cost",
            "impactedValue": "sqldb",
            "resourceMetadata": {"resourceId": f"{RG}/Microsoft.Sql/servers/s/databases/sqldb"},
            "shortDescription": {"problem": "Too big", "solution": "Shrink it"},
            "extendedProperties": {"annualSavingsAmount": "1200"},
        },
        {
            "category": "Cost",
            "id": f"{RG}/Microsoft.Compute/virtualMachines/VM-9/providers/Microsoft.Advisor/recommendations/abc",
            "shortDescription": {},
            "extendedProperties": {"savingsAmount": "15.5"},
        },
        {"category": "Cost", "id": "/subscriptions/0/providers/Microsoft.Advisor/recommendations/r", "extendedProperties": {}},
        {"category": "Security", "id": "x"},
    ]
    found = advisor_findings(recs)
    assert [(f.name, f.monthly_cost, f.resource_group) for f in found] == [
        ("sqldb", 100.0, "rg-payments-dev"),
        ("vm-9", 15.5, "rg-payments-dev"),
        ("0", 0.0, ""),
    ]
    assert found[0].problem == "Too big" and found[0].fix == "Shrink it" and found[0].kind == "Advisor"


def test_reading_graph_and_advisor_output(tmp_path):
    (tmp_path / "graph.json").write_text(json.dumps({"count": 1, "data": [{"id": "a"}, "junk"]}))
    (tmp_path / "advisor.json").write_text(json.dumps([{"id": "b"}]))
    assert load_json_rows(tmp_path / "graph.json") == [{"id": "a"}]
    assert load_json_rows(tmp_path / "advisor.json") == [{"id": "b"}]


# --- The report on the sample month -------------------------------------------------------------------


@pytest.fixture(scope="module")
def sample_report(tmp_path_factory):
    paths = sample.write(tmp_path_factory.mktemp("sample"))
    return build(read_exports([paths["costs"]]), load_json_rows(paths["resources"]), load_json_rows(paths["advisor"]))


def test_the_sample_is_the_same_every_time(tmp_path):
    a, b = sample.write(tmp_path / "a"), sample.write(tmp_path / "b")
    assert all(a[k].read_text() == b[k].read_text() for k in a)
    assert len(sample.RESOURCES) == 60


def test_the_sample_month(sample_report):
    r = sample_report
    assert (r.month, r.days, r.days_in_month, r.as_of) == ("November 2026", 20, 30, date(2026, 11, 20))
    assert r.forecast == pytest.approx(r.total * 1.5, abs=0.01)
    assert 0.8 < r.owned < 0.85  # the forgotten proof of concept has no tags


def test_spend_by_team(sample_report):
    teams = [t for t, _ in sample_report.by_team]
    assert teams[0] == "data" and teams[-1] == NO_OWNER  # no owner is always listed last
    assert set(teams) == {"data", "storefront", "payments", "support", "marketing", "platform", NO_OWNER}


def test_budgets(sample_report):
    status = {e.name: e.status for e in sample_report.environments}
    assert status["rg-data-dev"] == "Over budget"  # the oversized database
    assert status["rg-storefront-dev"] == "Watch"
    assert list(status.values()).count("On track") == 6  # including OneView, with no spend yet
    assert sample_report.environments[0].name == "rg-data-dev"  # trouble first


def test_unowned_spend_names_the_resources(sample_report):
    assert {g for _, g, _ in sample_report.no_owner} == {"rg-legacy-poc"}
    assert sample_report.no_owner[0][0] in {"vm-poc-01", "vm-poc-02"}


def test_savings(sample_report):
    idle = {f.name for f in sample_report.idle}
    assert idle == {
        "vm-poc-02",
        "disk-poc-data-old",
        "disk-poc-backup",
        "vm-old-build-osdisk",
        "pip-poc-old",
        "pip-poc-test",
        "asp-marketing-dev",
    }
    assert sample_report.advisor[0].name == "sqldb-analytics" and sample_report.advisor[0].monthly_cost == 321.0
    assert sample_report.savings == pytest.approx(sum(f.monthly_cost for f in sample_report.idle) + 420.0)


def test_environment_status_thresholds():
    assert Environment("a", "t", 100, 10, 79).status == "On track"
    assert Environment("a", "t", 100, 10, 81).status == "Watch"
    assert Environment("a", "t", 100, 10, 101).status == "Over budget"
    assert Environment("a", "t", 100, 101, 101).status == "Over budget"


# --- Attribution ------------------------------------------------------------------------------------------


def row(cost=1.0, group="rg-x", day=date(2026, 11, 2), **tags):
    return CostRow(day, f"/subscriptions/0/resourcegroups/{group}/providers/x/y/r{cost}", group, "Other", cost, tags)


ENVS = [{"team": "payments", "environment": "dev", "cost_center": "cc-2040", "monthly_budget": 100}]


def test_spend_is_attributed_by_tag_then_cost_centre_then_resource_group():
    r = build(
        [
            row(1, team="checkout", owner="o"),
            row(2, **{"cost-center": "cc-2040", "owner": "o"}),
            row(4, group="rg-payments-dev"),
            row(8, **{"cost-center": "cc-9999", "owner": "o"}),
            row(16, owner="someone@example.com"),
            row(32),
        ],
        environments=ENVS,
    )
    assert dict(r.by_team) == {"checkout": 1, "payments": 6, "cc-9999": 8, "someone@example.com": 16, NO_OWNER: 32}
    assert r.owned == pytest.approx(27 / 63)


def test_only_the_latest_month_counts():
    r = build([row(5, day=date(2026, 10, 31), owner="o"), row(1, day=date(2026, 11, 1), owner="o")], environments=[])
    assert (r.total, r.days, r.forecast) == (1, 1, 30)


def test_no_costs_means_no_report():
    with pytest.raises(ValueError, match="no costs"):
        build([], environments=[])


def test_environments_default_to_the_repository():
    assert len(load_environments()) == 8
    assert load_environments(load_environments.__defaults__[0] / "missing") == []


# --- Output ---------------------------------------------------------------------------------------------


def test_markdown_lists_only_environments_in_trouble(sample_report):
    md = to_markdown(sample_report)
    assert md.startswith("## FinOps: November 2026, day 20 of 30")
    assert "| rg-data-dev | $300 |" in md and "| Over budget |" in md
    assert "rg-payments-prod" not in md
    assert "**Spend with an owner:** 8" in md


def test_markdown_without_trouble():
    r = build([row(1, group="rg-payments-dev", owner="o")], environments=ENVS)
    assert "| Environment |" not in to_markdown(r)


def test_json(sample_report):
    data = json.loads(to_json(sample_report))
    assert data["as_of"] == "2026-11-20" and data["savings"] == sample_report.savings
    assert data["environments"][0]["status"] == "Over budget"


def test_html(sample_report):
    page = to_html(sample_report, note="Sample data.")
    assert 'class="finops"' in page and "prefers-color-scheme: dark" in page
    assert "Over budget" in page and "Fix: " in page and "Sample data." in page
    assert page.count('class="row"') == len(sample_report.by_team)


def test_html_escapes_names():
    r = build([row(1, team="<script>x</script>", owner="o")], environments=[])
    page = to_html(r)
    assert "<script>x" not in page and "&lt;script&gt;" in page


def test_html_for_an_empty_month():
    r = build([row(1, owner="o")], environments=[])
    r.by_team = []
    assert "No spend yet." in to_html(r)


def test_the_command_line(tmp_path, capsys):
    paths = sample.write(tmp_path / "in")
    out = tmp_path / "out"
    out.mkdir()
    code = main(
        [
            "--costs",
            str(paths["costs"]),
            "--resources",
            str(paths["resources"]),
            "--advisor",
            str(paths["advisor"]),
            "--html",
            str(out / "r.html"),
            "--json",
            str(out / "r.json"),
            "--markdown",
            str(out / "r.md"),
        ]
    )
    assert code == 0
    assert (out / "r.html").read_text().startswith("<!doctype html>")
    assert json.loads((out / "r.json").read_text())["month"] == "November 2026"
    assert (out / "r.md").read_text() in capsys.readouterr().out
    assert main(["--costs", str(paths["costs"])]) == 0
