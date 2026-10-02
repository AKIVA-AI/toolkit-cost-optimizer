"""Helpers for reading the report envelope that every command prints."""

from __future__ import annotations

import json
from typing import Any


def envelope(text: str) -> dict[str, Any]:
    """Parse one printed report envelope and check its outer shape."""
    env = json.loads(text)
    assert env["_type"] == "https://in-toto.io/Statement/v1"
    assert env["predicateType"].endswith("/report/v1")
    return env


def payload(text: str) -> dict[str, Any]:
    """The predicate's summary and details merged, for assertions on command results."""
    pred = envelope(text)["predicate"]
    return {**pred["summary"], **pred["details"]}
