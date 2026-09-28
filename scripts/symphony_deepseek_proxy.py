"""Narrow loopback broker for the Symphony DeepSeek Responses provider.

The trusted adapter keeps the real key in this process. Codex receives only a
short-lived local bearer token and this server's loopback URL. Request and
response bodies and credentials are never logged.
"""

from __future__ import annotations

import hmac
import http.client
import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlsplit


_UPSTREAM_HOST = "api.deepseek.com"
_MAX_REQUEST_BYTES = 32 * 1024 * 1024
_MAX_REPLY_BYTES = 64 * 1024 * 1024
_MAX_CONCURRENT = 4
_MAX_CONNECTIONS = 8
_UPSTREAM_TIMEOUT_SECONDS = 300
_CLIENT_IO_TIMEOUT_SECONDS = 10
_MAX_STREAM_BYTES = 64 * 1024 * 1024
_MAX_STREAM_SECONDS = 30 * 60
_REDACTION = b"[REDACTED]"


class _StreamLimitExceeded(Exception):
    pass


def _redacted_chunks(source: object, secret: bytes, before_read: Callable[[], None]):
    """Redact a secret even when it straddles arbitrary network chunks."""
    tail = b""
    hold = max(0, len(secret) - 1)
    read = getattr(source, "read1", None) or getattr(source, "read")
    while True:
        before_read()
        chunk = read(16 * 1024)
        if not chunk:
            break
        tail = (tail + chunk).replace(secret, _REDACTION)
        if len(tail) > hold:
            boundary = len(tail) - hold
            yield tail[:boundary]
            tail = tail[boundary:]
    if tail:
        yield tail.replace(secret, _REDACTION)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]):
        super().__init__(address, handler)
        self.slots = threading.BoundedSemaphore(_MAX_CONCURRENT)
        self.connection_slots = threading.BoundedSemaphore(_MAX_CONNECTIONS)
        self.upstreams: set[http.client.HTTPSConnection] = set()
        self.upstreams_lock = threading.Lock()
        self.stopping = threading.Event()

    def handle_error(self, request: object, client_address: object) -> None:
        # BaseServer normally prints tracebacks. Suppress all request logging.
        pass

    def process_request(self, request: object, client_address: object) -> None:
        # Reserve before ThreadingMixIn starts a handler thread.
        if not self.connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connection_slots.release()
            raise

    def process_request_thread(self, request: object, client_address: object) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()


