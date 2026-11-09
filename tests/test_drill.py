"""The mess drill's script, against a stand-in shell, a fake clock and fake retail prices: the order of
the steps, the timing, the clean-up, and the prices."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from drills import drill

ASSIGNMENT = "/providers/Microsoft.Management/managementGroups/guardrails-landing-zones/providers/Microsoft.Authorization/policyAssignments/gr-baseline"
RG = "/subscriptions/0/resourceGroups/rg-legacy-poc/providers"
MESS_IDS = (
    [f"{RG}/Microsoft.Compute/virtualMachines/vm-poc-0{i}" for i in (1, 2)]
    + [f"{RG}/Microsoft.Network/networkInterfaces/nic-poc-0{i}" for i in (1, 2)]
    + [
        f"{RG}/Microsoft.Network/virtualNetworks/vnet-legacy-poc",
        f"{RG}/Microsoft.Storage/storageAccounts/stpoc",
        f"{RG}/Microsoft.Sql/servers/sql-poc",
        f"{RG}/Microsoft.Sql/servers/sql-poc/databases/sqldb-poc",
        f"{RG}/Microsoft.Compute/disks/disk-poc-data-old",
        f"{RG}/Microsoft.Network/publicIPAddresses/pip-poc-old",
        f"{RG}/Microsoft.Web/serverfarms/asp-poc-unused",
    ]
)


class FakeShell:
    """Answers each command from canned replies; replies given as lists are used in turn."""

    def __init__(self, **replies):
        self.calls: list[list[str]] = []
        self.replies = replies

    def reply(self, key, default=""):
        value = self.replies.get(key, default)
        if isinstance(value, list):
            return value.pop(0) if len(value) > 1 else value[0]
        return value

    def __call__(self, args, cwd=None):
        self.calls.append(args)
        text = " ".join(args)
        if "output -json assignment_ids" in text:
            return json.dumps({"landing-zones/gr-baseline": ASSIGNMENT})
        if text.startswith("az account show"):
            return "om@example.com\n"
        if args[0] == "ssh-keygen":
            path = Path(args[args.index("-f") + 1])
            path.write_text("private")
            path.with_suffix(".pub").write_text("ssh-ed25519 AAAA guardrails-drill\n")
            return ""
        if "output -json resource_group" in text:
            return '"rg-legacy-poc"'
        if "output -json resource_ids" in text:
            return json.dumps(MESS_IDS)
        if "policy state list" in text:
            return self.reply("states", "[]")
        if "policy remediation show" in text:
            return self.reply("remediation", "Succeeded")
        return ""

    def find(self, text):
        return [" ".join(c) for c in self.calls if text in " ".join(c)]


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def states(ids, rule="inherit-owner"):
    return json.dumps([{"id": i.upper(), "rule": rule} for i in ids])


PRICES = {
    "S4 LRS": {"unitOfMeasure": "1/Month", "retailPrice": 2.10},
    "Standard IPv4 Static Public IP": {"unitOfMeasure": "1 Hour", "retailPrice": 0.006},
    "Basic Plan - Linux": {"unitOfMeasure": "1 Hour", "retailPrice": 0.021},
    "skuName eq 'S3'": {"unitOfMeasure": "1/Day", "retailPrice": 6.40},
    "skuName eq 'S0'": {"unitOfMeasure": "1/Day", "retailPrice": 0.64},
}


def fetch(url):
    from urllib.parse import unquote

    query = unquote(url)
    assert "currencyCode='CAD'" in query and "armRegionName eq 'canadacentral'" in query and "priceType eq 'Consumption'" in query
    return {"Items": [item for key, item in PRICES.items() if key in query]}


@pytest.fixture
def mess(tmp_path, monkeypatch):
    folder = tmp_path / "mess"
    folder.mkdir()
    monkeypatch.setattr(drill, "MESS", folder)
    monkeypatch.setattr(drill, "RESULTS", tmp_path / "results")
    (folder / "drill.auto.tfvars.json").write_text(json.dumps({"baseline_assignment_id": ASSIGNMENT}))
    return folder


# --- Seed and clean ------------------------------------------------------------------------------------


def test_seeding(mess, tmp_path):
    shell = FakeShell()
    key = tmp_path / "keys" / "guardrails-drill"
    drill.seed(shell, key=key)

    settings = json.loads((mess / "drill.auto.tfvars.json").read_text())
    assert settings == {
        "baseline_assignment_id": ASSIGNMENT,
        "owner": "om@example.com",
        "ssh_public_key": "ssh-ed25519 AAAA guardrails-drill",
    }
    assert shell.find("ssh-keygen -q -t ed25519")
    apply = shell.find(" apply ")[0]
    assert "-var exempt=true -var fixed=false" in apply and "-auto-approve" in apply


def test_seeding_reuses_an_existing_key(mess, tmp_path):
    key = tmp_path / "guardrails-drill"
    key.with_suffix(".pub").write_text("ssh-ed25519 BBBB existing\n")
    shell = FakeShell()
    drill.seed(shell, key=key)
    assert not shell.find("ssh-keygen")


def test_cleaning(mess):
    shell = FakeShell()
    drill.clean(shell)
    assert "destroy -auto-approve -input=false -var exempt=false -var fixed=true" in shell.find("destroy")[0]


# --- Measure ---------------------------------------------------------------------------------------------


def test_a_drill_that_finds_and_fixes_everything(mess):
    os_disks = [f"{RG}/Microsoft.Compute/disks/vm-poc-01_OsDisk"]
    shell = FakeShell(
        states=[
            "[]",  # the first scan hasn't finished
            states(MESS_IDS[:5]),  # some found
            json.dumps(
                [{"id": i, "rule": "inherit-owner"} for i in MESS_IDS + os_disks]
                + [{"id": MESS_IDS[5], "rule": "no-public-blobs"}]
            ),  # all found
            states(MESS_IDS[:2]),  # still fixing
            "[]",
        ],
        remediation=["Evaluating", "Succeeded"],
    )
    clock = FakeClock()
    r = drill.measure(shell, clock=clock, fetch=fetch, interval=60, timeout=3600)

    assert (r.resource_group, r.expected, r.found) == ("rg-legacy-poc", 11, 11)
    assert r.minutes_to_find == 2.0  # found on the third check
    assert r.rules_broken == {"inherit-owner": 12, "no-public-blobs": 1}
    assert r.remediation == dict.fromkeys(drill.INHERIT, "Succeeded")
    assert r.minutes_to_fix > r.minutes_to_find and r.still_non_compliant == 0

    # In order: lift the exemption, scan, fix tags, fix the rest, scan again.
    order = [
        shell.calls.index(next(c for c in shell.calls if text in " ".join(c)))
        for text in (
            "-var exempt=false -var fixed=false",
            "trigger-scan",
            "remediation create",
            "-var exempt=false -var fixed=true",
        )
    ]
    assert order == sorted(order)
    assert len(shell.find("trigger-scan")) == 2
    create = shell.find("remediation create --name drill-inherit-owner")[0]
    assert f"--policy-assignment {ASSIGNMENT}" in create and "--definition-reference-id inherit-owner" in create

    assert r.savings == {
        "Unattached 32 GiB Standard HDD disk": 2.10,
        "Orphaned static public IP": 4.38,
        "Empty App Service plan (B1 Linux)": 15.33,
        "Database at S3 (100 DTUs)": 194.56,
        "Database at S0 (10 DTUs) instead": -19.46,
    }
    assert r.monthly_savings == 196.91


def test_a_drill_that_runs_out_of_time(mess):
    shell = FakeShell(states=[states(MESS_IDS[:4])], remediation=["Evaluating"])
    r = drill.measure(shell, clock=FakeClock(), fetch=lambda url: {"Items": []}, interval=60, timeout=180)
    assert (r.found, r.minutes_to_find, r.minutes_to_fix, r.still_non_compliant) == (4, None, None, 4)
    assert r.remediation["inherit-owner"] == "Evaluating"
    assert all(v is None for v in r.savings.values()) and r.monthly_savings == 0
    md = drill.to_markdown(r)
    assert "| Time to find them all | not within the time limit |" in md
    assert "| Unattached 32 GiB Standard HDD disk | price not found |" in md


def test_results_rules_without_a_reference_id(mess):
    shell = FakeShell(states=[json.dumps([{"id": i, "rule": None} for i in MESS_IDS]), "[]"])
    r = drill.measure(shell, clock=FakeClock(), fetch=lambda url: {"Items": []}, interval=60, timeout=600)
    assert r.rules_broken == {"(whole assignment)": 11}


# --- Prices -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("items", "monthly"),
    [
        ([{"unitOfMeasure": "1 Hour", "retailPrice": 0.01}], 7.3),
        ([{"unitOfMeasure": "1/Day", "retailPrice": 1}], 30.4),
        ([{"unitOfMeasure": "1/Month", "retailPrice": 5}], 5.0),
        ([{"unitOfMeasure": "10K", "retailPrice": 5}, {"unitOfMeasure": "1/Month", "retailPrice": 3}], 3.0),
        ([], None),
    ],
)
def test_prices_become_monthly(items, monthly):
    assert drill.price_monthly(lambda url: {"Items": items}, "canadacentral", "skuName eq 'x'") == monthly


# --- Output and the command line ------------------------------------------------------------------------


def test_markdown_and_saving(mess, tmp_path):
    shell = FakeShell(states=[states(MESS_IDS), "[]"])
    r = drill.measure(shell, clock=FakeClock(), fetch=fetch, interval=60, timeout=600)
    md = drill.to_markdown(r)
    assert md.startswith(f"### Mess drill, {r.started[:10]}")
    assert "| Mess resources found by Azure Policy | 11 of 11 |" in md and "| Time to find them all | 0 minutes |" in md
    path = drill.save(r)
    saved = json.loads(path.read_text())
    assert path.name == f"{r.started[:10]}.json" and saved["monthly_savings"] == r.monthly_savings


def test_the_command_line_asks_before_spending(mess, capsys):
    shell = FakeShell()
    assert drill.main(["seed"], shell=shell) == 2
    assert "about $0.30 an hour" in capsys.readouterr().out and shell.calls == []
    assert drill.main(["clean"], shell=shell) == 2
    assert "delete the mess" in capsys.readouterr().out


def test_the_command_line(mess, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(drill, "KEY", tmp_path / "k")
    seeded, cleaned = [], []
    monkeypatch.setattr(drill, "seed", lambda shell: seeded.append(1))
    monkeypatch.setattr(drill, "clean", lambda shell: cleaned.append(1))
    assert drill.main(["seed", "--yes"], shell=FakeShell()) == 0 and seeded
    assert drill.main(["clean", "--yes"], shell=FakeShell()) == 0 and cleaned
    fake = drill.Result(started="2026-11-20T15:00:00+00:00", resource_group="rg-legacy-poc", expected=11)
    monkeypatch.setattr(drill, "measure", lambda shell, interval, timeout: fake)
    assert drill.main(["measure", "--interval", "5"], shell=FakeShell()) == 0
    out = capsys.readouterr().out
    assert "### Mess drill, 2026-11-20" in out and "Saved" in out
    assert (tmp_path / "results" / "2026-11-20.json").exists()
