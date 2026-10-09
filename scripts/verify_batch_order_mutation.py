"""Prove the order test kills a completion-order mutant; never edit source bytes."""
from __future__ import annotations

import asyncio
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from perpetua_core.graph.adapters.langchain_adapter import LangChainRunnableAdapter


async def completion_order(self, inputs, config=None):
    """Deliberately broken result ordering, preserving the configured concurrency."""
    limit = (config or {}).get("max_concurrency")
    semaphore = asyncio.Semaphore(limit) if limit else None
    async def run(item):
        """Keep the mutation scoped to ordering rather than admission."""
        if semaphore is None:
            return await self.ainvoke(item, config)
        async with semaphore:
            return await self.ainvoke(item, config)
    tasks = [asyncio.create_task(run(item)) for item in inputs]
    return [await future for future in asyncio.as_completed(tasks)]


def main():
    """Run the real PR test against baseline and mutant, restoring the method."""
    test = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                            "src/tests/test_langchain_adapter.py"))[
        "test_abatch_valid_bounds_preserve_input_order"]
    for limit in (None, 1, 2, 100):
        test(limit)
    original = LangChainRunnableAdapter.abatch
    killed = []
    try:
        LangChainRunnableAdapter.abatch = completion_order
        for limit in (None, 1, 2, 100):
            try:
                test(limit)
            except AssertionError:
                killed.append(limit)
        assert killed == [None, 2, 100], killed
    finally:
        LangChainRunnableAdapter.abatch = original
    print("baseline: 4 passed; completion-order mutant killed: None, 2, 100; serial control: 1")


if __name__ == "__main__":
    main()
