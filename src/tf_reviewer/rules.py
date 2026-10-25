"""Rules that look at each resource change and report risky ones as findings.

Rules are plain functions: they take one ResourceChange and return a list of Findings
(empty if the change is fine). Every finding names the rule that produced it, so each
risk level can be traced back to a rule.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import IntEnum

from tf_reviewer.plan import Action, ResourceChange, format_path


class Risk(IntEnum):
    """How risky a finding is. Higher numbers are riskier, so risks can be compared and sorted."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass(frozen=True)
class Finding:
    """One risky change, as reported by one rule."""

    rule_id: str
    risk: Risk
    address: str
    message: str


Rule = Callable[[ResourceChange], list[Finding]]

# Resource types that store data. Deleting or replacing one loses that data unless it's backed up.
STATEFUL_TYPES = frozenset(
    {
        "aws_db_instance",
        "aws_rds_cluster",
        "aws_docdb_cluster",
        "aws_neptune_cluster",
        "aws_redshift_cluster",
        "aws_dynamodb_table",
        "aws_s3_bucket",
        "aws_ebs_volume",
        "aws_efs_file_system",
        "aws_fsx_lustre_file_system",
        "aws_elasticache_cluster",
        "aws_elasticache_replication_group",
        "aws_opensearch_domain",
        "aws_elasticsearch_domain",
        "aws_kinesis_stream",
        "aws_sqs_queue",
        "aws_secretsmanager_secret",
        "aws_kms_key",
    }
)

_REMOVING = (Action.DELETE, Action.REPLACE)


def _what_happens(change: ResourceChange) -> str:
    """'will be deleted' or 'will be replaced (forced by: bucket)'."""
    if change.action is Action.DELETE:
        return "will be deleted"
    if change.replace_paths:
        return f"will be replaced (forced by: {', '.join(format_path(p) for p in change.replace_paths)})"
    return "will be replaced"


def data_loss(change: ResourceChange) -> list[Finding]:
    """A resource that stores data is deleted or replaced."""
    if change.action not in _REMOVING or change.type not in STATEFUL_TYPES:
        return []
    return [
        Finding(
            rule_id="data-loss",
            risk=Risk.HIGH,
            address=change.address,
            message=f"{change.address} {_what_happens(change)}. It stores data, which is lost unless it's backed up.",
        )
    ]


def resource_removed(change: ResourceChange) -> list[Finding]:
    """Any other resource is deleted or replaced."""
    if change.action not in _REMOVING or change.type in STATEFUL_TYPES:
        return []
    return [
        Finding(
            rule_id="resource-removed",
            risk=Risk.MEDIUM,
            address=change.address,
            message=f"{change.address} {_what_happens(change)}.",
        )
    ]


# --- Ports opened to the internet -------------------------------------------------------

WORLD = frozenset({"0.0.0.0/0", "::/0"})

# Ports that should never be reachable from the whole internet.
SENSITIVE_PORTS = {
    22: "SSH",
    3389: "RDP",
    3306: "MySQL",
    5432: "PostgreSQL",
    1433: "SQL Server",
    27017: "MongoDB",
    6379: "Redis",
    9200: "Elasticsearch",
}

# Ports meant to be public (websites).
WEB_PORTS = frozenset({80, 443})

_ALL_PROTOCOLS = frozenset({"-1", "all"})
_PORTLESS_PROTOCOLS = frozenset({"icmp", "icmpv6", "1", "58"})


@dataclass(frozen=True)
class _Opening:
    """One inbound rule that lets the whole internet in."""

    protocol: str
    from_port: int | None
    to_port: int | None
    cidr: str


def _inbound_rules(resource_type: str, values: dict | None) -> list[tuple[dict, str]]:
    """The inbound rules in a resource's values, each with the key that holds its protocol.

    Security group rules come in three Terraform styles, and all of them must be checked.
    """
    if not values:
        return []
    if resource_type == "aws_security_group":
        return [(rule, "protocol") for rule in values.get("ingress") or []]
    if resource_type == "aws_security_group_rule" and values.get("type") == "ingress":
        return [(values, "protocol")]
    if resource_type == "aws_vpc_security_group_ingress_rule":
        return [(values, "ip_protocol")]
    return []


def _openings(resource_type: str, values: dict | None) -> set[_Opening]:
    """Every (protocol, ports, range) that admits traffic from the whole internet."""
    openings = set()
    for rule, protocol_key in _inbound_rules(resource_type, values):
        ranges = [
            *(rule.get("cidr_blocks") or []),
            *(rule.get("ipv6_cidr_blocks") or []),
            rule.get("cidr_ipv4"),
            rule.get("cidr_ipv6"),
        ]
        for cidr in ranges:
            if cidr in WORLD:
                protocol = str(rule.get(protocol_key)).lower()
                openings.add(_Opening(protocol, rule.get("from_port"), rule.get("to_port"), cidr))
    return openings


