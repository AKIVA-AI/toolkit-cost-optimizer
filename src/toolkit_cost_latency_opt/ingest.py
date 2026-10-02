"""Normalize gateway spend logs, tracing spans and provider usage exports into v2 rows.

Supported formats (field names from each project's documented schema):

``litellm-spendlogs``
    Rows of LiteLLM's ``LiteLLM_SpendLogs`` table, as returned by ``GET /spend/logs``
    (``summarize=false``) or ``GET /spend/logs/v2`` (``{"data": [...]}``). Source:
    ``litellm/proxy/schema.prisma`` (model ``LiteLLM_SpendLogs``) and
    https://docs.litellm.ai/docs/proxy/cost_tracking.
``litellm-payload``
    LiteLLM ``StandardLoggingPayload`` objects, as written by the ``s3_v2``,
    ``gcs_bucket`` and ``generic_api`` loggers (JSON array or NDJSON). Source:
    ``litellm/types/utils.py`` (``StandardLoggingPayload``) and
    https://docs.litellm.ai/docs/proxy/logging_spec.
``otel``
    OTLP/JSON trace data (``{"resourceSpans": [...]}``), one document per line as the
    OpenTelemetry Collector file exporter writes it, with GenAI semantic-convention
    attributes (``gen_ai.*``). Sources: https://opentelemetry.io/docs/specs/otlp/ and
    https://github.com/open-telemetry/semantic-conventions (v1.41.0 GenAI registry;
    the renamed ``gen_ai.usage.cache_write.input_tokens`` from the GenAI repository is
    also accepted).
``openai-usage``
    OpenAI organization Usage API, completions (``GET /v1/organization/usage/completions``
    grouped by ``model``). Source: the ``UsageCompletionsResult`` schema in
    https://github.com/openai/openai-openapi.
``anthropic-usage``
    Anthropic Admin API messages usage report
    (``GET /v1/organizations/usage_report/messages`` grouped by ``model``). Source:
    https://platform.claude.com/docs/en/api/beta/organization/usage_report/retrieve_messages.

Every produced row is checked against the row schema. A source record that cannot be
turned into a valid row is skipped and counted with a reason; nothing is filled in.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import LogFormatError, read_json, read_jsonl
from .schema import validate_inference_event

FORMATS = ("litellm-spendlogs", "litellm-payload", "otel", "openai-usage", "anthropic-usage")

# gen_ai.operation.name values that are a model inference call (priced per token).
INFERENCE_OPERATIONS = frozenset({"chat", "text_completion", "generate_content"})

# OTLP status code ERROR (opentelemetry-proto trace.proto: STATUS_CODE_ERROR = 2).
OTLP_STATUS_ERROR = 2

_FRACTION = re.compile(r"\.(\d+)")


@dataclass
class IngestResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    records: int = 0
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    ignored: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def skip(self, reason: str) -> None:
        self.skipped[reason] += 1

    def ignore(self, reason: str) -> None:
        self.ignored[reason] += 1


def load_documents(path: Path) -> list[Any]:
    """A ``.jsonl`` file is one document per line; a ``.json`` file is one document."""
    if path.suffix.lower() == ".jsonl":
        return list(_read_jsonl_values(path))
    return [read_json(path)]


def _read_jsonl_values(path: Path) -> Iterator[Any]:
    # read_jsonl requires objects, which every supported JSONL format writes.
    yield from read_jsonl(path)


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------


def _int(value: Any) -> int | None:
    """A non-negative integer from a JSON number or a decimal string (OTLP int64)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _first_int(*values: Any) -> int | None:
    for v in values:
        n = _int(v)
        if n is not None:
            return n
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _dict(value: Any) -> dict[str, Any]:
    """A JSON object, also when stored as a JSON string (database JSON columns)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []


def parse_timestamp(value: Any) -> float | None:
    """Unix seconds from epoch seconds or an ISO 8601 string (naive means UTC)."""
    num = _number(value)
    if num is not None:
        return num if num >= 0 else None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace(" ", "T", 1)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    # Python 3.10's fromisoformat accepts only 3 or 6 fractional digits.
    text = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _finish(result: IngestResult, source: str, row: dict[str, Any]) -> None:
    """Drop absent values, validate against the v2 schema, and keep or skip the row."""
    clean: dict[str, Any] = {"schema_version": 2, "source": source}
    clean.update({k: v for k, v in row.items() if v is not None})
    if "model" not in clean:
        result.skip("missing_model")
        return
    if "created_ts" not in clean:
        result.skip("missing_timestamp")
        return
    issues = validate_inference_event(clean)
    if issues:
        first = issues[0]
        result.skip(f"invalid_row:{first.field}:{first.message}")
        return
    result.rows.append(clean)


# ---------------------------------------------------------------------------
# LiteLLM
# ---------------------------------------------------------------------------


def _litellm_usage(metadata: dict[str, Any]) -> dict[str, int | None]:
    """Cache and reasoning counts from a LiteLLM usage object.

    LiteLLM normalizes provider usage into the OpenAI shape: ``prompt_tokens`` includes
    cache reads and cache creation (for Anthropic it adds ``cache_creation_input_tokens``
    and ``cache_read_input_tokens`` to ``input_tokens``), cache reads are
    ``prompt_tokens_details.cached_tokens``, cache writes
    ``prompt_tokens_details.cache_write_tokens`` / ``cache_creation_tokens`` with a
    ``cache_creation_token_details.ephemeral_1h_input_tokens`` split, and reasoning is
    ``completion_tokens_details.reasoning_tokens``. Spend logs also copy the cache
    counts into ``metadata.additional_usage_values``.
    """
    extra = _dict(metadata.get("additional_usage_values"))
    usage = _dict(metadata.get("usage_object"))
    prompt = _dict(extra.get("prompt_tokens_details")) or _dict(usage.get("prompt_tokens_details"))
    completion = _dict(extra.get("completion_tokens_details")) or _dict(
        usage.get("completion_tokens_details")
    )
    creation = _dict(prompt.get("cache_creation_token_details"))
    return {
        "tokens_cache_read": _first_int(
            extra.get("cache_read_input_tokens"), prompt.get("cached_tokens")
        ),
        "tokens_cache_write": _first_int(
            extra.get("cache_creation_input_tokens"),
            prompt.get("cache_write_tokens"),
            prompt.get("cache_creation_tokens"),
        ),
        "tokens_cache_write_1h": _int(creation.get("ephemeral_1h_input_tokens")) or None,
        "tokens_reasoning": _int(completion.get("reasoning_tokens")),
    }


def _litellm_tier(record: dict[str, Any], metadata: dict[str, Any], key: str | None) -> Any:
    if not key:
        return None
    prefix = f"{key}:"
    for tag in _list(record.get("request_tags")):
        if isinstance(tag, str) and tag.startswith(prefix) and tag[len(prefix) :].strip():
            return tag[len(prefix) :].strip()
    return _text(metadata.get(key))


def _litellm_success(status: Any) -> bool | None:
    if status == "success":
        return True
    if status == "failure":
        return False
    return None


def _litellm_records(docs: Iterable[Any]) -> Iterator[Any]:
    for doc in docs:
        if isinstance(doc, list):
            yield from doc
        elif isinstance(doc, dict) and isinstance(doc.get("data"), list):
            yield from doc["data"]
        else:
            yield doc


def _ingest_litellm_spendlogs(docs: list[Any], tier_from: str | None) -> IngestResult:
    result = IngestResult()
    for rec in _litellm_records(docs):
        result.records += 1
        if not isinstance(rec, dict):
            result.skip("not_an_object")
            continue
        metadata = _dict(rec.get("metadata"))
        start = parse_timestamp(rec.get("startTime"))
        end = parse_timestamp(rec.get("endTime"))
        latency = _number(rec.get("request_duration_ms"))
        if latency is None and start is not None and end is not None and end >= start:
            latency = (end - start) * 1000.0
        _finish(
            result,
            "litellm-spendlogs",
            {
                "created_ts": start,
                "model": _text(rec.get("model")),
                "provider": _text(rec.get("custom_llm_provider")),
                "request_id": _text(rec.get("request_id")),
                "latency_ms": latency,
                "success": _litellm_success(rec.get("status")),
                "cost_usd": _number(rec.get("spend")),
                "tokens_in": _int(rec.get("prompt_tokens")),
                "tokens_out": _int(rec.get("completion_tokens")),
                "tier": _litellm_tier(rec, metadata, tier_from),
                **_litellm_usage(metadata),
            },
        )
    return result


def _ingest_litellm_payload(docs: list[Any], tier_from: str | None) -> IngestResult:
    result = IngestResult()
    for rec in _litellm_records(docs):
        result.records += 1
        if not isinstance(rec, dict):
            result.skip("not_an_object")
            continue
        metadata = _dict(rec.get("metadata"))
        usage_meta = metadata if metadata.get("usage_object") else _dict(rec.get("hidden_params"))
        start = _number(rec.get("startTime"))
        end = _number(rec.get("endTime"))
        latency = (end - start) * 1000.0 if start is not None and end is not None else None
        _finish(
            result,
            "litellm-payload",
            {
                "created_ts": start,
                "model": _text(rec.get("model")),
                "provider": _text(rec.get("custom_llm_provider")),
                "request_id": _text(rec.get("id")),
                "latency_ms": latency if latency is not None and latency >= 0 else None,
                "success": _litellm_success(rec.get("status")),
                "cost_usd": _number(rec.get("response_cost")),
                "tokens_in": _int(rec.get("prompt_tokens")),
                "tokens_out": _int(rec.get("completion_tokens")),
                "tier": _litellm_tier(rec, metadata, tier_from),
                **_litellm_usage(usage_meta),
            },
        )
    return result


# ---------------------------------------------------------------------------
# OpenTelemetry (OTLP/JSON, GenAI semantic conventions)
# ---------------------------------------------------------------------------


def _any_value(value: Any) -> Any:
    """Decode an OTLP/JSON AnyValue."""
    if not isinstance(value, dict):
        return None
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        return _int(value["intValue"])  # int64 is a decimal string in OTLP/JSON
    if "arrayValue" in value:
        return [_any_value(v) for v in _dict(value["arrayValue"]).get("values", [])]
    return None


def _attributes(items: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            out[item["key"]] = _any_value(item.get("value"))
    return out


def _ingest_otel(docs: list[Any], tier_from: str | None) -> IngestResult:
    result = IngestResult()
    for doc in docs:
        if not isinstance(doc, dict) or not isinstance(doc.get("resourceSpans"), list):
            result.records += 1
            result.skip("not_otlp_trace_data")
            continue
        for rs in doc["resourceSpans"]:
            resource_attrs = _attributes(_dict(_dict(rs).get("resource")).get("attributes"))
            for ss in _list(_dict(rs).get("scopeSpans")):
                for span in _list(_dict(ss).get("spans")):
                    result.records += 1
                    _otel_span(result, _dict(span), resource_attrs, tier_from)
    return result


def _otel_span(
    result: IngestResult,
    span: dict[str, Any],
    resource_attrs: dict[str, Any],
    tier_from: str | None,
) -> None:
    attrs = _attributes(span.get("attributes"))
    operation = attrs.get("gen_ai.operation.name")
    if operation is None:
        result.ignore("not_a_genai_span")
        return
    if operation not in INFERENCE_OPERATIONS:
        result.ignore(f"operation:{operation}")
        return
    start_ns = _int(span.get("startTimeUnixNano"))
    end_ns = _int(span.get("endTimeUnixNano"))
    latency = None
    if start_ns is not None and end_ns is not None and end_ns >= start_ns:
        latency = (end_ns - start_ns) / 1_000_000
    status = _dict(span.get("status"))
    failed = _int(status.get("code")) == OTLP_STATUS_ERROR or bool(attrs.get("error.type"))
    tier = None
    if tier_from:
        tier = _text(attrs.get(tier_from)) or _text(resource_attrs.get(tier_from))
    _finish(
        result,
        "otel",
        {
            "created_ts": start_ns / 1e9 if start_ns is not None else None,
            "model": _text(attrs.get("gen_ai.response.model"))
            or _text(attrs.get("gen_ai.request.model")),
            "provider": _text(attrs.get("gen_ai.provider.name"))
            or _text(attrs.get("gen_ai.system")),
            "request_id": _text(attrs.get("gen_ai.response.id")) or _text(span.get("spanId")),
            "latency_ms": latency,
            "success": not failed,
            "tokens_in": _first_int(
                attrs.get("gen_ai.usage.input_tokens"), attrs.get("gen_ai.usage.prompt_tokens")
            ),
            "tokens_out": _first_int(
                attrs.get("gen_ai.usage.output_tokens"),
                attrs.get("gen_ai.usage.completion_tokens"),
            ),
            "tokens_cache_read": _int(attrs.get("gen_ai.usage.cache_read.input_tokens")),
            "tokens_cache_write": _first_int(
                attrs.get("gen_ai.usage.cache_write.input_tokens"),
                attrs.get("gen_ai.usage.cache_creation.input_tokens"),
            ),
            "tokens_reasoning": _int(attrs.get("gen_ai.usage.reasoning.output_tokens")),
            "tier": tier,
        },
    )


# ---------------------------------------------------------------------------
# Provider usage exports (aggregate rows)
# ---------------------------------------------------------------------------


def _buckets(docs: Iterable[Any]) -> Iterator[Any]:
    for doc in docs:
        if isinstance(doc, dict) and isinstance(doc.get("data"), list):
            yield from doc["data"]
        elif isinstance(doc, list):
            yield from doc
        else:
            yield doc


def _ingest_openai_usage(docs: list[Any], tier_from: str | None) -> IngestResult:
    result = IngestResult()
    for bucket in _buckets(docs):
        bucket = _dict(bucket)
        start = parse_timestamp(bucket.get("start_time"))
        for res in _list(bucket.get("results")):
            result.records += 1
            res = _dict(res)
            requests = _int(res.get("num_model_requests"))
            if not requests:
                result.ignore("empty_result")
                continue
            if res.get("batch") is True:
                result.skip("batch_pricing_not_modeled")
                continue
            service_tier = res.get("service_tier")
            if service_tier not in (None, "default", "auto"):
                result.skip(f"service_tier_not_modeled:{service_tier}")
                continue
            _finish(
                result,
                "openai-usage",
                {
                    "created_ts": start,
                    "model": _text(res.get("model")),
                    "provider": "openai",
                    "aggregate": True,
                    "requests": requests,
                    # input_tokens includes cached and cache-write tokens (spec text).
                    "tokens_in": _int(res.get("input_tokens")),
                    "tokens_cache_read": _int(res.get("input_cached_tokens")),
                    "tokens_cache_write": _int(res.get("input_cache_write_tokens")),
                    "tokens_out": _int(res.get("output_tokens")),
                    "tier": _text(res.get(tier_from)) if tier_from else None,
                },
            )
    return result


def _ingest_anthropic_usage(docs: list[Any], tier_from: str | None) -> IngestResult:
    result = IngestResult()
    for bucket in _buckets(docs):
        bucket = _dict(bucket)
        start = parse_timestamp(bucket.get("starting_at"))
        for res in _list(bucket.get("results")):
            result.records += 1
            res = _dict(res)
            service_tier = res.get("service_tier")
            if service_tier not in (None, "standard"):
                result.skip(f"service_tier_not_modeled:{service_tier}")
                continue
            creation = _dict(res.get("cache_creation"))
            parts = {
                "uncached": _int(res.get("uncached_input_tokens")),
                "read": _int(res.get("cache_read_input_tokens")) or 0,
                "w5m": _int(creation.get("ephemeral_5m_input_tokens")) or 0,
                "w1h": _int(creation.get("ephemeral_1h_input_tokens")) or 0,
            }
            output = _int(res.get("output_tokens"))
            if parts["uncached"] is None or output is None:
                result.skip("missing_token_counts")
                continue
            total_in = parts["uncached"] + parts["read"] + parts["w5m"] + parts["w1h"]
            if total_in == 0 and output == 0:
                result.ignore("empty_result")
                continue
            _finish(
                result,
                "anthropic-usage",
                {
                    "created_ts": start,
                    "model": _text(res.get("model")),
                    "provider": "anthropic",
                    # The report has no request count: an aggregate of unknown size.
                    "aggregate": True,
                    "tokens_in": total_in,
                    "tokens_cache_read": parts["read"],
                    "tokens_cache_write": parts["w5m"] + parts["w1h"],
                    "tokens_cache_write_1h": parts["w1h"],
                    "tokens_out": output,
                    "tier": _text(res.get(tier_from)) if tier_from else None,
                },
            )
    return result


_INGESTERS = {
    "litellm-spendlogs": _ingest_litellm_spendlogs,
    "litellm-payload": _ingest_litellm_payload,
    "otel": _ingest_otel,
    "openai-usage": _ingest_openai_usage,
    "anthropic-usage": _ingest_anthropic_usage,
}


def ingest(fmt: str, docs: list[Any], tier_from: str | None = None) -> IngestResult:
    if fmt not in _INGESTERS:
        raise ValueError(f"unknown format {fmt!r}; expected one of {', '.join(FORMATS)}")
    try:
        return _INGESTERS[fmt](docs, tier_from)
    except RecursionError as e:  # pragma: no cover - pathological nesting
        raise LogFormatError(f"input nested too deeply for {fmt}") from e
