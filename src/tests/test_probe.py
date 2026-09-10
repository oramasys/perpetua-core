"""Real regression tests for health_probe's Telos-routed transport.

Uses a genuine local socket server for the loopback-success case (not a
mock of the request function) so the test exercises the actual Telos
transport path end-to-end.
"""
from __future__ import annotations

import inspect
import json
import socket
import threading

import pytest

from perpetua_core.discovery.backend import BackendHealth
from perpetua_core.discovery import probe as probe_module
from perpetua_core.discovery.probe import health_probe


def _serve_once(sock: socket.socket, status_line: str, body: bytes) -> None:
    conn, _ = sock.accept()
    conn.recv(4096)
    headers = (
        f"{status_line}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode()
    conn.sendall(headers + body)
    conn.close()
    sock.close()


def _start_server(status_line: str, body: bytes) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    thread = threading.Thread(
        target=_serve_once,
        args=(sock, status_line, body),
        daemon=True,
    )
    thread.start()
    return port


def test_probe_has_no_core_owned_always_allow_authorizer() -> None:
    source = inspect.getsource(probe_module)

    assert "AlwaysAllowHealthProbeAuthorizer" not in source
    assert "EndpointUseDecision(" not in source
    assert "EndpointAuthorizer.from_exact_rules" in source


@pytest.mark.asyncio
async def test_health_probe_succeeds_against_genuine_loopback_server():
    body = json.dumps({"data": [{"id": "llama3"}]}).encode()
    port = _start_server("HTTP/1.1 200 OK", body)
    result = await health_probe(f"http://127.0.0.1:{port}/v1")
    assert result.health == BackendHealth.ONLINE
    assert result.models == ("llama3",)


@pytest.mark.asyncio
async def test_health_probe_reports_offline_on_non_200():
    port = _start_server("HTTP/1.1 500 Internal Server Error", b"")
    result = await health_probe(f"http://127.0.0.1:{port}/v1")
    assert result.health == BackendHealth.OFFLINE
    assert result.models == ()


@pytest.mark.asyncio
async def test_health_probe_reports_degraded_on_malformed_json():
    port = _start_server("HTTP/1.1 200 OK", b"not json")
    result = await health_probe(f"http://127.0.0.1:{port}/v1")
    assert result.health == BackendHealth.DEGRADED


@pytest.mark.asyncio
async def test_health_probe_rejects_public_address_without_attempting_a_connection():
    """A literal public IP must be rejected by Telos before transport dispatch."""
    import time

    start = time.monotonic()
    result = await health_probe("http://93.184.216.34/v1")
    elapsed = time.monotonic() - start
    assert result.health == BackendHealth.OFFLINE
    assert result.models == ()
    assert elapsed < 1.0, (
        f"rejection took {elapsed:.2f}s -- too slow for a local policy check, "
        "suggests this passed due to network unreachability, not Telos policy"
    )
