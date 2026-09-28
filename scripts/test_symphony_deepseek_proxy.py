"""Local-only protocol tests; no DeepSeek request or real credential is used."""

from __future__ import annotations

import http.client
import json
import socket
import time
import unittest
from unittest import mock

import symphony_deepseek_proxy as proxy


_TEST_KEY = "sk-example-key-for-local-test-only"
_TEST_TOKEN = "local-example-token-for-local-test-only"


class FakeResponse:
    def __init__(self, status: int, content_type: str, chunks: list[bytes], retry_after: str | None = None):
        self.status = status
        self.content_type = content_type
        self.chunks = list(chunks)
        self.retry_after = retry_after

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return {
            "Content-Type": self.content_type,
            "Retry-After": self.retry_after,
        }.get(name) or default

    def read(self, size: int = -1) -> bytes:
        return b"".join(self.chunks)

    def read1(self, size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


class FakeConnection:
    responses: list[FakeResponse] = []
    requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []
    hosts: list[str] = []

    def __init__(self, host: str, *, timeout: int, context: object):
        self.hosts.append(host)

    def request(self, method: str, path: str, body: bytes | None, headers: dict[str, str]):
        self.requests.append((method, path, body, headers))

    def getresponse(self) -> FakeResponse:
        return self.responses.pop(0)

    def close(self) -> None:
        pass


class ProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeConnection.responses = []
        FakeConnection.requests = []
        FakeConnection.hosts = []
        self.patch = mock.patch.object(proxy.http.client, "HTTPSConnection", FakeConnection)
        self.patch.start()
        self.base_url, self.stop = proxy.start_proxy(_TEST_KEY, _TEST_TOKEN)
        self.assertTrue(self.base_url.startswith("http://127.0.0.1:"))
        self.port = int(self.base_url.rsplit(":", 1)[1])

    def tearDown(self) -> None:
        self.stop()
        self.stop()
        self.patch.stop()

    def request(self, method: str, path: str, body: bytes | None = None, *, token: str = _TEST_TOKEN, host: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Authorization": f"Bearer {token}"}
        if host is not None:
            headers["Host"] = host
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = (response.status, dict(response.getheaders()), response.read())
        connection.close()
        return result

    def test_authorization_and_fixed_paths(self) -> None:
        for method, path, token in [
            ("POST", "/responses", "wrong"),
            ("POST", "/other", _TEST_TOKEN),
            ("GET", "/responses", _TEST_TOKEN),
        ]:
            status, _, _ = self.request(method, path, b'{}' if method == "POST" else None, token=token)
            self.assertIn(status, (401, 404))
        status, _, _ = self.request("GET", "/models", host="malicious.invalid")
        self.assertEqual(status, 401)
        self.assertEqual(FakeConnection.requests, [])

    def test_models_discards_query_and_redacts_error(self) -> None:
        FakeConnection.responses.append(
            FakeResponse(429, "application/json", [b'{"error":"' + _TEST_KEY.encode() + b'"}'], "7")
        )
        status, headers, body = self.request("GET", "/models?client_version=0.0.0")
        self.assertEqual(status, 429)
        self.assertEqual(headers.get("Retry-After"), "7")
        self.assertNotIn(_TEST_KEY.encode(), body)
        self.assertIn(b"[REDACTED]", body)
        self.assertEqual(FakeConnection.hosts, ["api.deepseek.com"])
        method, path, sent, upstream_headers = FakeConnection.requests[0]
        self.assertEqual((method, path, sent), ("GET", "/models", None))
        self.assertEqual(upstream_headers["Authorization"], "Bearer " + _TEST_KEY)

    def test_streaming_sse_redacts_split_key_without_done_marker(self) -> None:
        raw = b'event: response.output_text.delta\ndata: {"delta":"' + _TEST_KEY.encode() + b'"}\n\nevent: response.completed\ndata: {}\n\n'
        at = raw.index(_TEST_KEY.encode()) + 4
        FakeConnection.responses.append(
            FakeResponse(200, "text/event-stream", [raw[:at], raw[at:at + 5], raw[at + 5:]])
        )
        payload = json.dumps({"model": "deepseek-flash", "input": "hello", "stream": True}).encode()
        status, headers, body = self.request("POST", "/responses", payload)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "text/event-stream")
        self.assertEqual(headers.get("Transfer-Encoding"), "chunked")
        self.assertIn(b"event: response.completed", body)
        self.assertIn(b"[REDACTED]", body)
        self.assertNotIn(_TEST_KEY.encode(), body)
        self.assertNotIn(b"[DONE]", body)
        method, path, sent, upstream_headers = FakeConnection.requests[0]
        self.assertEqual((method, path, sent), ("POST", "/responses", payload))
        self.assertEqual(upstream_headers["Authorization"], "Bearer " + _TEST_KEY)

    def test_rejects_other_model_without_upstream_call(self) -> None:
        payload = json.dumps({"model": "unrelated-model", "input": "hello"}).encode()
        status, _, _ = self.request("POST", "/responses", payload)
        self.assertEqual(status, 400)
        self.assertEqual(FakeConnection.requests, [])


    def test_stream_byte_limit_closes_without_completion(self) -> None:
        raw = b"event: response.completed\ndata: {}\n\n" + b"x" * 100
        FakeConnection.responses.append(FakeResponse(200, "text/event-stream", [raw]))
        payload = json.dumps({"model": "deepseek-flash", "input": "hello", "stream": True}).encode()
        with mock.patch.object(proxy, "_MAX_STREAM_BYTES", 20):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            connection.request(
                "POST", "/responses", body=payload,
                headers={"Authorization": f"Bearer {_TEST_TOKEN}", "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            with self.assertRaises(http.client.IncompleteRead) as error:
                response.read()
            self.assertNotIn(b"response.completed", error.exception.partial)
            connection.close()

    def test_stream_time_limit_closes_without_completion(self) -> None:
        FakeConnection.responses.append(
            FakeResponse(200, "text/event-stream", [b"event: response.completed\ndata: {}\n\n"])
        )
        payload = json.dumps({"model": "deepseek-flash", "input": "hello", "stream": True}).encode()
        with mock.patch.object(proxy, "_MAX_STREAM_SECONDS", 0):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            connection.request(
                "POST", "/responses", body=payload,
                headers={"Authorization": f"Bearer {_TEST_TOKEN}", "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            with self.assertRaises(http.client.IncompleteRead) as error:
                response.read()
            self.assertNotIn(b"response.completed", error.exception.partial)
            connection.close()

    def test_slow_unauthenticated_connections_are_bounded(self) -> None:
        with mock.patch.object(proxy, "_MAX_CONNECTIONS", 1):
            base_url, stop = proxy.start_proxy(_TEST_KEY, _TEST_TOKEN)
        port = int(base_url.rsplit(":", 1)[1])
        server = next(
            cell.cell_contents for cell in stop.__closure__
            if isinstance(cell.cell_contents, proxy._Server)
        )
        first = socket.create_connection(("127.0.0.1", port), timeout=3)
        try:
            first.sendall(
                f"GET /models HTTP/1.1\\r\\nHost: 127.0.0.1:{port}\\r\\n".encode()
            )
            for _ in range(100):
                if not server.connection_slots.acquire(blocking=False):
                    break
                server.connection_slots.release()
                time.sleep(0.01)
            else:
                self.fail("first slow connection did not reserve its handler slot")
            second = socket.create_connection(("127.0.0.1", port), timeout=3)
            try:
                second.sendall(
                    f"GET /models HTTP/1.1\\r\\nHost: 127.0.0.1:{port}\\r\\n"
                    f"Authorization: Bearer {_TEST_TOKEN}\\r\\n\\r\\n".encode()
                )
                second.settimeout(2)
                try:
                    reply = second.recv(1024)
                except (ConnectionResetError, ConnectionAbortedError):
                    reply = b""
                self.assertEqual(reply, b"")
                self.assertEqual(FakeConnection.requests, [])
            finally:
                second.close()
        finally:
            first.close()
            stop()

    def test_slow_request_body_times_out_before_upstream(self) -> None:
        with mock.patch.object(proxy, "_CLIENT_IO_TIMEOUT_SECONDS", 0.15):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
            connection.putrequest("POST", "/responses")
            connection.putheader("Authorization", f"Bearer {_TEST_TOKEN}")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", "100")
            connection.endheaders()
            connection.send(b"{")
            response = connection.getresponse()
            self.assertEqual(response.status, 408)
            response.read()
            connection.close()
        self.assertEqual(FakeConnection.requests, [])


if __name__ == "__main__":
    unittest.main()
