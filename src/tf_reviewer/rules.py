"""Rules that look at each resource change and report risky ones as findings.

Rules are plain functions: they take one ResourceChange and return a list of Findings
(empty if the change is fine). Every finding names the rule that produced it, so each
risk level can be traced back to a rule.
"""

from __future__ import annotations

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


RULES: tuple[Rule, ...] = (data_loss, resource_removed)


def evaluate(changes: Iterable[ResourceChange], rules: Iterable[Rule] = RULES) -> list[Finding]:
    """Run every rule on every change. Riskiest findings first, then by address."""
    rules = tuple(rules)
    findings = [finding for change in changes for rule in rules for finding in rule(change)]
    return sorted(findings, key=lambda f: (-f.risk, f.address, f.rule_id))


def overall_risk(findings: Iterable[Finding]) -> Risk | None:
    """The highest risk among the findings, or None if there are none."""
    return max((f.risk for f in findings), default=None)
