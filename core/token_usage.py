"""Real Gemini usage telemetry, without storing prompts, files, or secrets.

The Live API reports usage per completed turn. GenerateContent calls report
usage on their response. We keep one compact JSONL event per report so the
assistant can show session totals and the user can inspect them after restart.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.RLock()
_SESSION_ID: str | None = None
_TOTALS: dict = {}
_PATH = Path(__file__).resolve().parent.parent / "memory" / "token_usage.jsonl"


def _number(obj, *names: str) -> int:
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
    return 0


def _modality_counts(usage, field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in (getattr(usage, field, None) or []):
        modality = getattr(item, "modality", None)
        count = _number(item, "token_count")
        if modality is not None and count:
            name = str(getattr(modality, "name", modality)).upper()
            result[name] = result.get(name, 0) + count
    return result


def _new_totals() -> dict:
    return {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "thinking_tokens": 0,
        "tool_tokens": 0,
        "grounding_queries": 0,
        "input_modalities": {},
        "output_modalities": {},
        "sources": {},
        "paid_tier_usd_estimate": 0.0,
        "priced_requests": 0,
    }


def start_session(session_id: str | None = None) -> str:
    """Start a new local usage session. Reconnects keep the same session id."""
    global _SESSION_ID, _TOTALS
    with _LOCK:
        _SESSION_ID = session_id or uuid.uuid4().hex
        _TOTALS = _new_totals()
        return _SESSION_ID


def session_id() -> str:
    global _SESSION_ID, _TOTALS
    with _LOCK:
        if _SESSION_ID is None:
            _SESSION_ID = uuid.uuid4().hex
            _TOTALS = _new_totals()
        return _SESSION_ID


def _paid_estimate(model: str, input_modalities: dict, output_modalities: dict,
                   input_tokens: int, output_tokens: int, cached: int) -> float | None:
    """Return a paid-tier estimate only for known model IDs and uncached usage."""
    if cached:
        return None  # cache discounts and modality split vary by endpoint
    model = (model or "").lower().removeprefix("models/")
    if model.startswith("gemini-3.1-flash-live-preview"):
        in_rates = {"TEXT": 0.75, "AUDIO": 3.0, "IMAGE": 1.0, "VIDEO": 1.0}
        out_rates = {"TEXT": 4.5, "AUDIO": 12.0}
    elif model.startswith("gemini-3.1-flash-lite"):
        in_rates = {"TEXT": 0.25, "AUDIO": 0.5, "IMAGE": 0.25, "VIDEO": 0.25}
        out_rates = {"TEXT": 1.5, "AUDIO": 1.5}
    elif model.startswith("gemini-3.8-flash"):
        # Official introductory price through 2026-12-31; stop estimating if
        # the documented rate changes and this table has not been updated.
        if datetime.now(timezone.utc).date().isoformat() > "2026-12-31":
            return None
        in_rates = {"TEXT": 0.75, "AUDIO": 0.75, "IMAGE": 0.75, "VIDEO": 0.75}
        out_rates = {"TEXT": 3.75, "AUDIO": 3.75}
    else:
        return None

    # Some SDK/model versions omit modality details. Text-only totals remain
    # estimable, but multimodal totals without a breakdown are deliberately not.
    if not input_modalities and input_tokens:
        input_modalities = {"TEXT": input_tokens}
    if not output_modalities and output_tokens:
        output_modalities = {"TEXT": output_tokens}
    if sum(input_modalities.values()) != input_tokens or sum(output_modalities.values()) != output_tokens:
        return None
    return (sum(count * in_rates.get(kind, in_rates.get("TEXT", 0.0))
                for kind, count in input_modalities.items())
            + sum(count * out_rates.get(kind, out_rates.get("TEXT", 0.0))
                  for kind, count in output_modalities.items())) / 1_000_000


def _add_modalities(target: dict, source: dict) -> None:
    for name, count in source.items():
        target[name] = target.get(name, 0) + count


def record_usage(model: str, source: str, usage, grounding_queries: int = 0) -> dict | None:
    """Record one usage report; return updated session totals, or None if empty."""
    global _SESSION_ID, _TOTALS
    if usage is None and not grounding_queries:
        return None
    input_tokens = _number(usage, "prompt_token_count", "input_token_count", "total_input_tokens")
    output_tokens = _number(usage, "candidates_token_count", "response_token_count", "output_token_count", "total_output_tokens")
    cached = _number(usage, "cached_content_token_count", "cached_input_token_count", "total_cached_tokens")
    thinking = _number(usage, "thoughts_token_count", "thinking_token_count", "total_thought_tokens")
    tool_tokens = _number(usage, "tool_use_prompt_token_count", "tool_use_input_tokens", "total_tool_use_tokens")
    input_modalities = _modality_counts(usage, "prompt_tokens_details") or _modality_counts(usage, "input_tokens_details")
    output_modalities = _modality_counts(usage, "response_tokens_details") or _modality_counts(usage, "output_tokens_details")
    if not any((input_tokens, output_tokens, cached, thinking, tool_tokens, grounding_queries,
                sum(input_modalities.values()), sum(output_modalities.values()))):
        return None

    estimate = _paid_estimate(model, input_modalities, output_modalities,
                              input_tokens, output_tokens, cached)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    delta = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached,
        "thinking_tokens": thinking,
        "tool_tokens": tool_tokens,
        "grounding_queries": max(0, int(grounding_queries)),
        "input_modalities": input_modalities,
        "output_modalities": output_modalities,
        "paid_tier_usd_estimate": estimate,
    }
    with _LOCK:
        if _SESSION_ID is None:
            _SESSION_ID = uuid.uuid4().hex
            _TOTALS = _new_totals()
        totals = _TOTALS
        totals["requests"] += 1
        for key in ("input_tokens", "output_tokens", "cached_input_tokens", "thinking_tokens", "tool_tokens", "grounding_queries"):
            totals[key] += delta[key]
        _add_modalities(totals["input_modalities"], input_modalities)
        _add_modalities(totals["output_modalities"], output_modalities)
        source_totals = totals["sources"].setdefault(source, {"requests": 0, "input_tokens": 0, "output_tokens": 0})
        source_totals["requests"] += 1
        source_totals["input_tokens"] += input_tokens
        source_totals["output_tokens"] += output_tokens
        if estimate is not None:
            totals["paid_tier_usd_estimate"] += estimate
            totals["priced_requests"] += 1
        event = {
            "session_id": _SESSION_ID, "timestamp": stamp, "model": model,
            "source": source, "usage": delta, "session_totals": dict(totals),
        }
        try:
            _PATH.parent.mkdir(parents=True, exist_ok=True)
            with _PATH.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError as exc:
            print(f"[Usage] Could not save token usage: {exc}")
        return json.loads(json.dumps(totals))


def record_response(response, model: str, source: str) -> dict | None:
    """Record GenerateContent response usage, tolerating older SDK shapes."""
    actual_model = getattr(response, "model_version", None) or model
    queries = 0
    for candidate in (getattr(response, "candidates", None) or []):
        grounding = getattr(candidate, "grounding_metadata", None)
        queries = max(queries, len(getattr(grounding, "web_search_queries", None) or []))
    return record_usage(str(actual_model), source, getattr(response, "usage_metadata", None), queries)


def totals() -> dict:
    with _LOCK:
        if _SESSION_ID is None:
            return _new_totals()
        return json.loads(json.dumps(_TOTALS))


def format_totals(summary_source: str = "session_summary") -> str:
    data = totals()
    if not data["requests"]:
        return "Usage Gemini : aucune métadonnée de tokens reçue pour cette session."
    im = data["input_modalities"]
    om = data["output_modalities"]
    bits = [
        f"entrée {data['input_tokens']:,}",
        f"sortie {data['output_tokens']:,}",
        f"audio {im.get('AUDIO', 0):,} in / {om.get('AUDIO', 0):,} out",
        f"images {im.get('IMAGE', 0) + im.get('VIDEO', 0):,} in / {om.get('IMAGE', 0) + om.get('VIDEO', 0):,} out",
        f"outils {data['tool_tokens']:,}",
    ]
    if data["cached_input_tokens"]:
        bits.append(f"cache {data['cached_input_tokens']:,}")
    if data["grounding_queries"]:
        bits.append(f"Google Search {data['grounding_queries']} requêtes (frais mensuels hors estimation)")
    summary = data["sources"].get(summary_source)
    if summary:
        bits.append(f"résumé {summary['input_tokens'] + summary['output_tokens']:,}")
    costly_sources = sorted(
        ((name, stats["input_tokens"] + stats["output_tokens"])
         for name, stats in data["sources"].items()),
        key=lambda pair: pair[1], reverse=True,
    )[:4]
    if costly_sources:
        bits.append("par source " + ", ".join(f"{name} {count:,}" for name, count in costly_sources))
    if data["priced_requests"]:
        bits.append(f"≈ ${data['paid_tier_usd_estimate']:.6f} paid-tier, {data['priced_requests']}/{data['requests']} appels tarifés")
    else:
        bits.append("estimation USD indisponible pour ce modèle/offre")
    return "Usage Gemini session : " + " · ".join(bits)
