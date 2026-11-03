"""A small Azure Policy evaluator, for testing this repo's rules without an Azure account.

It reads the same JSON files Terraform deploys (policies/definitions, policies/initiatives and
policies/assignments.json) and answers: if this resource were created in this management group,
what would Azure Policy do?

    python tools/policy_eval.py online examples/public-storage.json
    python tools/policy_eval.py sandbox examples/untagged-vm.json --resource-group examples/rg.json

It covers the parts of the policy language these rules use: allOf, anyOf and not; field and value
conditions; equals, notEquals, in, notIn, exists, like, notLike, contains, notContains; and the
template functions parameters(), concat(), resourceGroup(), field(), toLower(), toUpper() and
empty(). Unknown operators and functions raise an error rather than guess.

Where it has to model Azure's behaviour, it follows Microsoft's documentation and built-in policies:
- String comparisons and tag names ignore case, and true/false compare equal to "true"/"false".
- A field that doesn't exist matches notIn, notLike, notContains and exists: false.
- In equals and notEquals, a missing value compares as an empty string. So a missing
  allowBlobPublicAccess is notEquals "false", and a resource group with no owner tag gives
  resourceGroup().tags['owner'] notEquals "" as false: the pattern Microsoft's own "Inherit a tag
  from the resource group if missing" policy relies on. The tag rules here check exists and
  equals "" together, so they don't depend on it.
- Indexed mode skips resource groups and subscriptions.
- Modify runs before Deny and Audit, so a tag that would be inherited counts as present.
- An alias such as Microsoft.Storage/storageAccounts/minimumTlsVersion reads
  properties.minimumTlsVersion. Real aliases come from Azure's alias list; the ones used here
  follow that pattern.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = ROOT / "policies"

# Where each management group sits, matching modules/management-groups/main.tf.
PARENTS: dict[str, str | None] = {
    "root": None,
    "platform": "root",
    "landing-zones": "root",
    "corp": "landing-zones",
    "online": "landing-zones",
    "sandbox": "root",
    "decommissioned": "root",
}

EFFECTS = {
    e.lower(): e
    for e in ("Deny", "Audit", "Modify", "Disabled", "Append", "AuditIfNotExists", "DeployIfNotExists", "DenyAction", "Manual")
}
UNINDEXED_TYPES = {"microsoft.resources/subscriptions/resourcegroups", "microsoft.resources/subscriptions"}
TOP_LEVEL_FIELDS = {"name", "fullname", "type", "location", "kind", "id", "tags", "identity.type"}
MISSING = None


class PolicyError(ValueError):
    """A rule uses something this evaluator doesn't support, or is malformed."""


# ---------------------------------------------------------------------------------------------
# Template expressions: "[concat('tags[', parameters('tagName'), ']')]" and friends.


@dataclass
class Context:
    parameters: dict[str, Any]
    resource: dict[str, Any]
    resource_group: dict[str, Any]


