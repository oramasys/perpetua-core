from __future__ import annotations
import asyncio
from dataclasses import dataclass
from secrets import token_urlsafe
import json
from telos.contracts import EndpointPurpose, EndpointUseDecision, EndpointUseRequest
from telos.errors import EndpointPolicyError
from telos.resolver import _stdlib_resolver
from telos.transport import TransportPolicy, request
from .backend import BackendHealth

_TIMEOUT_S = 1.5


@dataclass(frozen=True, slots=True)
class ProbeResult:
    health: BackendHealth
    models: tuple[str, ...]


class _AlwaysAllowHealthProbeAuthorizer:
    """Health-probe-only authorizer: semantic allowlisting is deliberately
    not the security boundary here. Discovery is inherently dynamic
    (autodetect's seed list can change; register_by_ip is ad hoc by
    design), so there is no fixed endpoint set to check in advance.
    Transport safety is the real gate: the TransportPolicy this module
    passes sets allow_public=False unconditionally, so a health probe can
    reach loopback/private/LAN addresses only, never a public address,
    regardless of what host this authorizer allows semantically. Scoped
    to this one module's sole purpose (HEALTH_PROBE) rather than a
    general-purpose always-allow authorizer that could be misused
    elsewhere."""

    def authorize(self, req: EndpointUseRequest) -> EndpointUseDecision:
        return EndpointUseDecision(
            allowed=True,
            reason_code="allowed",
            policy_version="perpetua-core-health-probe-v1",
            decision_ref=token_urlsafe(18),
            endpoint=req.endpoint,
        )


_AUTHORIZER = _AlwaysAllowHealthProbeAuthorizer()
_POLICY = TransportPolicy(
    allow_public=False,
    allow_private=True,
    allow_loopback=True,
    require_https_for_public=True,
)


async def health_probe(base_url: str, *, timeout: float = _TIMEOUT_S) -> ProbeResult:
    url = base_url.rstrip("/") + "/models"
    try:
        response = await asyncio.to_thread(
            request,
            "GET",
            url,
            authorizer=_AUTHORIZER,
            transport_policy=_POLICY,
            actor_id="perpetua-core-discovery",
            workflow_id="health-probe",
            purpose=EndpointPurpose.HEALTH_PROBE,
            run_id=token_urlsafe(12),
            timeout=timeout,
            resolver=_stdlib_resolver,
        )
    except (EndpointPolicyError, OSError):
        return ProbeResult(BackendHealth.OFFLINE, ())
    if response.status != 200:
        return ProbeResult(BackendHealth.OFFLINE, ())
    try:
        body = json.loads(response.body)
        models = tuple(item["id"] for item in body.get("data", []) if "id" in item)
    except (ValueError, KeyError, TypeError):
        return ProbeResult(BackendHealth.DEGRADED, ())
    return ProbeResult(BackendHealth.ONLINE, models)
