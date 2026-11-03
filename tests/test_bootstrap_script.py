"""bootstrap/bootstrap.sh, run against stand-in az, terraform and gh commands that log every call.

The script only runs once for real, in Cloud Shell, so these tests check its decisions: what it
creates on a first run, what it skips on a second, and how it stops when something's wrong.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUB = "11111111-2222-3333-4444-555555555555"
OUTPUTS = {
    "AZURE_TENANT_ID": "tenant-id",
    "AZURE_SUBSCRIPTION_ID": SUB,
    "AZURE_PLAN_CLIENT_ID": "plan-client-id",
    "AZURE_APPLY_CLIENT_ID": "apply-client-id",
    "TFSTATE_RESOURCE_GROUP": "rg-guardrails-tfstate",
    "TFSTATE_STORAGE_ACCOUNT": "stgrstate1234567890",
    "TFSTATE_CONTAINER": "tfstate",
}

AZ = r"""#!/usr/bin/env bash
echo "az $*" >>"$CALLS"
case "$*" in
  "account show --query id -o tsv") [[ -n "$FAKE_SUB" ]] || exit 1; echo "$FAKE_SUB" ;;
  "account show --query tenantId -o tsv") echo tenant-id ;;
  "account show --query name -o tsv") echo "Azure for Students" ;;
  "policy assignment list"*) printf '%s' "$FAKE_REGION_LIMITS" ;;
  "account management-group show"*) exit "$FAKE_MG_MISSING" ;;
  "storage blob exists"*) echo "$FAKE_BLOB_EXISTS" ;;
esac
"""

TERRAFORM = r"""#!/usr/bin/env bash
echo "terraform $*" >>"$CALLS"
case "$*" in
  *"output -json github_variables") cat "$FAKE_OUTPUTS" ;;
  *"state show"*) exit "$FAKE_ROOT_NOT_IN_STATE" ;;
esac
"""

GH = r"""#!/usr/bin/env bash
echo "gh $*" >>"$CALLS"
case "$*" in
  "auth status") exit "$FAKE_GH_SIGNED_OUT" ;;
  "api user --jq .id") echo 4242 ;;
  "api -X PUT"*)
    body=$(cat)
    echo "body $body" >>"$CALLS"
    [[ "$body" == *reviewers* ]] && exit "$FAKE_REVIEWERS_FAIL"
    ;;
