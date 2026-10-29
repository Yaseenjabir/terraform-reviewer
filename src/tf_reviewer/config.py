"""Settings, from a team's `.tf-reviewer.yml` file or the built-in defaults.

Lists can be extended or trimmed with `add` and `remove`, so teams don't have to copy the
defaults to change one item. Unknown settings and invalid values are errors rather than
being ignored: a misspelt setting that silently does nothing is dangerous in a safety tool.

Example:

    cost:
      threshold: 100
    rules:
      disable: [resource-removed]
    stateful_types:
      add: [aws_memorydb_cluster]
      remove: [aws_sqs_queue]
    sensitive_ports:
      add: {8080: Jenkins}
      remove: [9200]
    web_ports:
      add: [8443]
    checkov:
      skip: [CKV_AWS_16]
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

CONFIG_FILE_NAME = ".tf-reviewer.yml"

RULE_IDS = frozenset(
    {
        "data-loss",
        "resource-removed",
        "open-to-internet",
        "iam-wildcard",
        "admin-policy-attached",
        "cost-increase",
        "checkov",
    }
)

# Resource types that store data. Deleting or replacing one loses that data unless it's backed up.
DEFAULT_STATEFUL_TYPES = frozenset(
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

# Ports that should never be reachable from the whole internet.
DEFAULT_SENSITIVE_PORTS: Mapping[int, str] = MappingProxyType(
    {
        22: "SSH",
        3389: "RDP",
        3306: "MySQL",
        5432: "PostgreSQL",
        1433: "SQL Server",
        27017: "MongoDB",
        6379: "Redis",
        9200: "Elasticsearch",
    }
)

# Ports meant to be public (websites).
DEFAULT_WEB_PORTS = frozenset({80, 443})

DEFAULT_COST_THRESHOLD = Decimal(50)


class ConfigError(ValueError):
    """The config file is invalid."""


@dataclass(frozen=True)
class Config:
    cost_threshold: Decimal = DEFAULT_COST_THRESHOLD
    disabled_rules: frozenset[str] = frozenset()
    stateful_types: frozenset[str] = DEFAULT_STATEFUL_TYPES
    sensitive_ports: Mapping[int, str] = DEFAULT_SENSITIVE_PORTS
    web_ports: frozenset[int] = DEFAULT_WEB_PORTS
    skipped_checkov_checks: frozenset[str] = frozenset()


DEFAULT_CONFIG = Config()

_SECTIONS = {
    "version": None,
    "cost": {"threshold"},
    "rules": {"disable"},
    "stateful_types": {"add", "remove"},
    "sensitive_ports": {"add", "remove"},
    "web_ports": {"add", "remove"},
    "checkov": {"skip"},
}


def _section(data: dict, name: str) -> dict:
    value = data.get(name)
    if value is None:  # missing, or written as an empty `name:` line
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"'{name}' must be a mapping")
    unknown = set(value) - _SECTIONS[name]
    if unknown:
        raise ConfigError(f"unknown setting '{name}.{sorted(unknown)[0]}' (allowed: {', '.join(sorted(_SECTIONS[name]))})")
    return value


def _strings(value: Any, where: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ConfigError(f"'{where}' must be a list of names")
    return frozenset(value)


def _port(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).isdigit():
        raise ConfigError(f"'{where}': {value!r} is not a port number")
    port = int(value)
    if not 1 <= port <= 65535:
        raise ConfigError(f"'{where}': {port} is not a port number (1-65535)")
    return port


def _ports(value: Any, where: str) -> frozenset[int]:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise ConfigError(f"'{where}' must be a list of port numbers")
    return frozenset(_port(item, where) for item in value)


def parse_config(data: Any) -> Config:
    """Turn the decoded contents of a config file into a Config."""
    if data is None:
        return DEFAULT_CONFIG
    if not isinstance(data, dict):
        raise ConfigError("the config file must be a mapping of settings")
    unknown = set(data) - _SECTIONS.keys()
    if unknown:
        raise ConfigError(f"unknown setting '{sorted(unknown)[0]}' (allowed: {', '.join(sorted(_SECTIONS))})")
    if data.get("version", 1) != 1:
        raise ConfigError(f"unsupported config version {data['version']!r} (supported: 1)")

    config = {}

    threshold = _section(data, "cost").get("threshold")
    if threshold is not None:
        if isinstance(threshold, bool):
            raise ConfigError(f"'cost.threshold': {threshold!r} must be a non-negative amount")
        try:
            amount = Decimal(str(threshold))
        except InvalidOperation:
            raise ConfigError(f"'cost.threshold': {threshold!r} is not an amount") from None
        if amount < 0:
            raise ConfigError(f"'cost.threshold': {threshold!r} must be a non-negative amount")
        config["cost_threshold"] = amount

    disabled = _strings(_section(data, "rules").get("disable"), "rules.disable")
    unknown_rules = disabled - RULE_IDS
    if unknown_rules:
        raise ConfigError(
            f"'rules.disable': unknown rule '{sorted(unknown_rules)[0]}' (rules: {', '.join(sorted(RULE_IDS))})"
        )
    config["disabled_rules"] = disabled

    stateful = _section(data, "stateful_types")
    config["stateful_types"] = (
        DEFAULT_STATEFUL_TYPES | _strings(stateful.get("add"), "stateful_types.add")
    ) - _strings(stateful.get("remove"), "stateful_types.remove")

    sensitive = _section(data, "sensitive_ports")
    ports = dict(DEFAULT_SENSITIVE_PORTS)
    added = sensitive.get("add") or {}
    if not isinstance(added, dict):
        raise ConfigError("'sensitive_ports.add' must map port numbers to names, e.g. {8080: Jenkins}")
    for port, name in added.items():
        if not isinstance(name, str) or not name:
            raise ConfigError(f"'sensitive_ports.add': port {port} needs a name")
        ports[_port(port, "sensitive_ports.add")] = name
    for port in _ports(sensitive.get("remove"), "sensitive_ports.remove"):
        ports.pop(port, None)
    config["sensitive_ports"] = MappingProxyType(ports)

    web = _section(data, "web_ports")
    config["web_ports"] = (DEFAULT_WEB_PORTS | _ports(web.get("add"), "web_ports.add")) - _ports(
        web.get("remove"), "web_ports.remove"
    )

    config["skipped_checkov_checks"] = _strings(_section(data, "checkov").get("skip"), "checkov.skip")

    return Config(**config)


def load_config(path: str | Path) -> Config:
    """Read and validate a config file."""
    with open(path, encoding="utf-8-sig") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"{path} is not valid YAML: {e}") from e
    try:
        return parse_config(data)
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from None


def find_config(directory: str | Path = ".") -> Path | None:
    """The `.tf-reviewer.yml` in a directory, if there is one."""
    path = Path(directory) / CONFIG_FILE_NAME
    return path if path.is_file() else None