def is_expression(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("[") and value.endswith("]") and not value.startswith("[[")


def resolve(value: Any, ctx: Context) -> Any:
    """Evaluate a template expression, or return a literal unchanged (lists and dicts recursively)."""
    if is_expression(value):
        return _Parser(value[1:-1], ctx).parse()
    if isinstance(value, str) and value.startswith("[["):
        return value[1:]
    if isinstance(value, list):
        return [resolve(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}
    return value


class _Parser:
    TOKEN = re.compile(r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<num>-?\d+)|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>[(),.\[\]]))")

    def __init__(self, text: str, ctx: Context) -> None:
        self.ctx = ctx
        self.tokens: list[tuple[str, str]] = []
        pos = 0
        text = text.rstrip()
        while pos < len(text):
            match = self.TOKEN.match(text, pos)
            if not match or match.end() == pos:
                raise PolicyError(f"can't read expression at {text[pos:]!r}")
            kind = match.lastgroup
            assert kind is not None
            self.tokens.append((kind, match.group(kind)))
            pos = match.end()
        self.i = 0

    def parse(self) -> Any:
        value = self._expr()
        if self.i != len(self.tokens):
            raise PolicyError(f"unexpected {self.tokens[self.i][1]!r} in expression")
        return value

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _take(self, op: str | None = None) -> tuple[str, str]:
        token = self._peek()
        if token is None or (op is not None and token[1] != op):
            raise PolicyError(f"expected {op!r} in expression")
        self.i += 1
        return token

    def _expr(self) -> Any:
        kind, text = self._take()
        if kind == "str":
            value: Any = text[1:-1].replace("''", "'")
        elif kind == "num":
            value = int(text)
        elif kind == "name":
            self._take("(")
            args = []
            if self._peek() != ("op", ")"):
                args.append(self._expr())
                while self._peek() == ("op", ","):
                    self._take(",")
                    args.append(self._expr())
            self._take(")")
            value = self._call(text, args)
        else:
            raise PolicyError(f"unexpected {text!r} in expression")
        while (token := self._peek()) in (("op", "."), ("op", "[")):
            if token[1] == ".":
                self._take(".")
                value = _index(value, self._take()[1])
            else:
                self._take("[")
                key = self._expr()
                self._take("]")
                value = _index(value, key)
        return value

    def _call(self, name: str, args: list[Any]) -> Any:
        fn = name.lower()
        if fn == "parameters":
            (param,) = args
            if param not in self.ctx.parameters:
                raise PolicyError(f"no value for parameter {param!r}")
            return self.ctx.parameters[param]
        if fn == "concat":
            if all(isinstance(a, list) for a in args):
                return [item for a in args for item in a]
            return "".join("" if a is MISSING else str(a) for a in args)
        if fn == "resourcegroup":
            return self.ctx.resource_group
        if fn == "field":
            (path,) = args
            return get_field(self.ctx.resource, path)
        if fn == "tolower":
            return str(args[0]).lower()
        if fn == "toupper":
            return str(args[0]).upper()
        if fn == "empty":
            return args[0] in (MISSING, "", [], {})
        raise PolicyError(f"function {name}() isn't supported")


def _index(value: Any, key: Any) -> Any:
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).lower() == str(key).lower():
                return v
        return MISSING
    if isinstance(value, list) and isinstance(key, int):
        return value[key] if -len(value) <= key < len(value) else MISSING
    return MISSING


# ---------------------------------------------------------------------------------------------
# Fields: "location", "tags[owner]", "Microsoft.Storage/storageAccounts/minimumTlsVersion".

TAG_FIELD = re.compile(r"^tags(?:\[\s*'?(?P<a>[^'\]]+)'?\s*\]|\.(?P<b>.+))$", re.IGNORECASE)


def get_field(resource: dict[str, Any], path: str) -> Any:
    lowered = path.lower()
    if match := TAG_FIELD.match(path):
        return _index(resource.get("tags") or {}, match.group("a") or match.group("b"))
    if lowered == "fullname":
        return resource.get("name", MISSING)
    if lowered in TOP_LEVEL_FIELDS:
        value: Any = resource
        for part in lowered.split("."):
            value = _index(value, part)
        return value
    resource_type = str(resource.get("type", ""))
    if lowered.startswith(resource_type.lower() + "/"):
        value = resource.get("properties") or {}
        for part in path[len(resource_type) + 1 :].split("."):
            value = _index(value, part)
        return value
    if "/" in path:
        return MISSING  # an alias for another resource type
    raise PolicyError(f"field {path!r} isn't supported")


# ---------------------------------------------------------------------------------------------
# Conditions.


def _text(value: Any) -> str:
    if value is MISSING:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (list, dict)) or isinstance(b, (list, dict)):
        return a == b
    return _text(a).lower() == _text(b).lower()


def _contains(container: Any, item: Any) -> bool:
    if isinstance(container, list):
        return any(_same(c, item) for c in container)
    if isinstance(container, dict):
        return any(str(k).lower() == _text(item).lower() for k in container)
    return _text(item).lower() in _text(container).lower()


