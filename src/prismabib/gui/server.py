"""Build the Panel server with the Host and Origin guard in front of it (ticket #318)."""

from __future__ import annotations

from typing import Any

from panel.io.server import get_server
from tornado import httputil

from prismabib.gui.app import build_app
from prismabib.gui.launch import allowed_hosts, is_allowed

_FORBIDDEN = b"Forbidden: this is not the local prismabib app.\n"


class _GuardDelegate(httputil.HTTPMessageDelegate):
    def __init__(
        self, app: Any, server_conn: Any, conn: httputil.HTTPConnection, host: str, port: int
    ) -> None:
        self._app, self._server_conn, self._conn = app, server_conn, conn
        self._host, self._port = host, port
        self._inner: httputil.HTTPMessageDelegate | None = None

    def headers_received(self, start_line: Any, headers: httputil.HTTPHeaders) -> None:
        if not is_allowed(headers.get("Host"), headers.get("Origin"), self._host, self._port):
            self._conn.write_headers(
                httputil.ResponseStartLine("HTTP/1.1", 403, "Forbidden"),
                httputil.HTTPHeaders(
                    {
                        "Content-Type": "text/plain",
                        "Content-Length": str(len(_FORBIDDEN)),
                        "Connection": "close",
                    }
                ),
                _FORBIDDEN,
            )
            self._conn.finish()
            return
        self._inner = self._app.start_request(self._server_conn, self._conn)
        self._inner.headers_received(start_line, headers)

    def data_received(self, chunk: bytes) -> None:
        if self._inner is not None:
            self._inner.data_received(chunk)

    def finish(self) -> None:
        if self._inner is not None:
            self._inner.finish()

    def on_connection_close(self) -> None:
        if self._inner is not None:
            self._inner.on_connection_close()


class _Guard(httputil.HTTPServerConnectionDelegate):
    """Wraps the Tornado app: every request, websocket upgrades included, passes the check."""

    def __init__(self, app: Any, host: str, port: int) -> None:
        self._app, self._host, self._port = app, host, port

    def start_request(
        self, server_conn: object, request_conn: httputil.HTTPConnection
    ) -> httputil.HTTPMessageDelegate:
        return _GuardDelegate(self._app, server_conn, request_conn, self._host, self._port)


def create_server(host: str, port: int) -> Any:
    """Bind ``host:port`` and return the not-yet-running Panel server."""
    server = get_server(
        build_app,
        port=port,
        address=host,
        websocket_origin=sorted(allowed_hosts(host, port)),
        show=False,
        start=False,
        verbose=False,
    )
    server._http.request_callback = _Guard(server._http.request_callback, host, port)
    return server
