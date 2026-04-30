"""@tool decorator plugin — auto-derive Pydantic v2 input schema from type hints."""
from __future__ import annotations
import inspect
from typing import Any, get_type_hints
from pydantic import create_model


def tool(fn):
    """Decorate a typed function; auto-derives a Pydantic v2 input schema."""
    hints = get_type_hints(fn)
    hints.pop("return", None)
    sig = inspect.signature(fn)
    fields: dict[str, Any] = {}
    for name, param in sig.parameters.items():
        annotation = hints.get(name, Any)
        default = (
            param.default
            if param.default is not inspect.Parameter.empty
            else ...
        )
        fields[name] = (annotation, default)
    fn._input_schema = create_model(f"{fn.__name__}Input", **fields)
    fn._tool_name = fn.__name__
    return fn