def check(condition: dict[str, Any], ctx: Context) -> bool:
    """Whether a policy condition is true for the resource in ctx."""
    if "allOf" in condition:
        return all(check(c, ctx) for c in condition["allOf"])
    if "anyOf" in condition:
        return any(check(c, ctx) for c in condition["anyOf"])
    if "not" in condition:
        return not check(condition["not"], ctx)

    if "field" in condition:
        subject = get_field(ctx.resource, resolve(condition["field"], ctx))
    elif "value" in condition:
        subject = resolve(condition["value"], ctx)
    else:
        raise PolicyError(f"condition needs field or value: {condition}")

    operators = [k for k in condition if k not in ("field", "value")]
    if len(operators) != 1:
        raise PolicyError(f"condition needs exactly one operator: {condition}")
    op = operators[0]
    target = resolve(condition[op], ctx)

    if op == "equals":
        return _same(subject, target)
    if op == "notEquals":
        return not _same(subject, target)
    if op == "in":
        return subject is not MISSING and _contains(target, subject)
    if op == "notIn":
        return subject is MISSING or not _contains(target, subject)
    if op == "exists":
        wanted = _text(target).lower() == "true"
        return (subject is not MISSING) == wanted
    if op == "like":
        return subject is not MISSING and fnmatch.fnmatchcase(_text(subject).lower(), _text(target).lower())
    if op == "notLike":
        return subject is MISSING or not fnmatch.fnmatchcase(_text(subject).lower(), _text(target).lower())
    if op == "contains":
        return subject is not MISSING and _contains(subject, target)
    if op == "notContains":
        return subject is MISSING or not _contains(subject, target)
    if op == "containsKey":
        return isinstance(subject, dict) and _contains(subject, target)
    if op == "notContainsKey":
        return not (isinstance(subject, dict) and _contains(subject, target))
    raise PolicyError(f"operator {op!r} isn't supported")


# ---------------------------------------------------------------------------------------------
# Definitions, initiatives and assignments.


def load(policy_dir: Path = POLICY_DIR) -> tuple[dict, dict, list]:
    definitions = {p.stem: json.loads(p.read_text()) for p in sorted((policy_dir / "definitions").glob("*.json"))}
    initiatives = {p.stem: json.loads(p.read_text()) for p in sorted((policy_dir / "initiatives").glob("*.json"))}
    assignments = json.loads((policy_dir / "assignments.json").read_text())
    return definitions, initiatives, assignments


def parameter_values(declared: dict[str, Any], given: dict[str, Any], where: str) -> dict[str, Any]:
    values = {}
    for name, spec in declared.items():
        if name in given:
            value = given[name]
        elif "defaultValue" in spec:
            value = spec["defaultValue"]
        else:
            raise PolicyError(f"{where}: no value for parameter {name!r}")
        allowed = spec.get("allowedValues")
        if allowed is not None and not any(_same(value, a) for a in allowed):
            raise PolicyError(f"{where}: {value!r} isn't an allowed value of {name!r} ({allowed})")
        values[name] = value
    unknown = set(given) - set(declared)
    if unknown:
        raise PolicyError(f"{where}: unknown parameters {sorted(unknown)}")
    return values


@dataclass
class Result:
    assignment: str  # "<group key>/<assignment name>"
    rule: str  # the definition file, plus the initiative reference ID if any
    effect: str
    definition: dict[str, Any]
    parameters: dict[str, Any]


def evaluate_definition(
    definition: dict[str, Any],
    resource: dict[str, Any],
    parameters: dict[str, Any] | None = None,
    resource_group: dict[str, Any] | None = None,
) -> str | None:
    """The effect this definition has on the resource, or None if it doesn't apply."""
    props = definition["properties"]
    if props["mode"] == "Indexed" and str(resource.get("type", "")).lower() in UNINDEXED_TYPES:
        return None
    params = parameter_values(props.get("parameters", {}), parameters or {}, definition["name"])
    ctx = Context(params, resource, resource_group or {})
    if not check(props["policyRule"]["if"], ctx):
        return None
    effect = EFFECTS.get(str(resolve(props["policyRule"]["then"]["effect"], ctx)).lower())
    if effect is None:
        raise PolicyError(f"{definition['name']}: unknown effect")
    return None if effect == "Disabled" else effect


