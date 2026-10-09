# perpetua-core

Dependency-minimal execution **kernel** for Perpetua v2. It owns state-generation
mechanics, MiniGraph execution semantics, and small reusable execution plugins.
Higher layers may import it; the kernel does not depend back on Orama policy or
application composition.

Cross-repository authority is intentionally split:

- `oramasys/perpetua-core` owns structural GraphSpec/NodeSpec/EdgeSpec,
  graph identity and universal execution mechanics;
- `oramasys/oramasys` owns application graphs and restrict-only graph policy;
- `diazMelgarejo/orama-system` records normative decisions, evaluation and
  workflow methodology; it does not own a second runtime GraphSpec;
- `oramasys/agate` owns cold-local-metal hardware capability, affinity, fit,
  placement, and readiness policy;
- provider/runtime adapters own provider health, loaded-model state, and
  provider-native runtime behavior.

The repository still contains hardware/discovery helpers inherited from earlier
salvage work. Their presence does not make this package the cross-repository
hardware-policy authority.

R3 adds bounded single-level regions, not full LangGraph/LangChain replacement.
The export adapter explicitly refuses fan-out regions; dynamic Send, nested
regions and durable replay remain deferred. The LangChain adapter is protocol
shaped, with buffered synchronous streaming and partial config support, not a
drop-in implementation of every upstream Runnable API. Pydantic AI integration
belongs in Oramasys and remains an explicitly gated offline bridge.

`SqliteCheckpointer.load_latest()` reads state only. `resume_policy()` changes
scratchpad values only. Calling `ainvoke()` on that state starts at START again;
none of these functions provides an authorized or idempotent effect replay.

## Current MiniGraph contract

The canonical runtime boundary is:

```text
CompiledGraph._run()
  sole scheduler
        |
        v
GraphObservation(event, state, delta?)
        |
        +-------------------------+
        |                         |
        v                         v
     aobserve()                GraphEvent
     rich trusted                 |
                                  v
                               asteps()
                               sanitized
```

`PerpetuaState.merge()` isolates inherited nested state and caller-owned delta
values. `MiniGraph` is a mutable construction workspace; `compile()` produces a
detached execution snapshot. Unknown routes fail at route resolution on the
post-merge convergence branch. Plugin fan-out is deterministic, awaits async
callbacks, fails closed by default, and gives each listener a detached rich
observation payload on that branch.

Post-merge correction status is recorded in
[`docs/POST_MERGE_CONVERGENCE_2026-08-29.md`](docs/POST_MERGE_CONVERGENCE_2026-08-29.md).
That document and tested source are current status; `docs/PROGRESS.md` is a
historical May 2026 salvage ledger.

## Layout

```text
bin/              # thin executables (bin/test → pytest)
src/
  perpetua_core/  # kernel package (import perpetua_core)
    config/       # retained configuration examples/helpers
    discovery/    # backend probe + registry + selector
    graph/        # engine + execution plugins
  tests/          # test suite (pytest)
docs/
  POST_MERGE_CONVERGENCE_2026-08-29.md  # current corrective status
  PROGRESS.md                            # historical RC-1 salvage ledger
LICENSE
pyproject.toml     # build (hatchling, src-layout) + pytest config
```

Source lives under `src/` (PyPA src-layout); imports stay `import perpetua_core`
because `pyproject.toml` sets
`tool.hatch.build.targets.wheel.packages = ["src/perpetua_core"]` and pytest
`pythonpath = ["src"]`.

## Develop

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest          # or: bin/test
```

Requires Python ≥ 3.11.
