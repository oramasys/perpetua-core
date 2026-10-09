"""Pure folds for concurrent branch writes in a fan-out region.

Reducers are deterministic functions of ``(base, contributions)``. Contributions
arrive in canonical order (ascending branch name), never completion order. This
module has no scheduling, I/O or policy; it only decides the value a field ends
up with, or refuses.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from copy import deepcopy
from typing import Any

from perpetua_core.graph.spec import REDUCER_KINDS, ReducerKind

CustomReducerFn = Callable[[Any, tuple[Any, ...]], Any]


class ReducerConflict(ValueError):
    """Two branches wrote a field in a way its reducer refuses to resolve."""

    def __init__(self, field: str, branches: Sequence[str], reason: str) -> None:
        self.field = field
        self.branches = tuple(branches)
        self.reason = reason
        super().__init__(
            f"reducer conflict on field {field!r} "
            f"(branches {', '.join(self.branches)}): {reason}"
        )


@dataclass(frozen=True, slots=True)
class Reducer:
    """Runtime reducer binding; the spec records only ``kind`` and ``fn``'s ref."""

    kind: ReducerKind = "reject_conflict"
    fn: CustomReducerFn | None = None

    def __post_init__(self) -> None:
        if self.kind not in REDUCER_KINDS:
            raise ValueError(f"unsupported reducer kind: {self.kind!r}")
        if self.kind == "custom" and not callable(self.fn):
            raise ValueError("custom reducer requires a callable fn")
        if self.kind != "custom" and self.fn is not None:
            raise ValueError("only a custom reducer may set fn")


DEFAULT_REDUCER = Reducer("reject_conflict")


def reduce_field(
    field: str,
    reducer: Reducer,
    base: Any,
    contributions: Sequence[tuple[str, Any]],
    *,
    contribution_semantics: bool,
) -> Any:
    """Fold one field's branch writes into its committed value.

    ``contribution_semantics`` is True when the reducer was declared for the
    field. A contribution is then an increment (``concat`` appends it to the
    base). When False, the field has no declared reducer, so values are
    replacements and only agreement is accepted.
    """
    names = tuple(name for name, _ in contributions)
    values = tuple(value for _, value in contributions)
    kind = reducer.kind

    if kind == "reject_conflict":
        first = values[0]
        for _, value in contributions[1:]:
            if value != first:
                reason = "branches wrote different values"
                if not contribution_semantics:
                    reason += " and no reducer is declared for the field"
                raise ReducerConflict(field, names, reason)
        return first
    if kind == "first":
        return values[0]
    if kind == "last":
        return values[-1]
    if kind == "concat":
        return _concat(field, base, contributions)
    if kind == "union":
        return _union(field, base, contributions)
    assert reducer.fn is not None
    # A contract-violating custom fold must not mutate the pre-commit snapshot
    # or another field's contributions before raising or returning.
    return reducer.fn(deepcopy(base), deepcopy(values))


def _require_list(field: str, who: str, value: Any, kind: str) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(
            f"{kind} reducer for field {field!r} requires list values; "
            f"{who} is {type(value).__name__}"
        )
    return list(value)


def _concat(field: str, base: Any, contributions: Sequence[tuple[str, Any]]) -> list[Any]:
    result = _require_list(field, "the base value", base if base is not None else [], "concat")
    for name, value in contributions:
        result.extend(_require_list(field, f"branch {name!r}", value, "concat"))
    return result


def _union(field: str, base: Any, contributions: Sequence[tuple[str, Any]]) -> Any:
    kinds = {isinstance(value, dict) for _, value in contributions}
    if kinds == {True}:
        return _union_dict(field, base, contributions)
    if kinds == {False}:
        result = _require_list(field, "the base value", base if base is not None else [], "union")
        for name, value in contributions:
            for item in _require_list(field, f"branch {name!r}", value, "union"):
                if item not in result:
                    result.append(item)
        return result
    raise TypeError(f"union reducer for field {field!r} mixes dict and list values")


def _union_dict(
    field: str, base: Any, contributions: Sequence[tuple[str, Any]]
) -> dict[str, Any]:
    if base is not None and not isinstance(base, dict):
        raise TypeError(
            f"union reducer for field {field!r} requires a dict base; "
            f"got {type(base).__name__}"
        )
    result: dict[str, Any] = dict(base or {})
    written: dict[Any, tuple[str, Any]] = {}
    for name, value in contributions:
        for key, item in value.items():
            if key in written and written[key][1] != item:
                raise ReducerConflict(
                    field,
                    (written[key][0], name),
                    f"key {key!r} written with different values",
                )
            written.setdefault(key, (name, item))
            result[key] = item
    return result
