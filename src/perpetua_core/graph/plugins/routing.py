"""LabelRouter — separates branch-policy (label) from topology (target node)."""
from __future__ import annotations
from typing import Callable
from perpetua_core.state import PerpetuaState

PolicyFn = Callable[[PerpetuaState], str]


class LabelRouter:
    def __init__(self, policy: PolicyFn):
        self._policy = policy
        self._labels: dict[str, str] = {}

    def register(self, label: str, node: str) -> "LabelRouter":
        self._labels[label] = node
        return self

    def as_edge(self) -> Callable[[PerpetuaState], str]:
        def edge(state: PerpetuaState) -> str:
            label = self._policy(state)
            if label not in self._labels:
                raise KeyError(f"LabelRouter: no node registered for label {label!r}")
            return self._labels[label]
        return edge
