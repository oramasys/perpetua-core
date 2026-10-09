# Core adapter correction — 2026-10-09

Base: `c0795bcea810a9f7cec19e50a617bba91fe5e33d`.
Canonical [revision 3 design and evidence index](https://github.com/diazMelgarejo/orama-system/blob/docs/loop-graph-compatibility-r3/docs/v2/references/loop-graph-compatibility-2026-10-09/README.md)
and [PT evidence plan](https://github.com/diazMelgarejo/Perpetua-Tools/blob/docs/loop-graph-compatibility-evidence-r3/docs/plans/2026-10-09-minigraph-compatibility-evidence-plan.md)
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

## Revision 4 correction and verification

The design/evidence links above now point to open PR branches, not merged main.
Their earlier publication-target wording describes the revision 3 cutoff.
Core #8 additionally fixes ConditionalEdge router/path_map export and preserves
broken transitive import failures. It includes mandatory dependency-free wiring
tests, checked no-eager-import rules and the delayed-first-input order regression.

Python 3.12.14 full suite: 180 passed, 87.98% coverage with real LG installed;
framework-free suite: 176 passed, 1 optional-module skip, 87.88% coverage.
The mutation script rejects completion-order results for None/2/100; serial 1
is the control. Core's structural schema and scheduler remain unchanged.
See [current decisions](https://github.com/diazMelgarejo/orama-system/blob/docs/loop-graph-compatibility-r3/docs/v2/references/loop-graph-compatibility-2026-10-09/EXECUTION-REVISION-4.md).
