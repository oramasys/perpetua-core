# Post-merge convergence — 2026-08-29

## Status

The MiniGraph reconciliation merged to `main` as:

```text
d1c0dfca12fef5df6e6b15c602e765e299279676
```

A fresh post-merge comparison against the Orama architecture records found two
execution-boundary gaps that were not covered by the merged regression suite.
This document records the corrective branch and keeps code/documentation status
explicit.

Corrective branch:

```text
2026-08-29-001-post-merge-convergence
```

This branch is **not merged** merely because it exists or is documented here.
Integration requires explicit operator authorization and exact-head verification.

## 1. Unknown routes fail at the routing boundary

Merged behavior rejected non-string and empty routes but allowed an unknown node
name to leave `_resolve_edge()`. The later `_node_for()` lookup then raised a
`KeyError`.

The corrective contract is fail-closed at route resolution:

```text
target == END
  -> allowed

target in registered nodes
  -> allowed

otherwise
  -> ValueError at route resolution
```

Implementation commit:

```text
84120f2ee529333ea02cb94e149df7a26fe404b5
```

## 2. Plugin payloads are isolated per listener

The merged dispatcher correctly uses one `aobserve()` drain, delivers every
`GraphObservation` to every plugin in deterministic registration order, awaits
returned awaitables, and fails closed on plugin errors.

However, every plugin received the same rich observation object. The observation
dataclass is frozen only at the top level; its `PerpetuaState` collections and
optional `delta` remain mutable Python objects. A mutating listener could
therefore affect later listeners or the scheduler's live state.

The corrective dispatcher creates a detached payload for each listener:

```python
GraphObservation(
    event=observation.event,
    state=observation.state.model_copy(deep=True),
    delta=deepcopy(observation.delta),
)
```

Implementation commit:

```text
382e67edee7aab3f972e1a173e20934f10034102
```

## 3. Regression evidence

Corrective tests cover both gaps:

```text
src/tests/graph/test_post_merge_convergence.py
```

They prove:

- an edge resolving to an unregistered node raises at the routing boundary;
- a plugin that mutates its received state/delta cannot poison a later plugin;
- the same mutating plugin cannot alter the final live graph state.

Test commit:

```text
6615bf37cca164d0e13a710c7ac54ffa2a334e49
```

## 4. Canonical execution boundary

The converged architecture is:

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
aobserve()                   GraphEvent
rich trusted                     |
projection                        v
                              asteps()
                              sanitized
```

`run_with_plugins()` drains `aobserve()` once. The plugin contract is the generic
`on_observation(observation)` callback, so `edge.selected`, `node.start`,
`node.end`, `interrupt`, and `done` are all representable. Callback return values
are inspected and awaited when awaitable. Default authoritative delivery is
fail-closed.

## 5. State isolation remains two-layer

`PerpetuaState.merge(delta)` remains:

```python
self.model_copy(update=deepcopy(delta), deep=True)
```

The layers close different alias classes:

- `deep=True` isolates inherited nested mutable state;
- `deepcopy(delta)` isolates caller-owned mutable values supplied in the update.

This state-generation isolation is separate from the new per-listener
observation isolation; both boundaries are required.

## 6. Documentation authority

Current Orama architecture records own the cross-repository specification and
policy boundary. This repository owns executable field-level MiniGraph behavior.
When explanatory copies drift, tested core behavior and current canonical Orama
records must be reconciled together rather than allowing two normative stories.

Historical `docs/PROGRESS.md` remains a dated RC-1 salvage ledger from May 2026.
Its branch/push instructions are historical workflow evidence, not the current
post-merge integration status recorded here.
