from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class LogFormatError(Exception):
    """Raised when log file has invalid format."""


ALLOWED_EXTENSIONS = {".jsonl", ".json", ".txt"}
MAX_FILE_SIZE_MB = 1000


def validate_file_path(
    path: Path, allowed_exts: set[str] = ALLOWED_EXTENSIONS
) -> Path:
    """Validate file path for security and existence.

    Args:
        path: Path to validate
        allowed_exts: Set of allowed file extensions

    Returns:
        Validated Path object (resolved to absolute path)

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If path is invalid or unsafe
    """
    if path.is_symlink():
        raise ValueError(f"Symlinks not allowed: {path}")

    resolved = path.resolve()

    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    if not resolved.is_file():
        raise ValueError(f"Path is not a file: {resolved}")

    if resolved.suffix.lower() not in allowed_exts:
        raise ValueError(
            f"Invalid file extension: {resolved.suffix}. "
            f"Allowed: {', '.join(sorted(allowed_exts))}"
        )

    size_mb = resolved.stat().st_size / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(
            f"File too large: {size_mb:.1f}MB (max: {MAX_FILE_SIZE_MB}MB)"
        )

    return resolved


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """Read JSONL file line by line.

    Args:
        path: Path to JSONL file

    Yields:
        Parsed JSON objects (must be dicts)

    Raises:
        FileNotFoundError: If file doesn't exist
        PermissionError: If file isn't readable
        LogFormatError: If line isn't valid JSON or not a dict
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    raise LogFormatError(
                        f"Invalid JSON at line {line_num} in {path.name}: {e}"
                    ) from e

                if not isinstance(obj, dict):
                    raise LogFormatError(
                        f"Expected dict at line {line_num} in {path.name}, "
                        f"got: {type(obj).__name__}"
                    )

                yield obj

    except FileNotFoundError:
        raise FileNotFoundError(f"Log file not found: {path}") from None
    except PermissionError:
        raise PermissionError(f"Cannot read log file: {path}") from None
    except UnicodeDecodeError as e:
        raise LogFormatError(f"File is not valid UTF-8: {path.name}") from e


def read_json(path: Path) -> Any:
    """Read JSON file.

    Args:
        path: Path to JSON file

    Returns:
        Parsed JSON object

    Raises:
        FileNotFoundError: If file doesn't exist
        PermissionError: If file isn't readable
        ValueError: If file isn't valid JSON
    """
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"Policy file not found: {path}") from None
    except PermissionError:
        raise PermissionError(f"Cannot read policy file: {path}") from None
    except UnicodeDecodeError as e:
        raise ValueError(f"Policy file is not valid UTF-8: {path.name}") from e

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path.name}: {e}") from e
