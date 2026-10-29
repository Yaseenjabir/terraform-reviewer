"""Check the Terraform names an LLM writes against the provider schema.

`terraform providers schema -json` (run after `terraform init`) describes every resource type of
every provider in the configuration. The evaluation suite found that LLM fixes sometimes name
attributes that don't exist (e.g. `versioning { enabled = true }`, removed from the AWS
provider), and that listing the valid names in the prompt didn't stop it. So the reviewer checks
each fix with NameChecker instead, and asks the LLM to rewrite fixes that fail (see llm.py).
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path

_NAME = re.compile(r"[a-z][a-z0-9_]*")
_CODE = re.compile(r"`([^`]+)`")
_ASSIGNED = re.compile(r"\b([a-z][a-z0-9_]*)\s*(?:=(?!=)|\{)")

# Terraform language words that may appear in a fix but are not provider attributes.
LANGUAGE = frozenset(
    {
        "lifecycle", "prevent_destroy", "create_before_destroy", "ignore_changes", "replace_triggered_by",
        "moved", "removed", "import", "from", "to", "count", "for_each", "depends_on", "provider",
        "resource", "data", "variable", "locals", "module", "terraform", "dynamic", "content",
        "true", "false", "null", "each", "var", "local", "self", "jsonencode",
    }
)


def named_attributes(text: str) -> list[str]:
    """Attribute/block names written in backticks: `name = ...`, `name { ... }` or `name`."""
    names = []
    for span in _CODE.findall(text):
        span = span.strip()
        if _NAME.fullmatch(span):
            names.append(span)
        else:
            names += _ASSIGNED.findall(span)
    return names


class SchemaError(ValueError):
    """The file isn't the output of `terraform providers schema -json`."""


def _type_names(type_: object) -> set[str]:
    """Keys of object types, at any depth: ["set", ["object", {"cidr_blocks": ...}]]."""
    if not isinstance(type_, list) or len(type_) != 2:
        return set()
    kind, inner = type_
    if kind == "object" and isinstance(inner, dict):
        return set(inner) | {name for t in inner.values() for name in _type_names(t)}
    return _type_names(inner)


def block_names(block: dict) -> set[str]:
    """Every attribute and nested block name in a schema block, at any depth."""
    names = set()
    # Some blocks, such as aws_security_group's ingress, are declared as attributes of object type.
    for name, attribute in (block.get("attributes") or {}).items():
        names |= {name} | _type_names(attribute.get("type"))
    for name, nested in (block.get("block_types") or {}).items():
        names.add(name)
        names |= block_names(nested.get("block") or {})
    return {name for name in names if _NAME.fullmatch(name)}


def resource_schemas(data: dict) -> dict[str, dict]:
    """Every resource type's schema, from all providers in a `providers schema -json` output."""
    if not isinstance(data, dict) or not isinstance(data.get("provider_schemas"), dict):
        raise SchemaError("not a provider schema: expected the output of `terraform providers schema -json`")
    resources = {}
    for provider in data["provider_schemas"].values():
        resources.update(provider.get("resource_schemas") or {})
    return resources


def attribute_names(data: dict, types: Iterable[str]) -> dict[str, list[str]]:
    """The attribute names of each of the given resource types that the schema knows."""
    resources = resource_schemas(data)
    return {t: sorted(block_names(resources[t]["block"])) for t in sorted(set(types)) if t in resources}


class NameChecker:
    """Finds names in a text that aren't attributes of a resource type.

    A name is accepted if it is an attribute or block of the finding's resource type or of a
    resource type the text mentions (a fix may suggest another resource, e.g. an
    aws_s3_bucket_public_access_block), a resource type, a Terraform language word, or a label
    (a resource name from the plan, such as `orders` in aws_dynamodb_table.orders).
    """

    def __init__(self, resource_types: Iterable[str], attributes: Mapping[str, Collection[str]]) -> None:
        self.resource_types = frozenset(resource_types)
        self.attributes = attributes

    @classmethod
    def from_schema(cls, data: dict) -> NameChecker:
        """From a `terraform providers schema -json` output."""
        resources = resource_schemas(data)
        return cls(resources, attribute_names(data, resources))

    def unknown(self, text: str, resource_type: str, labels: Collection[str] = ()) -> list[str]:
        """The names in the text that don't exist (empty if the resource type isn't known)."""
        if resource_type not in self.attributes:
            return []
        mentioned = {t for t in self.attributes if re.search(rf"\b{t}\b", text)} | {resource_type}
        known = LANGUAGE | self.resource_types | set(labels)
        for t in mentioned:
            known |= set(self.attributes[t])
        return sorted({name for name in named_attributes(text) if name not in known})


def load_schema(path: str | Path) -> dict:
    """Read a `terraform providers schema -json` file."""
    with open(path, encoding="utf-8-sig") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise SchemaError(f"{path} is not valid JSON: {e}") from e
    resource_schemas(data)  # check the shape
    return data
