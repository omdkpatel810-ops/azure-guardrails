"""The Hugging Face Space: what it says about each example, how it handles bad input, the Gradio
page itself, and the folder scripts/build_space.py assembles."""

from __future__ import annotations

import importlib.metadata
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "space"))

import demo  # noqa: E402
from demo import EXAMPLES, GROUPS, evaluate, load_example, rules_rows, tags_text, verdict_html  # noqa: E402
from scripts.build_space import build  # noqa: E402
from tools.policy_eval import Result  # noqa: E402


def outcome_of(label):
    return evaluate(*load_example(label))


def text(rows):
    return "\n".join(" | ".join(row) for row in rows)


# --- Every example says the right thing -------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "allowed", "says"),
    [
        (
            "Resource group with no tags",
            False,
            ["no owner tag", "no cost-center tag", "no environment tag", "set at Landing zones"],
        ),
        ("The same, in the sandbox", True, ["**Audit**", "set at Sandbox"]),
        ("VM with no tags, in a tagged group", True, ["gets the resource group's: payments-team@example.com"]),
        (
            "VM in East US",
            False,
            ["eastus isn't an allowed region. Allowed: canadacentral, canadaeast.", "set at Guardrails (root)"],
        ),
        ("Storage open to the internet", False, ["allowBlobPublicAccess isn't false", "It allows TLS1_0."]),
        ("Public IP address in Corp", False, ["public IP addresses aren't allowed here", "set at Corp"]),
        ("Public IP address in Online", True, []),
        ("Anything in a decommissioned subscription", False, ["This subscription is decommissioned"]),
    ],
)
def test_each_example(label, allowed, says):
    outcome = outcome_of(label)
    assert outcome.error is None
    assert outcome.allowed is allowed
    for phrase in says:
        assert phrase in text(outcome.rows)


def test_every_example_loads_into_a_group_the_page_offers():
    for label in EXAMPLES:
        group, resource, _ = load_example(label)
        assert group in GROUPS and resource.strip().startswith("{")


def test_deny_rows_come_first():
    effects = [row[0] for row in outcome_of("Storage open to the internet").rows]
    assert effects == ["**Deny**", "**Deny**", "**Modify**", "**Modify**", "**Modify**"]


def test_rule_titles_read_as_sentences():
    rows = outcome_of("Resource group with no tags").rows
    assert rows[0][1] == "Resource groups must have a tag: owner<br><small>set at Landing zones</small>"


# --- The explanations for each kind of rule -------------------------------------------------------


def test_resource_groups_outside_canada():
    outcome = evaluate(
        "online",
        '{"type": "Microsoft.Resources/subscriptions/resourceGroups", "location": "westeurope", '
        '"tags": {"owner": "a", "cost-center": "b", "environment": "c"}}',
    )
    assert outcome.allowed is False
    assert "westeurope isn't an allowed region" in text(outcome.rows)


def test_a_resource_with_no_region():
    outcome = evaluate("online", '{"type": "Microsoft.Compute/virtualMachines"}')
    assert "No region isn't an allowed region" in text(outcome.rows)


def test_storage_over_plain_http_without_a_tls_minimum():
    outcome = evaluate(
        "online",
        '{"type": "Microsoft.Storage/storageAccounts", "location": "canadacentral", '
        '"properties": {"allowBlobPublicAccess": false, "supportsHttpsTrafficOnly": false}}',
    )
    assert "It accepts plain HTTP and sets no minimum TLS version." in text(outcome.rows)


def test_rules_without_their_own_explanation_use_the_assignment_message():
    definition = {"name": "x", "properties": {"displayName": "Guardrails: something new", "description": "From the definition."}}
    result = Result("online/x", "something-new", "Deny", definition, {})
    assert demo._why(result, {}, None, "From the assignment.") == "From the assignment."


# --- Bad input ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("resource", "resource_group", "message"),
    [
        ("", "", "Paste the resource as JSON."),
        ("{nope", "", "The resource isn't valid JSON: Expecting property name enclosed in double quotes (line 1, column 2)."),
        ("[1, 2]", "", "The resource should be a JSON object"),
        ('{"name": "x"}', "", 'The resource needs a "type"'),
        ('{"type": "x"}', "{", "The resource group isn't valid JSON"),
        ("{" + " " * 20_001 + "}", "", "longer than 20,000 characters"),
    ],
)
def test_bad_input_is_explained(resource, resource_group, message):
    outcome = evaluate("online", resource, resource_group)
    assert outcome.allowed is None and message in outcome.error
    assert "Can&#x27;t check that yet" in verdict_html(outcome) or "Can't check that yet" in verdict_html(outcome)
    assert tags_text(outcome) == ""