esac
exit 0
"""


@pytest.fixture
def run(tmp_path):
    repo = tmp_path / "repo"
    (repo / "bootstrap").mkdir(parents=True)
    (repo / "platform").mkdir()
    shutil.copy(ROOT / "bootstrap" / "bootstrap.sh", repo / "bootstrap" / "bootstrap.sh")
    (repo / "bootstrap" / "terraform.tfstate").write_text("{}")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"az": AZ, "terraform": TERRAFORM, "gh": GH}.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    (bin_dir / "sleep").write_text("#!/bin/sh\n")  # never wait in tests
    (bin_dir / "sleep").chmod(0o755)
    outputs = tmp_path / "outputs.json"
    outputs.write_text(json.dumps(OUTPUTS))
    calls = tmp_path / "calls.log"

    def go(*args, **fake):
        settings = {
            "FAKE_SUB": SUB,
            "FAKE_REGION_LIMITS": "",
            "FAKE_MG_MISSING": "1",
            "FAKE_BLOB_EXISTS": "false",
            "FAKE_ROOT_NOT_IN_STATE": "1",
            "FAKE_GH_SIGNED_OUT": "0",
            "FAKE_REVIEWERS_FAIL": "0",
            **{k.upper(): str(v) for k, v in fake.items()},
        }
        env = {
            **os.environ,
            **settings,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "FAKE_OUTPUTS": str(outputs),
        }
        env.pop("LOCATION", None)
        if "location" in fake:
            env["LOCATION"] = fake["location"]
        calls.write_text("")
        result = subprocess.run(
            ["bash", str(repo / "bootstrap" / "bootstrap.sh"), *args], env=env, capture_output=True, text=True, check=False
        )
        return result, calls.read_text().splitlines(), repo

    return go


def find(calls, text):
    return [c for c in calls if text in c]


def bodies(calls):
    return [json.loads(c.removeprefix("body ")) for c in calls if c.startswith("body ")]


def test_a_first_run_creates_everything_in_order(run):
    result, calls, repo = run("online")
    assert result.returncode == 0, result.stderr

    order = [
        "az account management-group create --name guardrails --display-name Guardrails",
        "terraform -chdir=bootstrap init -input=false",
        "terraform -chdir=bootstrap apply -var location=canadacentral",
        "az storage blob list",
        "terraform -chdir=bootstrap init -input=false -migrate-state -force-copy",
        "terraform -chdir=platform init -input=false -reconfigure",
        "terraform -chdir=platform import",
        "terraform -chdir=platform apply",
    ]
    positions = [next(i for i, c in enumerate(calls) if c.startswith(step)) for step in order]
    assert positions == sorted(positions)

    assert find(calls, "az provider register --namespace Microsoft.PolicyInsights")
    migrate = find(calls, "-migrate-state")[0]
    assert "storage_account_name=stgrstate" in migrate and "key=bootstrap.tfstate" in migrate
    assert f'subscription_placement={{"{SUB}" = "online"}}' in find(calls, "-chdir=platform apply")[0]
    assert "/providers/Microsoft.Management/managementGroups/guardrails" in find(calls, "platform import")[0]

    # Bootstrap's state now lives in storage, so the local copy is gone and the override stays.
    assert not (repo / "bootstrap" / "terraform.tfstate").exists()
    assert 'backend "azurerm" {}' in (repo / "bootstrap" / "backend_override.tf").read_text()
    assert not (repo / "bootstrap" / "backend_override.tf.next").exists()


def test_the_state_account_name_matches_what_terraform_computes(run):
    _, calls, _ = run()
    expected = "stgrstate" + hashlib.sha1(SUB.encode()).hexdigest()[:10]
    assert f"--account-name {expected}" in find(calls, "storage blob exists")[0]


def test_applies_wait_for_a_typed_yes(run):
    _, calls, _ = run()
    applies = [c for c in calls if c.startswith("terraform ") and " apply" in c]
    assert len(applies) == 2
    assert not any("-auto-approve" in c or "-input=false" in c for c in applies)


def test_github_is_connected_when_the_cli_is_signed_in(run):
    result, calls, _ = run()
    assert result.returncode == 0
    variables = [c.split()[3] for c in find(calls, "gh variable set")]
    assert variables == [*OUTPUTS, "SUBSCRIPTION_PLACEMENT"]
    body = bodies(calls)[0]
    assert body["reviewers"] == [{"type": "User", "id": 4242}]
    assert body["deployment_branch_policy"]["custom_branch_policies"] is True
    assert find(calls, "deployment-branch-policies -f name=main")


def test_without_paid_reviewers_the_environment_is_still_main_only(run):
    result, calls, _ = run(fake_reviewers_fail=1)
    assert result.returncode == 0
    sent = bodies(calls)
    assert len(sent) == 2 and "reviewers" in sent[0] and "reviewers" not in sent[1]
    assert "Couldn't make you a required reviewer" in result.stdout


def test_without_the_github_cli_it_prints_what_to_set(run):
    result, calls, _ = run(fake_gh_signed_out=1)
    assert result.returncode == 0
    assert not find(calls, "gh variable set")
    assert "AZURE_APPLY_CLIENT_ID = apply-client-id" in result.stdout
    assert f'SUBSCRIPTION_PLACEMENT = {{"{SUB}" = "online"}}' in result.stdout


def test_a_second_run_reuses_what_exists(run):
    result, calls, repo = run("sandbox", fake_mg_missing=0, fake_blob_exists="true", fake_root_not_in_state=0)
    assert result.returncode == 0, result.stderr
    assert not find(calls, "management-group create")
    assert not find(calls, "-migrate-state")
    assert not find(calls, "platform import")
    assert "key=bootstrap.tfstate" in find(calls, "-chdir=bootstrap init")[0]
    assert '= "sandbox"}' in find(calls, "-chdir=platform apply")[0]
    assert (repo / "bootstrap" / "backend_override.tf").exists()


def test_it_stops_when_the_subscription_doesnt_allow_the_region(run):
    result, calls, _ = run(fake_region_limits="eastus\nwestus2\ncentralus")
    assert result.returncode == 1
    assert "only allows these regions:\n  eastus\n  westus2\n  centralus" in result.stderr
    assert not find(calls, "terraform")


def test_an_allowed_region_can_be_chosen(run):
    result, calls, _ = run(fake_region_limits="eastus\nwestus2", location="westus2")
    assert result.returncode == 0, result.stderr
    assert "location=westus2" in find(calls, "-chdir=bootstrap apply")[0]


def test_it_refuses_an_unknown_placement(run):
    result, calls, _ = run("production")
    assert result.returncode == 1
    assert "not 'production'" in result.stderr
    assert calls == []


def test_it_stops_if_no_one_is_signed_in(run):
    result, _, _ = run(fake_sub="")
    assert result.returncode == 1
