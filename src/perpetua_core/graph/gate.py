"""Neutral dispatch gate seam for the one graph scheduler (R4 T1).

Core owns only this protocol and the places ``CompiledGraph._run`` calls it. It does
not know about authority, leases, budgets, stop state or delivery health: an
implementation supplied per invocation (Oramasys) decides those, in its own fixed
order, and returns a :class:`GateDecision`.

Boundaries guarded (``DispatchRequest.boundary``):

- ``node``     before a single node callable runs
- ``router``   before a conditional edge router runs
- ``fanout``   before a fan-out batch starts; one decision for the whole batch, so a
               refusal starts no branch (all-or-nothing)
- ``join``     before a custom join callable runs
- ``reducer``  before admitted branch deltas are folded

Commit boundaries (``CommitRequest.boundary``): ``node`` before a node delta is
merged, ``region`` before a fan-out region commits. After an allowed commit decision
the scheduler publishes synchronously, with no ``await`` in between.

Dispatch decisions are taken after the last observation yield and immediately before
the guarded call, so a stop or revocation recorded while a consumer holds the
observation iterator is honoured. ``steps`` on join, reducer and region-commit requests
already counts the branches that settled successfully.

A gate never cancels in-flight work and cannot undo an effect a callable already
caused: settle-all semantics are unchanged. With no gate, behaviour and the event
sequence are exactly the pre-T1 ones.

Gates may also implement :class:`DurableStartGate`. Core detects that optional
extension structurally and awaits it only after the matching dispatch request is
allowed, immediately before the actual callable receives control. It is separate
from :class:`DispatchGate` so existing gates do not acquire a mandatory method.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias, runtime_checkable

Verdict: TypeAlias = Literal["allow", "refuse", "stop"]
DispatchBoundary: TypeAlias = Literal["node", "router", "fanout", "join", "reducer"]
CommitBoundary: TypeAlias = Literal["node", "region"]

_VERDICTS = ("allow", "refuse", "stop")


@dataclass(frozen=True, slots=True)
class GateDecision:
    """``allow``, ``refuse(reason)`` or ``stop(reason)``. Reasons are required codes."""

    verdict: Verdict
    reason: str | None = None

    def __post_init__(self) -> None:
        """Validate the fields at construction and fail closed on bad input."""
        if self.verdict not in _VERDICTS:
            raise ValueError(f"unknown gate verdict: {self.verdict!r}")
        if self.verdict == "allow":
            if self.reason is not None:
                raise ValueError("an allow decision carries no reason")
        elif not isinstance(self.reason, str) or not self.reason:
            raise ValueError(f"a {self.verdict} decision needs a reason code")

    @classmethod
    def allow(cls) -> GateDecision:
        """Build an allow decision."""
        return cls("allow")

    @classmethod
    def refuse(cls, reason: str) -> GateDecision:
        """Build a refusal carrying a required reason code."""
        return cls("refuse", reason)

    @classmethod
    def stop(cls, reason: str) -> GateDecision:
        """Build a stop decision carrying a required reason code."""
        return cls("stop", reason)


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    """What is about to execute. ``steps`` counts completed node executions.

    ``batch_id`` is an opaque, scheduler-minted correlation value for one
    fan-out region. Core only passes it through; a gate may use it to connect
    dispatch-time accounting with the later region publication.
    """

    boundary: DispatchBoundary
    nodes: tuple[str, ...]
    steps: int
    source: str | None = None
    batch_id: str | None = None


@dataclass(frozen=True, slots=True)
class CommitRequest:
    """What is about to be published into graph state.

    ``result_digest`` is a Core-neutral canonical JSON digest of the pending
    delta. It lets an external gate bind durable commit preparation and
    settlement to the exact content Core publishes without teaching the
    scheduler about holds, budgets, or terminal outcomes.
    """

    boundary: CommitBoundary
    nodes: tuple[str, ...]
    steps: int
    source: str | None = None
    result_digest: str | None = None
    batch_id: str | None = None


@runtime_checkable
class DispatchGate(Protocol):
    """Implemented outside Core; bound to one invocation (run, lease, epoch)."""

    async def before_dispatch(self, request: DispatchRequest) -> GateDecision:
        """Decide whether the guarded call may start."""

    async def before_commit(self, request: CommitRequest) -> GateDecision:
        """Decide whether the pending delta may be published."""


@runtime_checkable
class DurableStartGate(Protocol):
    """Optional extension for persisting an admitted callable's start marker."""

    async def before_start(self, request: DispatchRequest) -> None:
        """Persist start evidence immediately before callable control transfer."""


class GateRefused(RuntimeError):
    """A gate refused a boundary. Nothing at that boundary was published."""

    def __init__(self, decision: GateDecision, request: DispatchRequest | CommitRequest) -> None:
        """Keep the refusing decision and the request it refused."""
        self.decision = decision
        self.request = request
        super().__init__(f"gate refused {request.boundary} {request.nodes!r}: {decision.reason}")


class GateStopped(RuntimeError):
    """Raised only by callers that want a stop as an exception; the scheduler itself
    ends the run with a ``stopped`` terminal event instead."""

    def __init__(self, decision: GateDecision) -> None:
        """Keep the stop decision that ended the run."""
        self.decision = decision
        super().__init__(f"gate stopped the run: {decision.reason}")


def checked(decision: object) -> GateDecision:
    """Fail closed on anything that is not a :class:`GateDecision`."""
    if not isinstance(decision, GateDecision):
        raise TypeError(f"gate returned {type(decision).__name__}; expected GateDecision")
    return decision
