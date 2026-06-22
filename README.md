# perpetua-core

Hardware-aware, local-first LLM orchestration **kernel** — the v2 microkernel that
owns state, the LLM/hardware policy, and the graph engine. Higher layers
(`oramasys`) import it; it imports nothing of theirs (one-way boundary).

## Layout

```
bin/            # thin executables (bin/test → pytest)
src/
  perpetua_core/   # the kernel package (import perpetua_core)
    config/        # hardware/model policy
    discovery/     # backend probe + registry + selector
    graph/         # engine + plugins (routing, tool_node, validator, …)
  tests/           # test suite (pytest)
docs/
  PROGRESS.md      # salvage/translation status
LICENSE
pyproject.toml     # build (hatchling, src-layout) + pytest config
```

Source lives under `src/` (PyPA src-layout); imports stay `import perpetua_core`
because `pyproject.toml` sets `tool.hatch.build.targets.wheel.packages = ["src/perpetua_core"]`
and pytest `pythonpath = ["src"]`.

## Develop

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest          # or: bin/test
```

Requires Python ≥ 3.11.
