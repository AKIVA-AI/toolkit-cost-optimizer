"""Read log rows for a command, validating every row against the row schema.

Commands never analyze rows they have not validated: a row that fails the schema makes
the whole command fail with exit code 2 and an ``error`` report listing the issues.
``--coerce-numeric-strings`` is the explicit opt-in for logs that quote their numbers.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .io import read_jsonl
from .schema import SchemaIssue, coerce_numeric_strings, validate_inference_event


class RowValidationError(ValueError):
    """Raised when one or more input rows fail the row schema."""

    def __init__(self, invalid_rows: int, total_rows: int, issues: list[dict[str, Any]]):
        self.invalid_rows = invalid_rows
        self.total_rows = total_rows
        self.issues = issues
        first = ", ".join(f"{i['field']}:{i['message']}" for i in issues[:3])
        super().__init__(
            f"{invalid_rows}/{total_rows} rows failed the row schema ({first}). "
            "Run `toolkit-opt validate` for the full list."
        )


@dataclass
class RowReader:
    """Iterate the valid rows of a JSONL log while counting and recording invalid ones."""

    path: Path
    coerce: bool = False
    require: tuple[str, ...] = ()
    total: int = 0
    invalid: int = 0
    coerced_values: int = 0
    _issues: dict[tuple[str, str, str], int] = field(default_factory=dict)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for row in read_jsonl(self.path):
            self.total += 1
            if self.coerce:
                self.coerced_values += coerce_numeric_strings(row)
            row_issues = validate_inference_event(row, self.require)
            if row_issues:
                self.invalid += 1
                for iss in row_issues:
                    key = (iss.kind, iss.field, iss.message)
                    self._issues[key] = self._issues.get(key, 0) + 1
                continue
            yield row

    def issue_list(self) -> list[dict[str, Any]]:
        return [
            SchemaIssue(kind=k[0], field=k[1], message=k[2], count=v).__dict__
            for k, v in sorted(self._issues.items())
        ]

    def raise_if_invalid(self) -> None:
        if self.invalid:
            raise RowValidationError(self.invalid, self.total, self.issue_list())
