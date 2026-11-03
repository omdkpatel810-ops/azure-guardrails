"""The evaluator's own pieces: expressions, fields, operators and the command line."""

from __future__ import annotations

import json

import pytest

from tools.policy_eval import Context, PolicyError, ancestors, apply_modify, check, evaluate_definition, get_field, main, resolve

CTX = Context(
    parameters={"tagName": "owner", "regions": ["canadacentral"], "more": ["canadaeast"]},
    resource={
        "type": "Microsoft.Web/sites",
        "name": "app",
        "location": "canadacentral",
        "tags": {"Owner": "om"},
        "kind": "app",
        "identity": {"type": "SystemAssigned"},
        "properties": {"siteConfig": {"minTlsVersion": "1.2"}, "httpsOnly": True},
    },
    resource_group={"name": "rg-app", "tags": {"owner": "om", "it's": "quoted"}},
)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("[parameters('tagName')]", "owner"),
        ("[concat('tags[', parameters('tagName'), ']')]", "tags[owner]"),
        ("[concat(parameters('regions'), parameters('more'))]", ["canadacentral", "canadaeast"]),
        ("[resourceGroup().tags[parameters('tagName')]]", "om"),
        ("[resourceGroup().tags['missing']]", None),
        ("[resourceGroup().name]", "rg-app"),
        ("[resourceGroup().tags['it''s']]", "quoted"),
        ("[field('location')]", "canadacentral"),
        ("[toLower('ABC')]", "abc"),
        ("[toUpper(field('name'))]", "APP"),
        ("[empty(resourceGroup().tags['missing'])]", True),
        ("[empty(field('name'))]", False),
        ("[parameters('regions')[0]]", "canadacentral"),
        ("[parameters('regions')[5]]", None),
        ("[concat('a', resourceGroup().tags['missing'], 'b')]", "ab"),
        ("[[not an expression]", "[not an expression]"),
        ("plain text", "plain text"),
        (["[parameters('tagName')]", 3], ["owner", 3]),
        ({"x": "[parameters('tagName')]"}, {"x": "owner"}),
    ],
)
def test_expressions(expression, expected):
    assert resolve(expression, CTX) == expected


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("[subscription().id]", "subscription\\(\\) isn't supported"),
        ("[parameters('nope')]", "no value for parameter 'nope'"),
        ("[concat('a' 'b')]", "expected"),
        ("[concat('a'))]", "unexpected"),
        ("[,]", "unexpected"),
        ("[concat('a']", "expected"),
        ("[concat('a', ;)]", "can't read"),
    ],
)
def test_bad_expressions_raise(expression, message):
    with pytest.raises(PolicyError, match=message):
        resolve(expression, CTX)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tags[owner]", "om"),
        ("tags['owner']", "om"),
        ("tags.owner", "om"),
        ("tags", {"Owner": "om"}),
        ("fullName", "app"),
        ("kind", "app"),
        ("identity.type", "SystemAssigned"),
        ("Microsoft.Web/sites/siteConfig.minTlsVersion", "1.2"),
        ("Microsoft.Web/sites/httpsOnly", True),
        ("Microsoft.Web/sites/missing", None),
        ("Microsoft.Storage/storageAccounts/minimumTlsVersion", None),  # another type's alias
    ],
)
def test_fields(path, expected):
    assert get_field(CTX.resource, path) == expected


def test_unknown_fields_raise():
    with pytest.raises(PolicyError, match="field 'sku' isn't supported"):
        get_field(CTX.resource, "sku")


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"field": "location", "equals": "CANADACENTRAL"}, True),
        ({"field": "Microsoft.Web/sites/httpsOnly", "equals": "true"}, True),
        ({"field": "tags", "equals": {"Owner": "om"}}, True),
        ({"field": "location", "in": ["canadacentral", "canadaeast"]}, True),
        ({"field": "tags[missing]", "in": [""]}, False),
        ({"field": "tags[missing]", "notIn": ["x"]}, True),
        ({"field": "name", "like": "a*"}, True),
        ({"field": "tags[missing]", "like": "*"}, False),
        ({"field": "name", "notLike": "b*"}, True),
        ({"field": "tags[missing]", "notLike": "*"}, True),
        ({"field": "name", "contains": "P"}, True),
        ({"field": "tags[missing]", "contains": ""}, False),
        ({"field": "name", "notContains": "z"}, True),
        ({"field": "tags[missing]", "notContains": "z"}, True),
        ({"field": "tags", "containsKey": "owner"}, True),
        ({"field": "tags", "notContainsKey": "cost-center"}, True),
        ({"field": "name", "containsKey": "a"}, False),
        ({"value": "[parameters('regions')]", "contains": "canadacentral"}, True),
        ({"field": "tags[owner]", "exists": True}, True),
        ({"field": "tags[missing]", "exists": "false"}, True),
        ({"not": {"field": "location", "equals": "eastus"}}, True),
        ({"anyOf": [{"field": "name", "equals": "x"}, {"field": "name", "equals": "app"}]}, True),
        ({"allOf": [{"field": "name", "equals": "app"}, {"field": "kind", "equals": "x"}]}, False),
    ],
)
def test_conditions(condition, expected):
    assert check(condition, CTX) is expected


