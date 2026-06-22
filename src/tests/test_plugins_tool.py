"""TDD: @tool decorator plugin."""
import pytest
from pydantic import BaseModel
from perpetua_core.graph.plugins.tool import tool


def test_tool_decorator_exposes_input_schema():
    @tool
    def my_tool(query: str, max_results: int = 5) -> str:
        return query

    assert hasattr(my_tool, "_input_schema")
    schema = my_tool._input_schema
    assert issubclass(schema, BaseModel)


def test_tool_schema_field_names():
    @tool
    def search(query: str, limit: int) -> list:
        return []

    schema_fields = search._input_schema.model_fields
    assert "query" in schema_fields
    assert "limit" in schema_fields


def test_tool_name_attribute():
    @tool
    def run_shell(cmd: str) -> str:
        return cmd

    assert run_shell._tool_name == "run_shell"


def test_tool_with_optional_param():
    @tool
    def fetch(url: str, timeout: float = 30.0) -> str:
        return url

    model = fetch._input_schema
    instance = model(url="http://example.com")
    assert instance.timeout == 30.0


def test_tool_validation_rejects_wrong_type():
    @tool
    def typed_fn(count: int) -> str:
        return str(count)

    with pytest.raises(Exception):
        typed_fn._input_schema(count="not_an_int")
