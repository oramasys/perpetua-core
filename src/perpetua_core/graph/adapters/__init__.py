"""Edge-boundary adapters between MiniGraph and external graph ecosystems.

Zero-core-dependency invariant: nothing under ``perpetua_core.graph.engine``
or ``perpetua_core.graph.plugins`` imports ``langchain`` or ``langgraph``.
These adapters are the only place that boundary is crossed, and only via
lazy, call-time imports inside the adapter methods that need them.
"""
from __future__ import annotations

from perpetua_core.graph.adapters.langchain_adapter import LangChainRunnableAdapter
from perpetua_core.graph.adapters.langgraph_adapter import LangGraphExporter

__all__ = ["LangChainRunnableAdapter", "LangGraphExporter"]
