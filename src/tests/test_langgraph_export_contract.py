"""Dependency-free wiring tests; real semantics are tested in the oracle suite."""
from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from perpetua_core.graph.adapters.langgraph_adapter import LangGraphExporter
from perpetua_core.graph.engine import END, START, ConditionalEdge, MiniGraph
from perpetua_core.state import PerpetuaState


def test_declared_targets_and_router_survive_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catch lost path maps and incorrectly calling a ConditionalEdge wrapper."""
    calls: dict[str, Any] = {"nodes": [], "edges": [], "conditional": []}

    class Builder:
        """Capture the outbound API without claiming upstream runtime parity."""

        def __init__(self, schema: type[Any]) -> None:
            """Record the caller's exact state schema."""
            calls["schema"] = schema

        def add_node(self, name: str, fn: Any) -> None:
            """Record node registration."""
            calls["nodes"].append(name)

        def add_edge(self, source: str, target: str) -> None:
            """Record sentinel translation."""
            calls["edges"].append((source, target))

        def add_conditional_edges(self, source: str, router: Any, path_map: Any = None) -> None:
            """Capture the router and its finite target map."""
            calls["conditional"].append((source, router, path_map))

        def compile(self) -> object:
            """Return the captured builder as the compiled sentinel."""
            return self

    package = types.ModuleType("langgraph")
    module = types.ModuleType("langgraph.graph")
    module.START, module.END, module.StateGraph = "lg_start", "lg_end", Builder
    monkeypatch.setitem(sys.modules, "langgraph", package)
    monkeypatch.setitem(sys.modules, "langgraph.graph", module)
    graph = MiniGraph().add_node("a", lambda state: {}).add_node("b", lambda state: {})
    graph.add_edge(START, "a")
    graph.add_edge("a", ConditionalEdge(lambda state: END if state.metadata.get("done") else "b", ("b",)))
    graph.add_edge("b", END)
    assert isinstance(LangGraphExporter.to_langgraph(graph, PerpetuaState), Builder)
    assert calls["nodes"] == ["a", "b"]
    assert calls["edges"] == [("lg_start", "a"), ("b", "lg_end")]
    source, router, paths = calls["conditional"][0]
    assert source == "a"
    assert paths == {"a": "a", "b": "b", "lg_end": "lg_end"}
    assert router(PerpetuaState(session_id="x")) == "b"
    assert router(PerpetuaState(session_id="x", metadata={"done": True})) == "lg_end"


@pytest.mark.parametrize("name", ["langgraph", "broken_transitive"])
def test_optional_import_failure_contract_without_framework(monkeypatch, name) -> None:
    """Missing root gets installation help; broken dependencies retain their identity."""
    import builtins
    original = builtins.__import__
    failure = ModuleNotFoundError("planted failure", name=name)
    def broken(module, *args, **kwargs):
        """Raise at the lazy import irrespective of upstream installation."""
        if module == "langgraph.graph":
            raise failure
        return original(module, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", broken)
    with pytest.raises(ModuleNotFoundError) as error:
        LangGraphExporter.to_langgraph(MiniGraph(), PerpetuaState)
    assert error.value.name == name
    if name == "langgraph":
        assert "pip install langgraph" in str(error.value)
    else:
        assert error.value is failure
