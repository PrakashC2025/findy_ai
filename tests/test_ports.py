"""`devmind ports` must tell truth from a mock server: who owns the port?"""

from __future__ import annotations

import httpx

from devmind.ports import CLOSED, LLAMA, OPENAI, WEB, find_local_model, port_of, probe_port, scan


def _handler(request: httpx.Request) -> httpx.Response:
    port = request.url.port
    if port == 8081:  # a real llama.cpp: models + health JSON
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "build": "b4242"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen2.5-coder-7b"}]})
        return httpx.Response(404)
    if port == 8082:  # OpenAI-compatible without /health
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "lmstudio-community/qwen"}]})
        return httpx.Response(404)
    if port == 8083:  # an unrelated web app, HTML everywhere
        return httpx.Response(
            404,
            text="<html><head><title>Router admin</title></head><body>no</body></html>",
            headers={"server": "nginx"},
        )
    raise httpx.ConnectError("nothing there", request=request)


def _probe(port: int):
    return probe_port(port, transport=httpx.MockTransport(_handler))


def test_llama_cpp_is_recognised() -> None:
    report = _probe(8081)
    assert report.kind == LLAMA
    assert "qwen2.5-coder-7b" in report.detail
    assert report.usable is True


def test_openai_server_without_health_is_recognised() -> None:
    report = _probe(8082)
    assert report.kind == OPENAI
    assert report.usable is True


def test_other_web_app_is_named_not_confused_with_a_model() -> None:
    report = _probe(8083)
    assert report.kind == WEB
    assert "Router admin" in report.detail
    assert "nginx" in report.detail
    assert report.usable is False


def test_free_port() -> None:
    report = _probe(8084)
    assert report.kind == CLOSED
    assert report.free is True


def test_scan_keeps_the_requested_order() -> None:
    reports = scan([8084, 8081, 8083], transport=httpx.MockTransport(_handler))
    assert [report.port for report in reports] == [8084, 8081, 8083]
    assert [report.kind for report in reports] == [CLOSED, LLAMA, WEB]


def test_find_local_model_prefers_llama_cpp() -> None:
    found = find_local_model([8084, 8082, 8081], transport=httpx.MockTransport(_handler))
    assert found is not None
    assert (found.port, found.kind) == (8081, LLAMA)


def test_find_local_model_falls_back_to_any_openai_server() -> None:
    found = find_local_model([8084, 8082], transport=httpx.MockTransport(_handler))
    assert found is not None
    assert found.kind == OPENAI


def test_find_local_model_returns_none_when_nothing_answers() -> None:
    assert find_local_model([8084, 8085], transport=httpx.MockTransport(_handler)) is None


def test_port_of_understands_defaults() -> None:
    assert port_of("http://127.0.0.1:8080") == 8080
    assert port_of("https://home-pc.example.ts.net") == 443
    assert port_of("localhost:11434") == 11434
