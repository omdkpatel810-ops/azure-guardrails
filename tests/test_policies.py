"""The rules themselves: each definition against resources that should and shouldn't pass, then
whole management groups, where assignments stack from the root down."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.policy_eval import apply_modify, decide, evaluate_definition, load

ROOT = Path(__file__).resolve().parent.parent
DEFINITIONS, INITIATIVES, ASSIGNMENTS = load()

TAGS = {"owner": "om@example.com", "cost-center": "cc-1001", "environment": "dev"}


def vm(location="canadacentral", tags=None):
    return {"type": "Microsoft.Compute/virtualMachines", "name": "vm-app-01", "location": location, "tags": tags or {}}


def rg(location="canadacentral", tags=None, name="rg-app"):
    return {"type": "Microsoft.Resources/subscriptions/resourceGroups", "name": name, "location": location, "tags": tags or {}}


def storage(**properties):
    base = {"allowBlobPublicAccess": False, "supportsHttpsTrafficOnly": True, "minimumTlsVersion": "TLS1_2"}
    base.update(properties)
    base = {k: v for k, v in base.items() if v is not None}
    return {
        "type": "Microsoft.Storage/storageAccounts",
        "name": "stapp01",
        "location": "canadacentral",
        "tags": dict(TAGS),
        "properties": base,
    }


def effect(name, resource, resource_group=None, **parameters):
    return evaluate_definition(DEFINITIONS[name], resource, parameters, resource_group)


# --- One definition at a time ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("resource", "parameters", "expected"),
    [
        (vm("canadacentral"), {}, None),
        (vm("canadaeast"), {}, None),
        (vm("CanadaCentral"), {}, None),  # Azure compares regions without case
        (vm("eastus"), {}, "Deny"),
        (vm("eastus"), {"effect": "Audit"}, "Audit"),
        (vm("eastus"), {"effect": "Disabled"}, None),
        (vm("eastus"), {"allowedLocations": ["eastus"]}, None),
        ({"type": "Microsoft.Network/dnszones", "name": "example.com", "location": "global"}, {}, None),
        ({"type": "Microsoft.AzureActiveDirectory/b2cDirectories", "name": "b2c", "location": "unitedstates"}, {}, None),
        (rg("eastus"), {}, None),  # Indexed mode skips resource groups; the next rule covers them
    ],
)
def test_resources_stay_in_allowed_regions(resource, parameters, expected):
    assert effect("allowed-locations", resource, **parameters) == expected


@pytest.mark.parametrize(
    ("resource", "expected"),
    [(rg("canadaeast"), None), (rg("westeurope"), "Deny"), (vm("westeurope"), None)],
)
def test_resource_groups_stay_in_allowed_regions(resource, expected):
    assert effect("allowed-resource-group-locations", resource) == expected


@pytest.mark.parametrize(
    ("resource", "expected"),
    [
        (rg(tags={"owner": "om@example.com"}), None),
        (rg(tags={"Owner": "om@example.com"}), None),  # tag names ignore case
        (rg(tags={}), "Deny"),
        (rg(tags={"owner": ""}), "Deny"),
        (rg(tags={"team": "data"}), "Deny"),
        (vm(tags={}), None),  # only resource groups need the tag; resources inherit it
    ],
)
def test_resource_groups_need_the_tag(resource, expected):
    assert effect("require-resource-group-tag", resource, tagName="owner") == expected


def test_resources_inherit_a_missing_tag_from_their_resource_group():
    group = rg(tags={"owner": "om@example.com", "environment": "prod"})
    resource = vm(tags={"environment": "dev"})

    assert effect("inherit-resource-group-tag", resource, group, tagName="owner") == "Modify"
    fixed = apply_modify(DEFINITIONS["inherit-resource-group-tag"], resource, {"tagName": "owner"}, group)
    assert fixed["tags"] == {"environment": "dev", "owner": "om@example.com"}
    assert resource["tags"] == {"environment": "dev"}  # the original is untouched

    # A tag the resource already has is kept, even if the group's differs.
    assert effect("inherit-resource-group-tag", resource, group, tagName="environment") is None


@pytest.mark.parametrize(
    ("resource", "group"),
    [
        (vm(), rg(tags={})),  # nothing to inherit
        (vm(), rg(tags={"owner": ""})),
        (rg(), rg(tags={"owner": "om@example.com"})),  # Indexed mode skips resource groups
    ],
)
def test_nothing_to_inherit(resource, group):
    assert effect("inherit-resource-group-tag", resource, group, tagName="owner") is None


def test_tag_inheritance_can_be_switched_off():
    assert effect("inherit-resource-group-tag", vm(), rg(tags=TAGS), tagName="owner", effect="Disabled") is None


@pytest.mark.parametrize(
    ("resource", "expected"),
    [
        (storage(), None),
        (storage(allowBlobPublicAccess="False"), None),
        (storage(allowBlobPublicAccess=True), "Deny"),
        # Left out, the setting isn't "false", so it's denied: Terraform's azurerm provider
        # sends true unless allow_nested_items_to_be_public = false is set.
        (storage(allowBlobPublicAccess=None), "Deny"),
        (vm(), None),
    ],
)
def test_storage_never_allows_anonymous_blob_access(resource, expected):
    assert effect("deny-storage-public-access", resource) == expected


@pytest.mark.parametrize(
    ("resource", "expected"),
    [
        (storage(), None),
        (storage(minimumTlsVersion="TLS1_3"), None),
        (storage(minimumTlsVersion="TLS1_0"), "Deny"),
        (storage(minimumTlsVersion="TLS1_1"), "Deny"),
        (storage(minimumTlsVersion=None), "Deny"),
        (storage(supportsHttpsTrafficOnly=False), "Deny"),
        (vm(), None),
    ],
)
def test_storage_uses_https_and_tls_1_2(resource, expected):
    assert effect("require-storage-secure-transfer", resource) == expected


def test_public_ips_are_denied_where_assigned():
    public_ip = {"type": "Microsoft.Network/publicIPAddresses", "name": "pip-app", "location": "canadacentral"}
    nic = {"type": "Microsoft.Network/networkInterfaces", "name": "nic-app", "location": "canadacentral"}
    assert effect("deny-public-ip", public_ip) == "Deny"
    assert effect("deny-public-ip", nic) is None


@pytest.mark.parametrize(
    ("resource_type", "expected"),
    [
        ("Microsoft.Compute/virtualMachines", "Deny"),
        ("Microsoft.Resources/subscriptions/resourceGroups", "Deny"),
        ("Microsoft.Authorization/locks", None),
        ("Microsoft.Authorization/roleAssignments", None),
        ("Microsoft.Authorization/policyExemptions", None),
    ],
)
def test_decommissioned_subscriptions_allow_only_clean_up(resource_type, expected):
    assert effect("deny-new-resources", {"type": resource_type, "name": "x", "location": "canadacentral"}) == expected


# --- Whole management groups -----------------------------------------------------------------


def rules(decision, kind=None):
    return sorted(r.rule for r in decision.results if kind is None or r.effect == kind)


def test_an_untagged_resource_group_is_denied_in_a_landing_zone():
    decision = decide("online", rg(tags={"owner": "om@example.com"}))
    assert not decision.allowed
    assert rules(decision, "Deny") == [
        "require-resource-group-tag (rg-needs-cost-center)",
        "require-resource-group-tag (rg-needs-environment)",
    ]


def test_the_same_resource_group_is_only_reported_in_the_sandbox():
    decision = decide("sandbox", rg(tags={}))
    assert decision.allowed
    assert decision.effects == {"Audit"}
    assert len(decision.results) == 3


def test_a_tagged_resource_group_in_canada_is_allowed_everywhere_but_decommissioned():
    for group in ("platform", "corp", "online", "sandbox"):
        decision = decide(group, rg(tags=TAGS))
        assert decision.allowed and decision.results == [], group
    assert not decide("decommissioned", rg(tags=TAGS)).allowed


def test_data_residency_applies_in_every_group_including_the_sandbox():
    for group in ("platform", "corp", "online", "sandbox"):
        decision = decide(group, vm("eastus", TAGS), rg(tags=TAGS))
        assert not decision.allowed, group
        assert rules(decision, "Deny") == ["allowed-locations (resources-in-allowed-regions)"]


def test_public_storage_is_denied_in_landing_zones_and_audited_in_the_sandbox():
    public = storage(allowBlobPublicAccess=True)
    assert rules(decide("online", public, rg(tags=TAGS)), "Deny") == ["deny-storage-public-access (no-public-blobs)"]
    assert rules(decide("platform", public, rg(tags=TAGS)), "Deny") == ["deny-storage-public-access (no-public-blobs)"]
    sandbox = decide("sandbox", public, rg(tags=TAGS))
    assert sandbox.allowed and rules(sandbox, "Audit") == ["deny-storage-public-access (no-public-blobs)"]


def test_public_ips_are_denied_in_corp_but_not_online():
    public_ip = {"type": "Microsoft.Network/publicIPAddresses", "name": "pip", "location": "canadacentral", "tags": TAGS}
    assert not decide("corp", public_ip, rg(tags=TAGS)).allowed
    assert decide("online", public_ip, rg(tags=TAGS)).allowed


def test_an_untagged_vm_inherits_all_three_tags_and_is_allowed():
    decision = decide("online", vm(tags={}), rg(tags=TAGS))
    assert decision.allowed
    assert decision.effects == {"Modify"}
    assert decision.resource["tags"] == TAGS


def test_inherited_tags_count_before_the_audit_rules_run():
    # Modify runs first, so the storage account below isn't reported for missing tags (it
    # couldn't be anyway: the tag rules only check resource groups) and keeps its own owner.
    account = storage()
    account["tags"] = {"owner": "data-team@example.com"}
    decision = decide("sandbox", account, rg(tags=TAGS))
    assert decision.allowed
    assert decision.resource["tags"] == {**TAGS, "owner": "data-team@example.com"}
    assert rules(decision, "Audit") == []


# --- The examples in examples/, as the README shows them --------------------------------------

EXAMPLES = ROOT / "examples"


@pytest.mark.parametrize(
    ("group", "example", "with_group", "allowed", "effects"),
    [
        ("online", "rg-untagged", False, False, {"Deny"}),
        ("sandbox", "rg-untagged", False, True, {"Audit"}),
        ("online", "vm-untagged", True, True, {"Modify"}),
        ("sandbox", "vm-us-region", False, False, {"Deny"}),
        ("online", "storage-public", True, False, {"Modify", "Deny"}),
        ("sandbox", "storage-public", True, True, {"Modify", "Audit"}),
        ("corp", "public-ip", False, False, {"Deny"}),
        ("online", "public-ip", False, True, set()),
        ("online", "rg-tagged", False, True, set()),
    ],
)
def test_the_examples(group, example, with_group, allowed, effects):
    resource = json.loads((EXAMPLES / f"{example}.json").read_text())
    resource_group = json.loads((EXAMPLES / "rg-tagged.json").read_text()) if with_group else None
    decision = decide(group, resource, resource_group)
    assert (decision.allowed, decision.effects) == (allowed, effects)
