"""Structured output plugin — Pydantic v2 response validation with retry."""
from __future__ import annotations
import json
import os
from typing import TypeVar, Type
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
MAX_RETRIES = int(os.getenv("PERPETUA_STRUCTURED_RETRIES", "2"))


async def chat_structured(
    llm_client,
    *,
    model: str,
    messages: list[dict],
    output_schema: Type[T],
    state=None,
) -> T:
    """Call LLM and validate response into output_schema. Retry on parse failure."""
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        msgs = list(messages)
        if attempt > 0:
            schema_hint = output_schema.model_json_schema()
            msgs.append({
                "role": "user",
                "content": (
                    f"Your previous response failed JSON validation. "
                    f"Respond ONLY with valid JSON matching: {json.dumps(schema_hint)}"
                ),
            })
        response = await llm_client.chat(model=model, messages=msgs)
        raw = response.choices[0].message.content or ""
        try:
            return output_schema.model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            last_err = exc
            if state is not None:
                state = state.merge({"retry_count": state.retry_count + 1})
    raise ValueError(
        f"Structured output validation failed after {MAX_RETRIES + 1} attempts"
    ) from last_err
