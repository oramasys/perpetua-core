"""LLMClient — async OpenAI-compatible dispatcher. Dumb by design."""
from __future__ import annotations
import os
from openai import AsyncOpenAI

DEFAULT_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
DEFAULT_API_KEY = os.getenv("LLM_API_KEY", "ollama")


class LLMClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = DEFAULT_API_KEY,
    ):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def chat(self, *, model: str, messages: list[dict], **kwargs):
        return await self._client.chat.completions.create(
            model=model, messages=messages, **kwargs
        )
