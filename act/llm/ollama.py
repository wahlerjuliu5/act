from __future__ import annotations

import json
import re

import httpx

from act.models import QueryResponse

MAX_RETRIES = 3


async def query(
    prompt: str,
    system_prompt: str,
    model: str,
    host: str,
) -> QueryResponse:
    """Send a natural-language prompt to Ollama and return a validated QueryResponse.

    Retries up to MAX_RETRIES times, feeding the model its own bad output as
    context so it can self-correct.
    """
    url = f"{host}/v1/chat/completions"
    last_error: str | None = None
    last_raw: str = ""

    async with httpx.AsyncClient(timeout=90.0) as client:
        for _ in range(MAX_RETRIES):
            messages: list[dict] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            if last_error:
                messages += [
                    {"role": "assistant", "content": last_raw},
                    {
                        "role": "user",
                        "content": (
                            f"That response was not valid JSON. Error: {last_error}. "
                            "Respond ONLY with valid JSON matching the schema."
                        ),
                    },
                ]

            resp = await client.post(
                url,
                json={"model": model, "messages": messages},
            )
            resp.raise_for_status()
            last_raw = resp.json()["choices"][0]["message"]["content"]

            try:
                return QueryResponse.model_validate(extract_json(last_raw))
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)

    raise ValueError(
        f"LLM returned invalid JSON after {MAX_RETRIES} attempts. "
        f"Last error: {last_error}"
    )


def extract_json(text: str) -> dict:
    """Extract a JSON object from model output, stripping markdown code fences."""
    text = text.strip()
    # Handle ```json ... ``` or ``` ... ``` blocks
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)