@pytest.mark.parametrize(
    ("condition", "message"),
    [
        ({"field": "name", "greaterOrEquals": "a"}, "operator 'greaterOrEquals' isn't supported"),
        ({"field": "name"}, "exactly one operator"),
        ({"field": "name", "equals": "a", "notEquals": "b"}, "exactly one operator"),
        ({"count": {"field": "x[*]"}, "greater": 0}, "needs field or value"),
    ],
)
def test_bad_conditions_raise(condition, message):
    with pytest.raises(PolicyError, match=message):
        check(condition, CTX)


def test_ancestors():
    assert ancestors("corp") == ["root", "landing-zones", "corp"]
    assert ancestors("root") == ["root"]
    with pytest.raises(PolicyError, match="unknown management group 'prod'"):
        ancestors("prod")


def test_the_command_line(tmp_path, capsys):
    resource = tmp_path / "storage.json"
    resource.write_text(
        json.dumps(
            {
                "type": "Microsoft.Storage/storageAccounts",
                "name": "st",
                "location": "canadacentral",
                "properties": {"allowBlobPublicAccess": True, "supportsHttpsTrafficOnly": True, "minimumTlsVersion": "TLS1_2"},
            }
        )
    )
    group = tmp_path / "rg.json"
    group.write_text(json.dumps({"name": "rg", "location": "canadacentral", "tags": {"owner": "om@example.com"}}))

    assert main(["online", str(resource), "--resource-group", str(group)]) == 1
    out = capsys.readouterr().out
    assert "Modify   landing-zones/gr-baseline" in out
    assert "Deny     landing-zones/gr-baseline    deny-storage-public-access (no-public-blobs)" in out
    assert 'tags after Modify: {"owner": "om@example.com"}' in out
    assert out.rstrip().endswith("DENIED")

    assert main(["sandbox", str(resource)]) == 0
    out = capsys.readouterr().out
    assert "Audit    sandbox/gr-baseline-audit" in out and out.rstrip().endswith("ALLOWED")


def modify_definition(*operations, effect="Modify"):
    return {
        "name": "test-modify",
        "properties": {
            "mode": "Indexed",
            "parameters": {},
            "policyRule": {
                "if": {"field": "type", "equals": "Microsoft.Web/sites"},
                "then": {"effect": effect, "details": {"roleDefinitionIds": [], "operations": list(operations)}},
            },
        },
    }


def test_modify_operations():
    definition = modify_definition(
        {"operation": "addOrReplace", "field": "tags['Owner']", "value": "[resourceGroup().tags['owner']]"},
        {"operation": "add", "field": "tags.team", "value": "web"},
        {"operation": "remove", "field": "tags[stale]"},
        {"operation": "add", "field": "tags[owner]", "value": "ignored"},  # add keeps an existing tag
    )
    resource = {**CTX.resource, "tags": {"Owner": "someone-else", "stale": "yes"}}
    assert apply_modify(definition, resource, {}, CTX.resource_group)["tags"] == {"Owner": "om", "team": "web"}


def test_modify_only_changes_tags():
    definition = modify_definition({"operation": "add", "field": "Microsoft.Web/sites/httpsOnly", "value": True})
    with pytest.raises(PolicyError, match="only tags"):
        apply_modify(definition, CTX.resource, {})


def test_unknown_effects_raise():
    with pytest.raises(PolicyError, match="unknown effect"):
        evaluate_definition(modify_definition(effect="Block"), CTX.resource)
    assert evaluate_definition(modify_definition(effect="modify"), CTX.resource) == "Modify"  # effects ignore case
