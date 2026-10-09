# Core adapter correction — 2026-10-09

Base: `c0795bcea810a9f7cec19e50a617bba91fe5e33d`.
Canonical [revision 3 design and evidence index](https://github.com/diazMelgarejo/orama-system/blob/main/docs/v2/references/loop-graph-compatibility-2026-10-09/README.md)
and [PT evidence plan](https://github.com/diazMelgarejo/Perpetua-Tools/blob/main/docs/plans/2026-10-09-minigraph-compatibility-evidence-plan.md)
are local publication targets until separately pushed.

The existing neutral `LangChainRunnableAdapter.abatch` now accepts `None` or a
positive non-bool integer concurrency bound. Invalid values raise
`ValueError("max_concurrency must be a positive integer")` before task creation
and graph effects, including empty batches. Synchronous `batch` delegates to
the same contract. Omitted/None bounds preserve unbounded behavior.

This deliberate fail-fast validation change fixes a deadlock and inconsistent
invalid-input behavior; it is not a claim of exact upstream error parity.
Tests cover invalid values, empty input, no effects, order and valid bounds.
The red phase observed 13 failures; the focused green phase passed 29 tests.

No scheduler, state schema, persisted record or package dependency changes.
Existing adapters stay in Core; new replacement facades/bridges belong in
Oramasys. Hardware/security remain outside the kernel. Oramasys must pin the
merged immutable Core SHA only after review and publication, then run its suite.