def _assess(opening: _Opening) -> tuple[Risk, str] | None:
    """How risky an opening is, and what it opens; None if it's harmless."""
    if opening.protocol in _PORTLESS_PROTOCOLS:
        return None
    low, high = opening.from_port, opening.to_port
    if opening.protocol in _ALL_PROTOCOLS or low is None or high is None or (low <= 0 and high >= 65535):
        return Risk.HIGH, "all ports"
    sensitive = [f"{name} (port {port})" for port, name in SENSITIVE_PORTS.items() if low <= port <= high]
    if sensitive:
        return Risk.HIGH, ", ".join(sensitive)
    if low == high and low in WEB_PORTS:
        return None
    return Risk.MEDIUM, f"port {low}" if low == high else f"ports {low}-{high}"


def open_to_internet(change: ResourceChange) -> list[Finding]:
    """A security group rule newly lets the whole internet reach a non-web port.

    Only openings this change adds are reported; ones that already existed are not its fault.
    """
    added = _openings(change.type, change.after) - _openings(change.type, change.before)
    findings = []
    for opening in sorted(added, key=lambda o: (o.from_port or 0, o.cidr, o.protocol)):
        assessed = _assess(opening)
        if assessed is None:
            continue
        risk, what = assessed
        findings.append(
            Finding(
                rule_id="open-to-internet",
                risk=risk,
                address=change.address,
                message=f"{change.address} opens {what} to the whole internet ({opening.cidr}).",
            )
        )
    return findings


# --- IAM policies that allow everything --------------------------------------------------

# Resource types whose "policy" attribute is an IAM permissions policy (a JSON string).
_POLICY_TYPES = frozenset({"aws_iam_policy", "aws_iam_role_policy", "aws_iam_user_policy", "aws_iam_group_policy"})


def _as_list(value: object) -> list:
    """IAM fields can be a single value or a list; always return a list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _policy_documents(resource_type: str, values: dict | None) -> list[str]:
    """The policy JSON strings in a resource's values."""
    if not values:
        return []
    if resource_type in _POLICY_TYPES:
        return [values.get("policy")]
    if resource_type == "aws_iam_role":
        return [block.get("policy") for block in values.get("inline_policy") or []]
    return []


def _wildcard_grants(resource_type: str, values: dict | None) -> set[tuple[str, bool]]:
    """Each wildcard action an Allow statement grants, with whether it applies to every resource."""
    grants = set()
    for document in _policy_documents(resource_type, values):
        if not isinstance(document, str):
            continue  # unknown until apply
        try:
            policy = json.loads(document)
        except json.JSONDecodeError:
            continue
        if not isinstance(policy, dict):
            continue
        for statement in _as_list(policy.get("Statement")):
            if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
                continue
            every_resource = "*" in _as_list(statement.get("Resource"))
            for action in _as_list(statement.get("Action")):
                if action in ("*", "*:*"):
                    grants.add(("*", every_resource))
                elif isinstance(action, str) and action.endswith(":*"):
                    grants.add((action.lower(), every_resource))
    return grants


def iam_wildcard(change: ResourceChange) -> list[Finding]:
    """An IAM policy newly allows every action, or every action of a service.

    Allowing "*" or "iam:*" is HIGH (iam:* lets the holder grant itself anything);
    any other "service:*" is MEDIUM. Only grants this change adds are reported.
    """
    added = _wildcard_grants(change.type, change.after) - _wildcard_grants(change.type, change.before)
    findings = []
    for action, every_resource in sorted(added):
        where = 'on every resource ("*")' if every_resource else "on specific resources"
        if action == "*":
            risk, what = Risk.HIGH, f'every action ("*") {where}'
        elif action == "iam:*":
            risk, what = Risk.HIGH, f'all IAM actions ("iam:*") {where}, so it can grant itself any permission'
        else:
            service = action.split(":")[0]
            risk, what = Risk.MEDIUM, f'all {service} actions ("{action}") {where}'
        findings.append(
            Finding(rule_id="iam-wildcard", risk=risk, address=change.address, message=f"{change.address} allows {what}.")
        )
    return findings


RULES: tuple[Rule, ...] = (data_loss, resource_removed, open_to_internet, iam_wildcard)


def evaluate(changes: Iterable[ResourceChange], rules: Iterable[Rule] = RULES) -> list[Finding]:
    """Run every rule on every change. Riskiest findings first, then by address."""
    rules = tuple(rules)
    findings = [finding for change in changes for rule in rules for finding in rule(change)]
    return sorted(findings, key=lambda f: (-f.risk, f.address, f.rule_id))


def overall_risk(findings: Iterable[Finding]) -> Risk | None:
    """The highest risk among the findings, or None if there are none."""
    return max((f.risk for f in findings), default=None)
