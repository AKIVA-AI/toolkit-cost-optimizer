"""Row schema for LLM request logs (JSONL), versions 1 and 2.

Version 1 is one row per request with logged cost, latency and success all required.
Version 2 is the normalized row that ``toolkit-opt ingest`` writes: only
``schema_version``, ``created_ts`` and ``model`` are required, because gateway spend
logs, tracing spans and provider usage exports each carry a different subset of fields.
Both versions accept the same optional fields, and both accept unknown extra fields.
An optional field set to ``null`` is treated as absent.

Token classes (all optional non-negative ints):

- ``tokens_in``: all input tokens, **including** cached reads and cache writes.
- ``tokens_cache_read``: the part of ``tokens_in`` read from a prompt cache.
- ``tokens_cache_write``: the part of ``tokens_in`` written to a prompt cache.
- ``tokens_cache_write_1h``: the part of ``tokens_cache_write`` written with a 1-hour
  lifetime (Anthropic prices these apart from default 5-minute writes).
- ``tokens_out``: all output tokens, **including** reasoning/thinking tokens.
- ``tokens_reasoning``: the part of ``tokens_out`` spent on reasoning.

A row with ``"aggregate": true`` or ``requests`` > 1 is an aggregate (for example an
hourly usage bucket). It can carry token counts and cost but not per-request fields
(``latency_ms``, ``success``, ``quality``). An aggregate row without ``requests`` stands
for an unknown number of requests (Anthropic's usage report has no request count).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSIONS = (1, 2)

REQUIRED_FIELDS: dict[int, tuple[str, ...]] = {
    1: ("schema_version", "created_ts", "model", "latency_ms", "cost_usd", "success"),
    2: ("schema_version", "created_ts", "model"),
}

NUMBER_FIELDS: tuple[str, ...] = ("created_ts", "latency_ms", "cost_usd")
TOKEN_FIELDS: tuple[str, ...] = (
    "tokens_in",
    "tokens_out",
    "tokens_cache_read",
    "tokens_cache_write",
    "tokens_cache_write_1h",
    "tokens_reasoning",
)
INT_FIELDS: tuple[str, ...] = (*TOKEN_FIELDS, "requests")
STRING_FIELDS: tuple[str, ...] = ("model", "tier", "request_id", "provider", "source")
PER_REQUEST_FIELDS: tuple[str, ...] = ("latency_ms", "success", "quality")

_INT_TEXT = re.compile(r"^[+-]?[0-9]+$")


@dataclass(frozen=True)
class SchemaIssue:
    kind: str
    field: str
    message: str
    count: int = 1


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def coerce_numeric_strings(row: dict[str, Any]) -> int:
    """Convert numeric strings in number/int fields to numbers, in place.

    Only used when the caller passes ``--coerce-numeric-strings``. A string is converted
    only when it parses completely (``"0.5"``, ``" 12 "``); anything else is left for
    validation to reject. Returns the number of values converted.
    """
    converted = 0
    for f in (*NUMBER_FIELDS, "quality"):
        v = row.get(f)
        if isinstance(v, str):
            try:
                num = float(v.strip())
            except ValueError:
                continue
            if math.isfinite(num):
                row[f] = num
                converted += 1
    for f in INT_FIELDS:
        v = row.get(f)
        if isinstance(v, str) and _INT_TEXT.match(v.strip()):
            row[f] = int(v.strip())
            converted += 1
    return converted


def validate_inference_event(
    row: dict[str, Any], require: tuple[str, ...] = ()
) -> list[SchemaIssue]:
    """Return every schema problem in ``row`` (empty list when the row is valid).

    ``require`` names extra fields a particular command needs on every row (for example
    ``recommend`` needs ``latency_ms``, ``success`` and ``cost_usd`` even on v2 rows).
    """
    issues: list[SchemaIssue] = []

    version = row.get("schema_version")
    if "schema_version" not in row:
        issues.append(SchemaIssue(kind="missing", field="schema_version", message="required"))
        required: tuple[str, ...] = REQUIRED_FIELDS[1]
    elif not _is_int(version) or version not in SCHEMA_VERSIONS:
        issues.append(SchemaIssue(kind="invalid", field="schema_version", message="must_be_1_or_2"))
        required = REQUIRED_FIELDS[1]
    else:
        required = REQUIRED_FIELDS[version]

    needed = tuple(dict.fromkeys((*required, *require)))
    for f in needed:
        if f != "schema_version" and f not in row:
            issues.append(SchemaIssue(kind="missing", field=f, message="required"))
    # An optional field set to null is treated as absent; a required one is a type error.
    row = {k: v for k, v in row.items() if v is not None or k in needed}

    for f in STRING_FIELDS:
        if f in row:
            v = row[f]
            if not isinstance(v, str):
                issues.append(SchemaIssue(kind="type", field=f, message="expected_string"))
            elif not v.strip():
                issues.append(SchemaIssue(kind="constraint", field=f, message="must_be_non_empty"))

    for f in NUMBER_FIELDS:
        if f in row:
            v = row[f]
            if not _is_number(v):
                issues.append(SchemaIssue(kind="type", field=f, message="expected_number"))
            elif not math.isfinite(v):
                issues.append(SchemaIssue(kind="constraint", field=f, message="must_be_finite"))
            elif v < 0:
                issues.append(
                    SchemaIssue(kind="constraint", field=f, message="must_be_non_negative")
                )

    for f in ("success", "aggregate"):
        if f in row and not isinstance(row[f], bool):
            issues.append(SchemaIssue(kind="type", field=f, message="expected_bool"))

    if "quality" in row:
        q = row["quality"]
        if not _is_number(q):
            issues.append(SchemaIssue(kind="type", field="quality", message="expected_number"))
        elif not (math.isfinite(q) and 0.0 <= q <= 1.0):
            issues.append(
                SchemaIssue(kind="constraint", field="quality", message="must_be_between_0_and_1")
            )

    for f in INT_FIELDS:
        if f in row:
            v = row[f]
            if not _is_int(v):
                issues.append(SchemaIssue(kind="type", field=f, message="expected_int"))
            elif v < (1 if f == "requests" else 0):
                message = "must_be_positive" if f == "requests" else "must_be_non_negative"
                issues.append(SchemaIssue(kind="constraint", field=f, message=message))

    issues.extend(_token_consistency(row))
    return issues


def is_aggregate(row: dict[str, Any]) -> bool:
    """True for a row that sums several requests (or an unknown number of them)."""
    requests = row.get("requests")
    many = isinstance(requests, int) and not isinstance(requests, bool) and requests > 1
    return row.get("aggregate") is True or many


def request_count(row: dict[str, Any]) -> int | None:
    """Requests a valid row stands for; None for an aggregate of unknown size."""
    requests = row.get("requests")
    if isinstance(requests, int) and not isinstance(requests, bool):
        return requests
    return None if row.get("aggregate") is True else 1


def _token_consistency(row: dict[str, Any]) -> list[SchemaIssue]:
    issues: list[SchemaIssue] = []

    def _val(f: str) -> int | None:
        v = row.get(f)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            return v
        return None

    tin, cr, cw = _val("tokens_in"), _val("tokens_cache_read"), _val("tokens_cache_write")
    if (cr or cw) and tin is None:
        issues.append(
            SchemaIssue(kind="missing", field="tokens_in", message="required_with_cache_tokens")
        )
    elif tin is not None and (cr or 0) + (cw or 0) > tin:
        issues.append(
            SchemaIssue(kind="constraint", field="tokens_in", message="must_include_cache_tokens")
        )

    cw1h = _val("tokens_cache_write_1h")
    if cw1h and cw is None:
        issues.append(
            SchemaIssue(
                kind="missing", field="tokens_cache_write", message="required_with_1h_writes"
            )
        )
    elif cw is not None and cw1h is not None and cw1h > cw:
        issues.append(
            SchemaIssue(
                kind="constraint", field="tokens_cache_write", message="must_include_1h_writes"
            )
        )

    tout, rs = _val("tokens_out"), _val("tokens_reasoning")
    if rs and tout is None:
        issues.append(
            SchemaIssue(kind="missing", field="tokens_out", message="required_with_reasoning")
        )
    elif tout is not None and rs is not None and rs > tout:
        issues.append(
            SchemaIssue(
                kind="constraint", field="tokens_out", message="must_include_reasoning_tokens"
            )
        )

    if is_aggregate(row):
        for f in PER_REQUEST_FIELDS:
            if f in row:
                issues.append(
                    SchemaIssue(kind="constraint", field=f, message="not_allowed_on_aggregate_row")
                )
    return issues
