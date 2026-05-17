"""Validated — pre/post Pydantic validation wrapping any node."""
from __future__ import annotations
import asyncio
from typing import Callable
from pydantic import BaseModel, ValidationError as PydanticValidationError
from perpetua_core.state import PerpetuaState

NodeFn = Callable[[PerpetuaState], object]


class ValidationError(RuntimeError):
    pass


class Validated:
    def __init__(self, inner: NodeFn, *, pre: type[BaseModel] | None = None,
                 post: type[BaseModel] | None = None):
        self._inner = inner
        self._pre = pre
        self._post = post

    async def __call__(self, state: PerpetuaState) -> dict:
        if self._pre:
            try:
                self._pre(**state.model_dump())
            except PydanticValidationError as e:
                raise ValidationError(f"pre: {e}") from e

        fn = self._inner
        delta = await fn(state) if asyncio.iscoroutinefunction(fn) else fn(state)

        if self._post:
            try:
                merged = {**state.scratchpad, **(delta.get("scratchpad") or {})}
                self._post(**merged)
            except PydanticValidationError as e:
                raise ValidationError(f"post: {e}") from e
        return delta
