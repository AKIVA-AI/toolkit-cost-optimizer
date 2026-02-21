from __future__ import annotations

from dataclasses import dataclass
from typing import Any

REQUIRED_FIELDS: tuple[str, ...] = (
    "schema_version",
    "created_ts",
    "model",
    "latency_ms",
    "cost_usd",
    "success",
)


@dataclass(frozen=True)
class SchemaIssue:
    kind: str
    field: str
    message: str
    count: int = 1


def validate_inference_event(row: dict[str, Any]) -> list[SchemaIssue]:
    issues: list[SchemaIssue] = []
    for f in REQUIRED_FIELDS:
        if f not in row:
            issues.append(SchemaIssue(kind="missing", field=f, message="required"))

    if "schema_version" in row and int(row.get("schema_version") or 0) != 1:
        issues.append(SchemaIssue(kind="invalid", field="schema_version", message="must_be_1"))

    if "model" in row and not isinstance(row.get("model"), str):
        issues.append(SchemaIssue(kind="type", field="model", message="expected_string"))

    for f in ["latency_ms", "cost_usd", "created_ts"]:
        if f in row:
            v = row.get(f)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                issues.append(SchemaIssue(kind="type", field=f, message="expected_number"))
            elif float(v) < 0:
                issues.append(
                    SchemaIssue(kind="constraint", field=f, message="must_be_non_negative")
                )
        # handled above

    if "success" in row and not isinstance(row.get("success"), bool):
        issues.append(SchemaIssue(kind="type", field="success", message="expected_bool"))

    for f in ["tokens_in", "tokens_out"]:
        if f in row:
            v = row.get(f)
            if not isinstance(v, int) or isinstance(v, bool):
                issues.append(SchemaIssue(kind="type", field=f, message="expected_int"))
            elif v < 0:
                issues.append(
                    SchemaIssue(kind="constraint", field=f, message="must_be_non_negative")
                )

    return issues