def apply_modify(
    definition: dict[str, Any],
    resource: dict[str, Any],
    parameters: dict[str, Any],
    resource_group: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The resource after a Modify definition's operations (add, addOrReplace, remove)."""
    props = definition["properties"]
    params = parameter_values(props.get("parameters", {}), parameters, definition["name"])
    ctx = Context(params, resource, resource_group or {})
    changed = copy.deepcopy(resource)
    for operation in props["policyRule"]["then"]["details"]["operations"]:
        field = resolve(operation["field"], ctx)
        match = TAG_FIELD.match(field)
        if not match:
            raise PolicyError(f"Modify on {field!r} isn't supported, only tags")
        tag = match.group("a") or match.group("b")
        tags = changed.setdefault("tags", {})
        kind = operation["operation"]
        if kind == "remove":
            tags.pop(tag, None)
        elif kind == "addOrReplace" or (kind == "add" and get_field(changed, field) is MISSING):
            tags[tag] = resolve(operation["value"], ctx)
    return changed


def rules_of(assignment: dict[str, Any], definitions: dict, initiatives: dict) -> list[tuple[str, dict, dict]]:
    """Each (rule name, definition, parameters) an assignment applies."""
    kind, key = assignment["policy"].split(":", 1)
    where = f"{assignment['scope']}/{assignment['name']}"
    if kind == "definition":
        definition = definitions[key]
        params = parameter_values(definition["properties"].get("parameters", {}), assignment.get("parameters", {}), where)
        return [(key, definition, params)]
    initiative = initiatives[key]
    outer = parameter_values(initiative.get("parameters", {}), assignment.get("parameters", {}), where)
    rules = []
    for ref in initiative["policies"]:
        ctx = Context(outer, {}, {})
        params = {name: resolve(v["value"], ctx) for name, v in ref.get("parameters", {}).items()}
        rules.append((f"{ref['definition']} ({ref['referenceId']})", definitions[ref["definition"]], params))
    return rules


def ancestors(group: str) -> list[str]:
    if group not in PARENTS:
        raise PolicyError(f"unknown management group {group!r}; use one of {', '.join(PARENTS)}")
    chain: list[str] = []
    current: str | None = group
    while current is not None:
        chain.insert(0, current)
        current = PARENTS[current]
    return chain


@dataclass
class Decision:
    allowed: bool
    resource: dict[str, Any]  # after any Modify
    results: list[Result]

    @property
    def effects(self) -> set[str]:
        return {r.effect for r in self.results}


def decide(
    group: str,
    resource: dict[str, Any],
    resource_group: dict[str, Any] | None = None,
    policy_dir: Path = POLICY_DIR,
) -> Decision:
    """What Azure Policy does when this resource is created in this management group."""
    definitions, initiatives, assignments = load(policy_dir)
    scopes = ancestors(group)
    applied = [
        (f"{a['scope']}/{a['name']}", rule, definition, params)
        for a in assignments
        if a["scope"] in scopes
        for rule, definition, params in rules_of(a, definitions, initiatives)
    ]

    results: list[Result] = []
    # Modify runs first, so the Deny and Audit rules see the changed request.
    for name, rule, definition, params in applied:
        if evaluate_definition(definition, resource, params, resource_group) == "Modify":
            results.append(Result(name, rule, "Modify", definition, params))
            resource = apply_modify(definition, resource, params, resource_group)
    for name, rule, definition, params in applied:
        effect = evaluate_definition(definition, resource, params, resource_group)
        if effect not in (None, "Modify"):
            results.append(Result(name, rule, effect, definition, params))
    allowed = not any(r.effect == "Deny" for r in results)
    return Decision(allowed, resource, results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="What would Azure Policy do with this resource in this management group?")
    parser.add_argument("group", choices=list(PARENTS), help="management group key")
    parser.add_argument("resource", type=Path, help="resource as JSON: type, name, location, tags, properties")
    parser.add_argument("--resource-group", type=Path, help="its resource group as JSON: name, location, tags")
    args = parser.parse_args(argv)

    resource = json.loads(args.resource.read_text())
    resource_group = json.loads(args.resource_group.read_text()) if args.resource_group else None
    decision = decide(args.group, resource, resource_group)

    for r in decision.results:
        print(f"{r.effect:8} {r.assignment:28} {r.rule}")
    if decision.resource.get("tags") != resource.get("tags"):
        print(f"tags after Modify: {json.dumps(decision.resource.get('tags'))}")
    print("ALLOWED" if decision.allowed else "DENIED")
    return 0 if decision.allowed else 1


if __name__ == "__main__":
    sys.exit(main())
