from __future__ import annotations

import json
import re

import httpx

from act.llm.normalize import normalize_proposed_action
from act.models import QueryResponse

# JSON Schema passed to Ollama's "format" field.
# Ollama converts this to a GBNF grammar and enforces it at the token level —
# the model is physically unable to emit text that doesn't conform.
_STRUCTURED_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["query", "create", "delete", "update", "find_slots"],
        },
        "proposed_action": {
            "type": ["object", "null"],
        },
        "human_summary": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["intent", "proposed_action", "human_summary", "confidence"],
}

MAX_RETRIES = 3


async def query(
    prompt: str,
    system_prompt: str,
    model: str,
    host: str,
) -> QueryResponse:
    """Send a natural-language prompt to Ollama and return a validated QueryResponse.

    Uses Ollama's native /api/chat endpoint with structured-output (grammar sampling)
    so the model is physically constrained to emit valid JSON matching our schema.
    The retry loop is kept for transient network/server errors.
    """
    url = f"{host}/api/chat"
    last_error: str | None = None

    async with httpx.AsyncClient(timeout=90.0) as client:
        for _ in range(MAX_RETRIES):
            messages: list[dict] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]

            resp = await client.post(
                url,
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "format": _STRUCTURED_OUTPUT_SCHEMA,
                },
            )
            resp.raise_for_status()
            content = resp.json()["message"]["content"]

            try:
                data = json.loads(content)
                if isinstance(data.get("proposed_action"), dict):
                    data["proposed_action"] = normalize_proposed_action(
                        data.get("intent", ""), data["proposed_action"]
                    )
                return QueryResponse.model_validate(data)
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)

    raise ValueError(
        f"LLM returned invalid JSON after {MAX_RETRIES} attempts. "
        f"Last error: {last_error}"
    )


def extract_json(text: str) -> dict:
    """Extract a JSON object from model output, stripping markdown code fences.

    Not used in the main query path (structured output guarantees clean JSON),
    but kept as a utility for testing and debugging raw LLM output.
    """
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)
