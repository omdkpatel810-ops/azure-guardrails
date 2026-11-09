"""Environment requests: the real ones pass, and every kind of mistake is caught with a clear message."""

from __future__ import annotations

import json

import pytest

from tools.vending import allowed_locations, check, load, main, summary

GOOD = {
    "team": "alpha",
    "environment": "dev",
    "owner": "alpha-lead@example.com",
    "cost_center": "cc-1000",
    "region": "canadacentral",
    "monthly_budget": 100,
    "access": {"contributors": ["grp-guardrails-alpha"], "readers": ["grp-guardrails-finance"]},
    "address_space": "10.100.20.0/24",
}


@pytest.fixture
def requests(tmp_path):
    def write(*bodies, names=None):
        for old in tmp_path.glob("*.json"):
            old.unlink()
        for i, body in enumerate(bodies):
            name = (names or [None] * len(bodies))[i] or f"{body.get('team', 'x')}-{body.get('environment', 'x')}.json"
            (tmp_path / name).write_text(json.dumps(body) if not isinstance(body, str) else body)
        return check(tmp_path)

    return write


def test_the_real_requests_pass():
    assert check() == []
    assert len(load()[0]) == 7


def test_a_good_request_passes(requests):
    assert requests(GOOD) == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"owner": "the payments team"}, "owner: an email address"),
        ({"cost_center": "2040"}, "cost_center: like cc-2040"),
        ({"region": "eastus"}, "region: eastus isn't allowed; use canadacentral, canadaeast"),
        ({"monthly_budget": 0}, "monthly_budget: a positive number of dollars"),
        ({"monthly_budget": "100"}, "monthly_budget: a positive number of dollars"),
        ({"monthly_budget": True}, "monthly_budget: a positive number of dollars"),
        ({"monthly_budget": 900}, "dev environments can ask for up to 500; more needs a platform review"),
        ({"access": {"contributors": ["Payments Team"]}}, "access.contributors: 'Payments Team' isn't a group name"),
        ({"access": {"owners": ["grp-x"]}}, "access: an object with contributors and readers lists"),
        ({"access": ["grp-x"]}, "access: an object with contributors and readers lists"),
        ({"address_space": "10.100.20.0"}, "address_space: between /24 and /26"),
        ({"address_space": "10.100.20.0/23"}, "address_space: between /24 and /26"),
        ({"address_space": "192.168.1.0/24"}, "address_space: must be inside 10.100.0.0/16"),
        ({"address_space": "fd00::/64"}, "address_space: must be inside 10.100.0.0/16"),
        ({"address_space": "nope"}, "address_space: 'nope' isn't a network"),
        ({"colour": "blue"}, "unknown fields colour"),
    ],
)
def test_mistakes_in_one_request(requests, change, message):
    errors = requests({**GOOD, **change})
    assert len(errors) == 1 and errors[0].startswith("alpha-dev.json: ") and message in errors[0]


def test_bad_names(requests):
    assert "team: 2 to 21 lowercase letters" in requests({**GOOD, "team": "Alpha"})[0]
    assert "environment: one of dev, test, prod" in requests({**GOOD, "environment": "staging"})[0]
    assert "name the file alpha-dev.json" in requests(GOOD, names=["alpha.json"])[0]


def test_missing_fields(requests):
    errors = requests({"team": "alpha", "environment": "dev"})
    assert errors == ["alpha-dev.json: missing cost_center, monthly_budget, owner"]


def test_prod_doesnt_give_people_contributor(requests):
    prod = {**GOOD, "environment": "prod", "monthly_budget": 2000}
    errors = requests(prod)
    assert len(errors) == 1 and "prod doesn't give people Contributor" in errors[0]
    assert requests({**prod, "access": {"readers": ["grp-guardrails-alpha"]}}) == []
    assert "prod environments can ask for up to 5000" in requests({**prod, "access": {}, "monthly_budget": 6000})[0]


def test_networks_cant_overlap(requests):
    other = {**GOOD, "team": "beta", "address_space": "10.100.20.128/25"}
    errors = requests(GOOD, other)
    assert errors == ["beta-dev.json: address_space 10.100.20.128/25 overlaps alpha-dev.json's 10.100.20.0/24"]


def test_requests_without_a_network_never_overlap(requests):
    assert requests({k: v for k, v in GOOD.items() if k != "address_space"}, {**GOOD, "team": "beta"}) == []


def test_files_that_arent_requests(requests):
    errors = requests("{nope", "[1]", names=["broken-dev.json", "list-dev.json"])
    assert errors == [
        "broken-dev.json: not valid JSON (Expecting property name enclosed in double quotes, line 1)",
        "list-dev.json: should be a JSON object",
    ]


def test_the_resource_group_must_pass_the_guardrails(tmp_path):
    (tmp_path / "alpha-dev.json").write_text(json.dumps(GOOD))
    errors = check(tmp_path, group="decommissioned")
    assert errors == ["alpha-dev.json: the resource group would be blocked by deny-new-resources"]


def test_allowed_locations_come_from_the_platform_settings(tmp_path):
    assert allowed_locations() == ["canadacentral", "canadaeast"]
    bad = tmp_path / "x.tfvars"
    bad.write_text("exemptions = {}\n")
    with pytest.raises(ValueError, match="no allowed_locations"):
        allowed_locations(bad)


def test_the_summary_for_a_pull_request():
    text = summary()
    row = (
        "| `rg-payments-prod` | payments-lead@example.com | cc-2040 | $1,200 | - "
        "| grp-guardrails-payments, grp-guardrails-finance | 10.100.2.0/24 |"
    )
    assert row in text
    assert "| `rg-data-dev` |" in text and "| none |" in text
    assert text.endswith("7 environments, $4,490 a month in budgets.")


def test_the_command_line(tmp_path, capsys):
    assert main(["check"]) == 0
    assert "OK: 7 environment requests pass." in capsys.readouterr().out
    assert main(["summary"]) == 0
    assert "rg-storefront-prod" in capsys.readouterr().out
    (tmp_path / "alpha-dev.json").write_text(json.dumps({**GOOD, "monthly_budget": -5}))
    assert main(["check", "--dir", str(tmp_path)]) == 1
    assert "alpha-dev.json: monthly_budget" in capsys.readouterr().out
