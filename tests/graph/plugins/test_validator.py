import pytest
from pydantic import BaseModel, Field
from perpetua_core.state import PerpetuaState
from perpetua_core.graph.plugins.validator import Validated, ValidationError


class HasModelHint(BaseModel):
    model_hint: str = Field(min_length=1)


class HasResponse(BaseModel):
    response: str = Field(min_length=1)


@pytest.mark.asyncio
async def test_validator_passes_state_through_when_valid():
    async def inner(state):
        return {"scratchpad": {**state.scratchpad, "response": "ok"}}
    node = Validated(inner, pre=HasModelHint, post=HasResponse)
    state = PerpetuaState(session_id="t1", model_hint="qwen3-coder-30b")
    delta = await node(state)
    assert delta["scratchpad"]["response"] == "ok"


@pytest.mark.asyncio
async def test_validator_raises_when_precondition_fails():
    async def inner(state):
        return {}
    node = Validated(inner, pre=HasModelHint, post=HasResponse)
    state = PerpetuaState(session_id="t1", model_hint=None)
    with pytest.raises(ValidationError, match="pre"):
        await node(state)


@pytest.mark.asyncio
async def test_validator_raises_when_postcondition_fails():
    async def inner(state):
        return {"scratchpad": {"response": ""}}  # fails post (min_length=1)
    node = Validated(inner, pre=HasModelHint, post=HasResponse)
    state = PerpetuaState(session_id="t1", model_hint="x")
    with pytest.raises(ValidationError, match="post"):
        await node(state)
