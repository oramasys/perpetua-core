# T2 observer historical branch reuse review

Reviewed baseline: `6d75cfe08af95992dddffab13ae3019aa9c41350`.
Historical source: `722b8d8515875bf8fe1190fe18f412f892c98e69`, the final
observer correction in the retained T2 review lineage.

## Disposition

The observer correction is already implemented in this baseline. No runtime
replay is required. The historical source remains useful as provenance for
the delivery and cleanup failure contract.

The production-file patches in the historical source and merged
`1e9e53fd60c663596723a1cabb7f2d3f8b71ca6b` have the same stable patch ID:
`1d4446acd88c7d154559388653fb55cd7fb91e8b`. The comparison was scoped to
`src/perpetua_core/graph/plugins/observer.py`; it does not claim that the two
entire repository trees are identical.

## Retained behavior

`run_with_plugins` closes the observation iterator when delivery, a listener,
or graph iteration fails. If closure succeeds, the original failure propagates
unchanged. If closure also fails, both exceptions remain visible in an ordered
`BaseExceptionGroup`, with the primary failure first and cleanup failure second.
Cancellation remains represented as a `BaseException`, rather than being lost
through an `Exception`-only catch.

Callers that need to handle a delivery failure within a simultaneous cleanup
failure must use `except* CriticalDeliveryFailed` or inspect the exception
group. A plain `except CriticalDeliveryFailed` handles the ungrouped case only.

The earlier lineage's settlement lifecycle, fanout correlation, optional
delivery and dispatch gate, canonical delta publication, settlement failure
observation, durable-start gate, and iterator closure are also represented
in current main. Historical files should not replace their later refinements.

## Verification

The review independently compared the production patch IDs and reran the
baseline tests on Python 3.13:

- `python3 -m pytest -q src/tests/graph/plugins/test_observer.py`: 10 passed.
- `python3 -m pytest -q`: 301 passed, with 13 existing policy-deprecation warnings.

These are local source tests, not installed-consumer qualification or approval
to promote a dependency pin, enable serving, or advance a deployment gate.

## References

- [Current observer contract](../src/perpetua_core/graph/plugins/observer.py)
- [Observer regression tests](../src/tests/graph/plugins/test_observer.py)
- [Merged correction](https://github.com/oramasys/perpetua-core/commit/1e9e53fd60c663596723a1cabb7f2d3f8b71ca6b)
- [Compatibility review](COMPATIBILITY_REVIEW_2026-10-09.md)
