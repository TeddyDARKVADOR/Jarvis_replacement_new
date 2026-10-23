"""Expose real Gemini usage for the current Matthew conversation."""

from core import token_usage


def usage_report(parameters: dict | None = None, player=None) -> str:
    return token_usage.format_totals()


TOOL = {
    "name": "usage_report",
    "description": "Reports actual Gemini token usage for this session, split by audio, images, tools, summaries, and action source. Use when asked about token or API usage.",
    "parameters": {"type": "OBJECT", "properties": {}},
    "handler": usage_report,
}
