"""LangChain Runnable-protocol adapter over MiniGraph/CompiledGraph.

Zero-dependency at import time: ``langchain_core`` is never imported here or
anywhere in this module. This adapter only relies on the LangChain
``Runnable`` *interface shape* (invoke/batch/stream/ainvoke/abatch/astream
plus ``|`` chaining) being duck-typed compatible; it does not subclass or
import anything from LangChain.

Built against the real MiniGraph/CompiledGraph surface in
``perpetua_core.graph.engine``: there is no synchronous ``invoke`` on the
underlying graph, no ``stream``/``batch`` at all, and state is a
:class:`~perpetua_core.state.PerpetuaState`, not a plain dict. This adapter
bridges those gaps rather than assuming they already exist.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

from perpetua_core.graph.engine import CompiledGraph, MiniGraph
from perpetua_core.state import PerpetuaState


def _coerce_state(input_data: PerpetuaState | dict[str, Any]) -> PerpetuaState:
    if isinstance(input_data, PerpetuaState):
        return input_data
    return PerpetuaState(**input_data)


def _reject_if_loop_running() -> None:
    """Raise before a sync bridge method creates its coroutine.

    Checking first (rather than letting ``asyncio.run()`` reject an
    already-created coroutine) avoids a spurious "coroutine was never
    awaited" warning: if we built the coroutine and only then discovered a
    loop was already running, that coroutine object would be garbage
    collected unawaited.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "invoke()/batch()/stream() cannot be called from inside a running "
        "event loop; use ainvoke()/abatch()/astream() instead."
    )


class LangChainRunnableAdapter:
    """Wraps a MiniGraph/CompiledGraph as a LangChain-Runnable-shaped object.

    Input accepts either a :class:`PerpetuaState` or a plain ``dict`` (coerced
    via ``PerpetuaState(**input_data)`` — a ``session_id`` key is required,
    matching the state model's own requirement). Output is always the
    resulting :class:`PerpetuaState`, never silently downgraded to a dict, so
    callers keep ``.merge()``/``.metadata``/etc.; call ``.model_dump()``
    downstream if a plain dict is needed.
    """

    def __init__(self, graph: MiniGraph | CompiledGraph, name: str | None = None) -> None:
        self._graph: CompiledGraph = graph.compile() if isinstance(graph, MiniGraph) else graph
        self.name = name or "MiniGraphRunnable"

    # --- Asynchronous execution (the graph's native mode) ---
    async def ainvoke(
        self, input_data: PerpetuaState | dict[str, Any], config: dict[str, Any] | None = None
    ) -> PerpetuaState:
        return await self._graph.ainvoke(_coerce_state(input_data))

    async def abatch(
        self,
        inputs: list[PerpetuaState | dict[str, Any]],
        config: dict[str, Any] | None = None,
    ) -> list[PerpetuaState]:
        return list(
            await asyncio.gather(*(self.ainvoke(item, config=config) for item in inputs))
        )

    async def astream(
        self, input_data: PerpetuaState | dict[str, Any], config: dict[str, Any] | None = None
    ) -> AsyncIterator[PerpetuaState]:
        """Yield the state snapshot after each completed node.

        Built on ``CompiledGraph.aobserve`` (the same primitive
        ``graph.plugins.streaming.astream`` uses for structural
        ``GraphEvent``s) rather than a nonexistent raw state-stream method.
        Yields once per ``node.end``/``interrupt`` observation, skipping the
        purely structural ``edge.selected``/``node.start`` observations whose
        state hasn't changed yet.
        """
        async for observation in self._graph.aobserve(_coerce_state(input_data)):
            if observation.event.kind in ("node.end", "interrupt"):
                yield observation.state

    # --- Synchronous execution (bridged; the engine itself is async-only) ---
    def invoke(
        self, input_data: PerpetuaState | dict[str, Any], config: dict[str, Any] | None = None
    ) -> PerpetuaState:
        """Run to completion synchronously.

        MiniGraph/CompiledGraph expose no synchronous execution path — this
        bridges via ``asyncio.run()``. Raises ``RuntimeError`` if called from
        inside an already-running event loop; use :meth:`ainvoke` there
        instead of nesting loops.
        """
        _reject_if_loop_running()
        return asyncio.run(self.ainvoke(input_data, config=config))

    def batch(
        self,
        inputs: list[PerpetuaState | dict[str, Any]],
        config: dict[str, Any] | None = None,
    ) -> list[PerpetuaState]:
        _reject_if_loop_running()
        return asyncio.run(self.abatch(inputs, config=config))

    def stream(
        self, input_data: PerpetuaState | dict[str, Any], config: dict[str, Any] | None = None
    ) -> Iterator[PerpetuaState]:
        """Synchronous state-snapshot stream.

        Known limitation: the underlying engine has no synchronous streaming
        primitive, so this runs the graph to completion first (buffering all
        snapshots via ``asyncio.run``) and then yields them synchronously —
        it is not lazy/incremental like :meth:`astream`. Prefer ``astream``
        in an async context; this exists only for sync-only LCEL callers.
        """
        _reject_if_loop_running()

        async def _collect() -> list[PerpetuaState]:
            return [snapshot async for snapshot in self.astream(input_data, config=config)]

        yield from asyncio.run(_collect())

    # --- LCEL pipe operator support ---
    def __or__(self, other: Any) -> "ChainedRunnable":
        return ChainedRunnable(self, other)


class ChainedRunnable:
    """Minimal LCEL-style sequential composition: ``self`` then ``other``.

    ``other`` must be async-invokable (``ainvoke``) at minimum; sync
    ``invoke`` is bridged via ``asyncio.run`` the same way
    :class:`LangChainRunnableAdapter` does, so a real LangChain component on
    the right-hand side works without modification.
    """

    def __init__(self, first: Any, second: Any) -> None:
        self._first = first
        self._second = second

    async def ainvoke(self, input_data: Any, config: dict[str, Any] | None = None) -> Any:
        intermediate = await self._first.ainvoke(input_data, config=config)
        return await self._second.ainvoke(intermediate, config=config)

    def invoke(self, input_data: Any, config: dict[str, Any] | None = None) -> Any:
        _reject_if_loop_running()
        return asyncio.run(self.ainvoke(input_data, config=config))

    def __or__(self, other: Any) -> "ChainedRunnable":
        return ChainedRunnable(self, other)
