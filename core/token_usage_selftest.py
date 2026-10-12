"""Offline checks for Gemini token telemetry aggregation and privacy."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from core import token_usage


class _Modality:
    def __init__(self, name: str, count: int):
        self.modality = name
        self.token_count = count


class _Usage:
    prompt_token_count = 120
    candidates_token_count = 60
    cached_content_token_count = 0
    thoughts_token_count = 9
    tool_use_prompt_token_count = 7
    prompt_tokens_details = [_Modality("AUDIO", 100), _Modality("IMAGE", 20)]
    response_tokens_details = [_Modality("AUDIO", 50), _Modality("TEXT", 10)]


def run() -> int:
    old_path = token_usage._PATH
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-token-usage-") as tmp:
            token_usage._PATH = Path(tmp) / "usage.jsonl"
            assert token_usage.start_session("offline-test") == "offline-test"
            totals = token_usage.record_usage(
                "gemini-3.1-flash-live-preview", "live_voice", _Usage()
            )
            assert totals is not None
            assert totals["input_tokens"] == 120 and totals["output_tokens"] == 60
            assert totals["input_modalities"] == {"AUDIO": 100, "IMAGE": 20}
            assert totals["output_modalities"] == {"AUDIO": 50, "TEXT": 10}
            assert totals["tool_tokens"] == 7 and totals["thinking_tokens"] == 9
            assert totals["priced_requests"] == 1 and totals["paid_tier_usd_estimate"] > 0
            token_usage.record_usage("unlisted-model", "file_processor", _Usage())
            totals = token_usage.totals()
            assert totals["requests"] == 2 and totals["priced_requests"] == 1
            assert totals["sources"]["live_voice"]["input_tokens"] == 120
            assert "audio 200 in / 100 out" in token_usage.format_totals()
            assert "résumé" not in token_usage.format_totals()
            grounded = token_usage.record_usage("unknown-model", "web_search", None, 2)
            assert grounded["grounding_queries"] == 2 and grounded["requests"] == 3
            rows = [json.loads(line) for line in token_usage._PATH.read_text().splitlines()]
            assert len(rows) == 3 and rows[0]["session_id"] == "offline-test"
            assert "prompt" not in rows[0] and "source code" not in rows[0]
        print("token_usage selftest: PASS")
        return 0
    except Exception as exc:
        print(f"token_usage selftest: FAIL: {exc}")
        return 1
    finally:
        token_usage._PATH = old_path


if __name__ == "__main__":
    raise SystemExit(run())
