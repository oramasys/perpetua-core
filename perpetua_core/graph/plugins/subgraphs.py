"""Subgraph composition plugin — expose a MiniGraph as a single node."""
from __future__ import annotations
from perpetua_core.state import PerpetuaState


def as_node(subgraph):
    """Wrap a MiniGraph as a node callable for use in a parent graph."""
    async def node(state: PerpetuaState) -> dict:
        result = await subgraph.ainvoke(state)
        return result.model_dump()
    node.__name__ = getattr(subgraph, "__name__", "subgraph")
    return node
