"""ToolNode — wrap a CLI subprocess as a graph node. Idiomatic async."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from perpetua_core.state import PerpetuaState

_TIMEOUT_S = 60.0


class ToolNodeError(RuntimeError):
    def __init__(self, returncode: int, stderr: str):
        super().__init__(f"tool exited {returncode}: {stderr.strip()}")
        self.returncode = returncode
        self.stderr = stderr


@dataclass
class ToolNode:
    argv: list[str] | None = None
    argv_template: list[str] | None = None
    output_key: str = "tool_out"
    timeout: float = _TIMEOUT_S
    env: dict[str, str] = field(default_factory=dict)

    async def __call__(self, state: PerpetuaState) -> dict:
        if self.argv_template:
            argv = [a.format(**state.model_dump()) for a in self.argv_template]
        elif self.argv:
            argv = list(self.argv)
        else:
            raise ValueError("ToolNode requires argv or argv_template")

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**self._inherited_env(), **self.env} if self.env else None,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise ToolNodeError(-1, "timeout")

        if proc.returncode != 0:
            raise ToolNodeError(proc.returncode, stderr_b.decode("utf-8", errors="replace"))

        return {"scratchpad": {**state.scratchpad,
                               self.output_key: stdout_b.decode("utf-8").strip()}}

    @staticmethod
    def _inherited_env() -> dict[str, str]:
        import os
        return dict(os.environ)
