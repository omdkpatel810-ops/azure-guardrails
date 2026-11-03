"""Checks on the policy files themselves, catching mistakes before Azure would."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.policy_eval import PARENTS, load, parameter_values, rules_of

ROOT = Path(__file__).resolve().parent.parent
DEFINITIONS, INITIATIVES, ASSIGNMENTS = load()
TAG_CONTRIBUTOR = "4a9ae827-6dc8-4573-8ac7-8239d42aa03f"  # learn.microsoft.com built-in roles


@pytest.mark.parametrize("key", sorted(DEFINITIONS))
def test_each_definition_is_complete(key):
    d = DEFINITIONS[key]
    props = d["properties"]
    assert d["name"].startswith("guardrails-") and len(d["name"]) <= 64
    assert props["displayName"].startswith("Guardrails: ")
    assert len(props["description"]) > 40
    assert props["mode"] in ("All", "Indexed")
    assert set(props["metadata"]) == {"category", "version"}

    # The effect is always a parameter that can be switched off, so a broken rule can be
    # disabled by a one-line assignment change instead of an emergency rewrite.
    assert props["policyRule"]["then"]["effect"] == "[parameters('effect')]"
    assert "Disabled" in props["parameters"]["effect"]["allowedValues"]


def test_definition_names_are_unique():
    names = [d["name"] for d in DEFINITIONS.values()]
    assert len(names) == len(set(names))


def test_policies_that_fix_resources_ask_only_for_tag_contributor():
    asked = {
        key: d["properties"]["policyRule"]["then"]["details"]["roleDefinitionIds"]
        for key, d in DEFINITIONS.items()
        if "details" in d["properties"]["policyRule"]["then"]
    }
    assert set(asked) == {"inherit-resource-group-tag"}
    assert asked["inherit-resource-group-tag"] == [f"/providers/Microsoft.Authorization/roleDefinitions/{TAG_CONTRIBUTOR}"]


@pytest.mark.parametrize("key", sorted(INITIATIVES))
def test_each_initiative_references_real_definitions_with_valid_parameters(key):
    initiative = INITIATIVES[key]
    assert initiative["name"].startswith("guardrails-")
    ids = [p["referenceId"] for p in initiative["policies"]]
    assert len(ids) == len(set(ids)), "reference IDs must be unique"
    used = set()
    for ref in initiative["policies"]:
        declared = DEFINITIONS[ref["definition"]]["properties"]["parameters"]
        assert set(ref["parameters"]) <= set(declared)
        missing = {n for n, spec in declared.items() if "defaultValue" not in spec} - set(ref["parameters"])
        assert not missing, f"{ref['referenceId']} doesn't set {missing}"
        for value in ref["parameters"].values():
            used.update(re.findall(r"parameters\('([^']+)'\)", str(value["value"])))
    assert used == set(initiative["parameters"]), "every initiative parameter is used, and no others"


def test_assignments_are_valid():
    seen = set()
    for a in ASSIGNMENTS:
        where = f"{a['scope']}/{a['name']}"
        assert where not in seen, f"{where} appears twice"
        seen.add(where)
        assert a["scope"] in PARENTS
        assert len(a["name"]) <= 24, "management group assignment names can be at most 24 characters"
        kind, key = a["policy"].split(":")
        assert key in (DEFINITIONS if kind == "definition" else INITIATIVES)
        assert rules_of(a, DEFINITIONS, INITIATIVES)  # parameters exist and are allowed values
        assert a["displayName"] and a["description"]


def test_every_blocking_assignment_explains_itself():
    for a in ASSIGNMENTS:
        if "Deny" in a["parameters"].values():
            assert len(a.get("nonComplianceMessage", "")) > 40, f"{a['name']} denies without saying why"


def test_parameter_checks_catch_mistakes():
    declared = DEFINITIONS["allowed-locations"]["properties"]["parameters"]
    with pytest.raises(ValueError, match="isn't an allowed value"):
        parameter_values(declared, {"effect": "Block"}, "test")
    with pytest.raises(ValueError, match="unknown parameters"):
        parameter_values(declared, {"regions": []}, "test")
    with pytest.raises(ValueError, match="no value for parameter 'tagName'"):
        parameter_values(DEFINITIONS["require-resource-group-tag"]["properties"]["parameters"], {}, "test")


def test_the_evaluator_knows_the_same_hierarchy_terraform_builds():
    main = (ROOT / "modules" / "management-groups" / "main.tf").read_text()

    def keys_of(resource):
        block = re.search(rf'resource "azurerm_management_group" "{resource}" {{\n  for_each = {{(.*?)}}', main, re.S)
        assert block, resource
        return set(re.findall(r"^\s+([a-z-]+)\s+=", block.group(1), re.M))

    top, landing = keys_of("top"), keys_of("landing_zone")
    assert top == {k for k, parent in PARENTS.items() if parent == "root"}
    assert landing == {k for k, parent in PARENTS.items() if parent == "landing-zones"}
    assert '"landing_zone"' in main and 'azurerm_management_group.top["landing-zones"].id' in main
