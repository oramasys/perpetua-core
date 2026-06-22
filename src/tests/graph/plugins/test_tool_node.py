import pytest
from perpetua_core.state import PerpetuaState
from perpetua_core.graph.plugins.tool_node import ToolNode, ToolNodeError


@pytest.mark.asyncio
async def test_tool_node_captures_stdout():
    node = ToolNode(argv=["bash", "-c", "echo hello-from-tool"], output_key="cli_out")
    delta = await node(PerpetuaState(session_id="t1"))
    assert delta["scratchpad"]["cli_out"] == "hello-from-tool"


@pytest.mark.asyncio
async def test_tool_node_raises_on_nonzero_exit():
    node = ToolNode(argv=["bash", "-c", "exit 7"], output_key="cli_out")
    with pytest.raises(ToolNodeError) as exc:
        await node(PerpetuaState(session_id="t1"))
    assert exc.value.returncode == 7


@pytest.mark.asyncio
async def test_tool_node_supports_argv_templating_from_state():
    node = ToolNode(
        argv_template=["bash", "-c", "echo session={session_id}"],
        output_key="cli_out",
    )
    delta = await node(PerpetuaState(session_id="abc123"))
    assert delta["scratchpad"]["cli_out"] == "session=abc123"
