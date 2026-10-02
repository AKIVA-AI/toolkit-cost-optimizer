"""Report envelope v1: every command's machine-readable result as an in-toto Statement v1.

See ``docs/report-envelope.md`` and ``schemas/report-envelope.v1.json``. The envelope is
written as canonical JSON (UTF-8, sorted keys, no insignificant whitespace, trailing
newline) so its SHA-256 is stable and it can be signed with standard tooling.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
TOOL_NAME = "toolkit-cost-optimizer"
PREDICATE_TYPE = f"https://github.com/AKIVA-AI/{TOOL_NAME}/report/v1"

VERDICT_PASS = "pass"  # noqa: S105  # nosec B105 - a verdict, not a password
VERDICT_FAIL = "fail"
VERDICT_ERROR = "error"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def created_at() -> str:
    """RFC 3339 UTC timestamp. Honors SOURCE_DATE_EPOCH for reproducible reports."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is not None and epoch.strip().isdigit():
        moment = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    else:
        moment = datetime.now(tz=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(obj: Any) -> str:
    """Canonical JSON text: sorted keys, compact separators, trailing newline.

    NaN and infinities are rejected: they are not JSON and would make a report unreadable
    by strict parsers.
    """
    return (
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        + "\n"
    )


def verdict_for_exit(exit_code: int) -> str:
    """Map the CLI's documented exit codes to a verdict: 0 pass, 4 fail, anything else error."""
    if exit_code == 0:
        return VERDICT_PASS
    if exit_code == 4:
        return VERDICT_FAIL
    return VERDICT_ERROR


@dataclass
class ReportContext:
    """Collects the subject and inputs of one command run as it validates its files."""

    kind: str
    subject: list[dict[str, Any]] = field(default_factory=list)
    inputs: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def _descriptor(name: str, digest: str) -> dict[str, Any]:
        return {"name": name, "digest": {"sha256": digest}}

    def add_subject(self, name: str, path: Path) -> None:
        self.subject.append(self._descriptor(name, sha256_file(path)))

    def add_input(self, name: str, path: Path) -> None:
        self.inputs.append(self._descriptor(name, sha256_file(path)))

    def add_input_bytes(self, name: str, data: bytes) -> None:
        self.inputs.append(self._descriptor(name, sha256_bytes(data)))

    def build(
        self,
        *,
        exit_code: int,
        summary: dict[str, Any],
        details: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "_type": STATEMENT_TYPE,
            "subject": list(self.subject),
            "predicateType": PREDICATE_TYPE,
            "predicate": {
                "tool": {"name": TOOL_NAME, "version": __version__},
                "kind": self.kind,
                "created_at": created_at(),
                "verdict": verdict_for_exit(exit_code),
                "exit_code": exit_code,
                "inputs": list(self.inputs),
                "summary": summary,
                "details": details,
            },
        }
