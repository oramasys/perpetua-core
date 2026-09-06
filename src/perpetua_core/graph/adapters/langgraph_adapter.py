"""LangGraph StateGraph exporter for MiniGraph/CompiledGraph topologies.

Zero-dependency at import time: ``langgraph`` is only imported inside
:meth:`LangGraphExporter.to_langgraph`, at call time, never at module load.

Built against the real public topology surface added to
``perpetua_core.graph.engine`` (``CompiledGraph.nodes`` / ``.edges``, both
read-only ``Mapping`` views) rather than private ``_nodes``/``_edges``
attributes or a nonexistent ``.entrypoint``/``.conditional_edges`` split.
MiniGraph's ``Edge`` type is a union (``str | Callable[[PerpetuaState], str]``)
stored in ONE ``edges`` mapping — static vs. conditional routing is
distinguished with ``isinstance(edge, str)`` at export time, not by two
separate structures.

**Topology-only export, not scheduler-semantics parity.** This exporter
moves node functions and edge topology into LangGraph's own execution
engine; it does not replicate MiniGraph's canonical scheduler
(``CompiledGraph._run``). Concretely, the exported graph runs under
LangGraph's semantics for: interrupt handling (MiniGraph's
``_is_interrupt``/``_interrupted_state`` machinery does not exist in
LangGraph and is not reproduced here), ``nodes_visited`` bookkeeping
(MiniGraph auto-appends to ``state.nodes_visited`` before every node call;
LangGraph does not, so an exported graph's node functions that rely on that
side effect having already happened will not see it), and ``max_steps``
enforcement (MiniGraph's ``MaxStepsExceeded`` guard is not carried over —
use LangGraph's own recursion-limit config instead). Use this exporter when
you need MiniGraph's *topology* inside a LangGraph deployment (e.g. LangGraph
Cloud, a LangGraph supervisor); do not assume identical run-time behavior to
``MiniGraph.ainvoke()``.
"""
from __future__ import annotations

from typing import Any

from perpetua_core.graph.engine import END, START, CompiledGraph, MiniGraph


class LangGraphExporter:
    @staticmethod
    def to_langgraph(graph: MiniGraph | CompiledGraph, state_schema: type[Any]) -> Any:
        """Convert a MiniGraph/CompiledGraph topology into a compiled LangGraph.

        Requires ``langgraph`` to be installed in the caller's environment;
        raises a clear ``ImportError`` otherwise rather than failing on an
        opaque import trace.
        """
        try:
            from langgraph.graph import END as LG_END
            from langgraph.graph import START as LG_START
            from langgraph.graph import StateGraph
        except ImportError as exc:
            raise ImportError(
                "langgraph is not installed. Install with `pip install langgraph` "
                "to use LangGraphExporter.to_langgraph()."
            ) from exc

        compiled: CompiledGraph = graph.compile() if isinstance(graph, MiniGraph) else graph
        builder = StateGraph(state_schema)

        for node_name, node_fn in compiled.nodes.items():
            builder.add_node(node_name, node_fn)

        for source, edge in compiled.edges.items():
            lg_source = LG_START if source == START else source
            if isinstance(edge, str):
                lg_target = LG_END if edge == END else edge
                builder.add_edge(lg_source, lg_target)
            else:
                # Conditional edge: `edge` is a Callable[[PerpetuaState], str]
                # returning a MiniGraph target name (or END). LangGraph's
                # add_conditional_edges routes by return value directly when
                # no explicit mapping is given, so END needs translating
                # inside a thin wrapper rather than passed through raw.
                def _route(state: Any, _edge: Any = edge) -> Any:
                    target = _edge(state)
                    return LG_END if target == END else target

                builder.add_conditional_edges(lg_source, _route)

        return builder.compile()
