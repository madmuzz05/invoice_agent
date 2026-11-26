from __future__ import annotations

import re
from typing import Any

from baca_invoice.utils.llm_text import answer_text, extract_json_object
from google.adk.runners import Runner
from google.genai import types as genai_types


async def run_agent_for_json(
    runner: Runner,
    user_id: str,
    session_id: str,
    message: genai_types.Content,
) -> dict:
    """Jalankan agent dan kumpulkan JSON hasil ekstraksi terakhir.

    Mendukung dua jalur output ADK:
    1. Tool call `set_model_response` (structured) — diambil duluan.
    2. Final response text — di-parse dengan `_parse_json_result`.
    """
    parsed_result: dict | None = None
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=message,
    ):
        structured_result = _extract_set_model_response(event)
        if structured_result is not None:
            parsed_result = structured_result
            continue

        if event.is_final_response() and event.content and event.content.parts:
            raw_text = _extract_text(event.content.parts)
            parsed_result = _unwrap_agent_result(_parse_json_result(raw_text))

    if parsed_result is None:
        raise ValueError("Agent finished without returning a JSON extraction result.")
    return parsed_result


def _extract_text(parts) -> str:
    return answer_text(parts)


def _parse_json_result(text: str) -> dict:
    parsed = extract_json_object(text)
    if parsed is not None:
        return parsed

    # Fallback: literal Python (True/False/None) yang kadang dipakai LLM.
    pythonish = re.sub(r"\bTrue\b", "true", text)
    pythonish = re.sub(r"\bFalse\b", "false", pythonish)
    pythonish = re.sub(r"\bNone\b", "null", pythonish)
    parsed = extract_json_object(pythonish)
    return parsed if parsed is not None else {"raw": text.strip()}


def _unwrap_agent_result(data: dict) -> dict:
    if not isinstance(data, dict) or len(data) != 1:
        return data
    key = next(iter(data))
    if key.endswith("_response") and isinstance(data[key], dict):
        return data[key]
    return data


def _extract_set_model_response(event: Any) -> dict | None:
    if not hasattr(event, "get_function_responses"):
        return None
    for response in event.get_function_responses() or []:
        if response.name != "set_model_response":
            continue
        data = response.response
        if isinstance(data, dict) and isinstance(data.get("result"), dict):
            return data["result"]
        if isinstance(data, dict):
            return data
    return None