def test_an_unknown_group_is_explained():
    outcome = evaluate("production", '{"type": "x"}')
    assert "unknown management group 'production'" in outcome.error


def test_input_is_escaped_in_the_verdict():
    outcome = evaluate("<b>sandbox</b>", '{"type": "x"}')
    assert "unknown management group '<b>sandbox</b>'" in outcome.error
    assert "<b>" not in verdict_html(outcome) and "&lt;b&gt;sandbox" in verdict_html(outcome)


# --- The verdict and the tags -----------------------------------------------------------------


def test_verdicts():
    assert "3 rules block it" in verdict_html(outcome_of("Resource group with no tags"))
    assert "1 rule blocks it" in verdict_html(outcome_of("VM in East US"))
    assert "No rule applies" in verdict_html(outcome_of("Public IP address in Online"))
    sandbox = verdict_html(outcome_of("The same, in the sandbox"))
    assert 'class="verdict allowed"' in sandbox and "3 rules allowed, but reported as non-compliant" in sandbox
    vm = verdict_html(outcome_of("VM with no tags, in a tagged group"))
    assert "3 rules changed on the way in" in vm


def test_tags_show_only_when_modify_changed_them():
    assert '"owner": "payments-team@example.com"' in tags_text(outcome_of("VM with no tags, in a tagged group"))
    assert tags_text(outcome_of("Resource group with no tags")) == ""


def test_the_rules_tab_lists_every_assignment_top_down():
    rows = rules_rows()
    assert [r[0] for r in rows] == ["Guardrails (root)", "Platform", "Landing zones", "Corp", "Sandbox", "Decommissioned"]
    assert rows[0][2] == "Resources only in allowed regions; Resource groups only in allowed regions"
    assert rows[1][3] == "Deny, Modify" and rows[4][3] == "Audit, Modify"


# --- The Gradio page ----------------------------------------------------------------------------


def test_the_page_builds_and_its_handlers_work():
    gr = pytest.importorskip("gradio")
    import app

    assert isinstance(app.demo, gr.Blocks)
    verdict, table, tags = app.check(*load_example("VM with no tags, in a tagged group"))
    assert "Allowed" in verdict and table.visible and tags.visible
    group, _resource, _resource_group, verdict, table, tags = app.pick("Resource group with no tags")
    assert group == "online" and "Denied" in verdict and not tags.visible
    verdict, table, tags = app.check("online", "", "")
    assert "Paste the resource" in verdict and not table.visible


# --- The Space folder -----------------------------------------------------------------------------


def test_the_build_has_everything_the_space_needs(tmp_path):
    out = tmp_path / "space"
    (out / "stale.txt").parent.mkdir(parents=True)
    (out / "stale.txt").write_text("from an earlier build")
    files = build(out)

    assert not (out / "stale.txt").exists()
    for needed in [
        "README.md",
        "app.py",
        "demo.py",
        "tools/policy_eval.py",
        "policies/assignments.json",
        "policies/definitions/allowed-locations.json",
        "policies/initiatives/baseline.json",
        "examples/rg-tagged.json",
    ]:
        assert needed in files and (out / needed).is_file()
    for path in (ROOT / "policies").rglob("*.json"):
        assert (out / path.relative_to(ROOT)).read_text() == path.read_text()


def test_the_space_card_matches_the_gradio_version_we_test_with():
    card = (ROOT / "space" / "README.md").read_text()
    front = card.split("---")[1]
    sdk_version = re.search(r"^sdk_version: (\S+)$", front, re.M).group(1)
    pinned = re.search(r"^gradio==(\S+)$", (ROOT / "requirements-dev.txt").read_text(), re.M).group(1)
    assert sdk_version == pinned
    assert re.search(r"^app_file: app.py$", front, re.M) and re.search(r"^sdk: gradio$", front, re.M)
    short = re.search(r"^short_description: (.+)$", front, re.M).group(1)
    assert len(short) <= 60, "Hugging Face cuts short descriptions at 60 characters"
    assert importlib.metadata.version("gradio") == pinned


def test_storage_over_plain_http_with_modern_tls():
    outcome = evaluate(
        "online",
        '{"type": "Microsoft.Storage/storageAccounts", "location": "canadacentral", '
        '"properties": {"allowBlobPublicAccess": false, "supportsHttpsTrafficOnly": false, "minimumTlsVersion": "TLS1_2"}}',
    )
    assert "It accepts plain HTTP. Storage must use HTTPS only" in text(outcome.rows)


def test_the_build_can_start_from_nothing(tmp_path):
    assert "app.py" in build(tmp_path / "new" / "space")