def start_proxy(key: str, dummy_token: str) -> tuple[str, Callable[[], None]]:
    """Start the broker; return ``(base_url, stop)`` with an idempotent stop."""
    if not isinstance(key, str) or not key or any(c in key for c in "\r\n"):
        raise ValueError("invalid DeepSeek key")
    if not isinstance(dummy_token, str) or not dummy_token or any(
        c in dummy_token for c in "\r\n"
    ) or hmac.compare_digest(key, dummy_token):
        raise ValueError("invalid local proxy token")
    secret = key.encode("utf-8")
    expected_authorization = "Bearer " + dummy_token

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server: _Server

        def setup(self) -> None:
            self.request.settimeout(_CLIENT_IO_TIMEOUT_SECONDS)
            super().setup()

        def log_message(self, format: str, *args: object) -> None:
            pass

        def handle_expect_100(self) -> bool:
            self._error(417, "expectation not supported")
            return False

        def _error(self, status: int, message: str) -> None:
            payload = json.dumps({"error": {"message": message}}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _authorized(self) -> bool:
            host = self.headers.get_all("Host", [])
            authorization = self.headers.get_all("Authorization", [])
            expected_host = f"127.0.0.1:{self.server.server_port}"
            return (
                len(host) == 1
                and host[0] == expected_host
                and len(authorization) == 1
                and hmac.compare_digest(authorization[0], expected_authorization)
            )

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path != "/models" or parsed.scheme or parsed.netloc or parsed.fragment:
                self._error(404, "not found")
                return
            if not self._authorized():
                self._error(401, "unauthorized")
                return
            # Codex may add client_version; no user-supplied query reaches upstream.
            self._forward("GET", "/models", None, False)

        def do_POST(self) -> None:
            if self.path != "/responses":
                self._error(404, "not found")
                return
            if not self._authorized():
                self._error(401, "unauthorized")
                return
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding"):
                self._error(400, "unsupported request encoding")
                return
            try:
                size = int(self.headers.get("Content-Length", ""))
            except ValueError:
                size = -1
            if not 0 < size <= _MAX_REQUEST_BYTES:
                self._error(413, "invalid request size")
                return
            try:
                body = self.rfile.read(size)
            except OSError:
                self._error(408, "request body timeout")
                return
            try:
                params = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                self._error(400, "invalid JSON")
                return
            if len(body) != size or not isinstance(params, dict):
                self._error(400, "invalid request")
                return
            if params.get("model") != "deepseek-flash":
                self._error(400, "unsupported model")
                return
            self._forward("POST", "/responses", body, params.get("stream") is True)

        def _forward(self, method: str, path: str, body: bytes | None, stream: bool) -> None:
            if self.server.stopping.is_set():
                self._error(503, "proxy stopped")
                return
            if not self.server.slots.acquire(blocking=False):
                self._error(503, "proxy busy")
                return
            connection: http.client.HTTPSConnection | None = None
            headers_sent = False
            try:
                # Fixed HTTPS origin prevents credential-bearing redirects.
                connection = http.client.HTTPSConnection(
                    _UPSTREAM_HOST,
                    timeout=_UPSTREAM_TIMEOUT_SECONDS,
                    context=ssl.create_default_context(),
                )
                with self.server.upstreams_lock:
                    self.server.upstreams.add(connection)
                headers = {
                    "Authorization": "Bearer " + key,
                    "Accept": "text/event-stream" if stream else "application/json",
                }
                if body is not None:
                    headers["Content-Type"] = "application/json"
                connection.request(method, path, body=body, headers=headers)
                upstream = connection.getresponse()
                content_type = upstream.getheader("Content-Type", "application/json")
                retry_after = upstream.getheader("Retry-After")
                is_stream = stream and upstream.status == 200 and content_type.lower().startswith("text/event-stream")
                if is_stream:
                    headers_sent = True
                    self._stream(upstream, content_type, retry_after, connection)
                else:
                    headers_sent = True
                    self._buffered(upstream, content_type, retry_after)
            except (OSError, http.client.HTTPException, ssl.SSLError):
                if not headers_sent:
                    self._error(502, "upstream unavailable")
            finally:
                if connection is not None:
                    with self.server.upstreams_lock:
                        self.server.upstreams.discard(connection)
                    connection.close()
                self.server.slots.release()

        def _headers(self, status: int, content_type: str, retry_after: str | None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if retry_after and retry_after.isascii() and retry_after.isdigit():
                self.send_header("Retry-After", retry_after)
            self.send_header("Connection", "close")
            self.close_connection = True

        def _buffered(self, upstream: http.client.HTTPResponse, content_type: str, retry_after: str | None) -> None:
            payload = upstream.read(_MAX_REPLY_BYTES + 1)
            if len(payload) > _MAX_REPLY_BYTES:
                self._error(502, "upstream reply too large")
                return
            payload = payload.replace(secret, _REDACTION)
            self._headers(upstream.status, content_type, retry_after)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _stream(
            self,
            upstream: http.client.HTTPResponse,
            content_type: str,
            retry_after: str | None,
            connection: http.client.HTTPSConnection,
        ) -> None:
            self._headers(upstream.status, content_type, retry_after)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            deadline = time.monotonic() + _MAX_STREAM_SECONDS
            sent_bytes = 0

            def before_read() -> None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _StreamLimitExceeded
                # HTTPResponse.read1 uses this socket, so a stalled upstream
                # cannot hold a slot beyond the total stream deadline.
                sock = getattr(connection, "sock", None)
                if sock is not None:
                    sock.settimeout(min(_UPSTREAM_TIMEOUT_SECONDS, remaining))

            try:
                for chunk in _redacted_chunks(upstream, secret, before_read):
                    if self.server.stopping.is_set():
                        break
                    sent_bytes += len(chunk)
                    if sent_bytes > _MAX_STREAM_BYTES or time.monotonic() >= deadline:
                        break
                    self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                else:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
            except (_StreamLimitExceeded, BrokenPipeError, ConnectionResetError, OSError):
                # Closing the upstream cancels generation. Never forge a final SSE.
                pass

    try:
        server = _Server(("127.0.0.1", 0), Handler)
    except OSError:
        raise RuntimeError("could not bind DeepSeek proxy") from None
    thread = threading.Thread(target=server.serve_forever, name="deepseek-proxy", daemon=True)
    thread.start()
    stopped = threading.Event()

    def stop() -> None:
        if stopped.is_set():
            return
        stopped.set()
        server.stopping.set()
        with server.upstreams_lock:
            for connection in tuple(server.upstreams):
                connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return f"http://127.0.0.1:{server.server_port}", stop
