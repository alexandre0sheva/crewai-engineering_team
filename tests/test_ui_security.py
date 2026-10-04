"""The UI server's guard: hosts, origins, the custom header, tokens, and size limits."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from ui_helpers import API, make_client  # noqa: E402

from engineering_team.ui.security import Security, hostname_of  # noqa: E402

TOKEN = "t" * 40


def remote_client(**headers: str) -> TestClient:
    client, _ = make_client(security=Security(remote=True, token=TOKEN))
    client.base_url = "http://192.168.1.20:8765"  # type: ignore[assignment]
    client.headers.update(headers)
    return client


@pytest.mark.parametrize(
    ("header", "name"),
    [
        ("localhost:8765", "localhost"),
        ("LOCALHOST", "localhost"),
        ("127.0.0.1:80", "127.0.0.1"),
        ("[::1]:8765", "[::1]"),
        ("evil.example:8765", "evil.example"),
    ],
)
def test_hostname_of(header: str, name: str) -> None:
    assert hostname_of(header) == name


def test_a_hostname_the_server_does_not_own_is_refused_in_local_mode() -> None:
    client, _ = make_client()

    assert client.get(f"{API}/health").status_code == 200
    rebound = client.get(f"{API}/runs", headers={"Host": "evil.example:8765"})

    assert rebound.status_code == 403 and "localhost" in rebound.json()["error"]
    assert client.get(f"{API}/runs", headers={"Host": "127.0.0.1:8765"}).status_code == 200
    assert client.get(f"{API}/runs", headers={"Host": "[::1]:8765"}).status_code == 200


def test_cross_origin_requests_are_refused_and_same_origin_ones_are_not() -> None:
    client, _ = make_client()

    foreign = client.get(f"{API}/runs", headers={"Origin": "http://evil.example"})
    opaque = client.get(f"{API}/runs", headers={"Origin": "null"})
    same = client.get(f"{API}/runs", headers={"Origin": "http://localhost"})
    site = client.get(f"{API}/runs", headers={"Sec-Fetch-Site": "cross-site"})

    assert foreign.status_code == opaque.status_code == site.status_code == 403
    assert same.status_code == 200


def test_the_server_never_sends_cors_headers_and_refuses_preflight() -> None:
    client, _ = make_client()

    plain = client.get(f"{API}/health", headers={"Origin": "http://localhost"})
    preflight = client.options(
        f"{API}/runs",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-engineering-team",
        },
    )

    assert not any(name.lower().startswith("access-control-") for name in plain.headers)
    assert preflight.status_code in (403, 405)
    assert "access-control-allow-origin" not in preflight.headers


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_mutating_calls_need_the_custom_header(method: str) -> None:
    client, _ = make_client()
    del client.headers["X-Engineering-Team"]

    response = getattr(client, method)(f"{API}/runs/20261003-120000-abc123/cancel")

    assert response.status_code == 403 and "X-Engineering-Team" in response.json()["error"]


def test_reads_do_not_need_the_custom_header() -> None:
    client, _ = make_client()
    del client.headers["X-Engineering-Team"]

    assert client.get(f"{API}/runs").status_code == 200


def test_remote_mode_requires_the_bearer_token_everywhere_but_health() -> None:
    client = remote_client()

    assert client.get(f"{API}/health").json() == {
        "ok": True, "version": client.get(f"{API}/health").json()["version"], "auth": "bearer"
    }  # fmt: skip
    assert client.get(f"{API}/runs").status_code == 401
    assert client.get(f"{API}/runs", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get(f"{API}/runs", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
    assert (
        client.get(f"{API}/runs", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    )
    assert client.get(f"{API}/runs?token={TOKEN}").status_code == 401  # never accepted in a URL
    assert client.post(f"{API}/runs", json={}).status_code == 401  # auth first, then the rest


def test_remote_mode_still_refuses_cross_origin_requests() -> None:
    client = remote_client(Authorization=f"Bearer {TOKEN}")

    response = client.get(f"{API}/runs", headers={"Origin": "http://evil.example"})

    assert response.status_code == 403


def test_local_mode_reports_no_auth() -> None:
    client, _ = make_client()

    assert client.get(f"{API}/health").json()["auth"] == "none"


def test_a_remote_server_without_a_token_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="token"):
        Security(remote=True, token=None)


def test_a_json_body_over_the_limit_is_refused_before_it_is_read() -> None:
    client, launcher = make_client(security=Security(max_request_bytes=1000))

    response = client.post(f"{API}/runs", content=b"{" + b" " * 5000 + b"}",
                           headers={"Content-Type": "application/json"})  # fmt: skip

    assert response.status_code == 413 and "limit" in response.json()["error"]
    assert launcher.records() == []


def test_a_chunked_body_without_a_length_is_refused() -> None:
    client, _ = make_client()

    def chunks():  # noqa: ANN202
        yield b'{"mode": "new"}'

    response = client.post(f"{API}/runs", content=chunks(),
                           headers={"Content-Type": "application/json"})  # fmt: skip

    assert response.status_code == 411


def test_an_upload_over_its_own_limit_is_refused() -> None:
    client, launcher = make_client(security=Security(max_upload_bytes=10_000))
    big = b"x" * 200_000

    response = client.post(
        f"{API}/runs",
        data={"spec": '{"mode": "new"}'},
        files=[("request_files", ("request.md", big, "text/markdown"))],
    )

    assert response.status_code == 413
    assert launcher.records() == []


def test_unknown_routes_and_methods_answer_in_json() -> None:
    client, _ = make_client()

    missing = client.get(f"{API}/nope")
    wrong = client.put(f"{API}/runs")

    assert missing.status_code == 404 and "error" in missing.json()
    assert wrong.status_code == 405 and "error" in wrong.json()
