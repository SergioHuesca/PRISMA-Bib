"""Build the Panel server with the Host and Origin guard in front of it (ticket #318)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import panel as pn
from panel.io.server import get_server
from tornado import httputil

from prismabib.gui import palette
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


def install_page_css() -> None:
    """Add the focus ring and default button edge to ``pn.config.raw_css`` once (ticket #343).

    It assigns the process-wide ``pn.config.raw_css``, and is idempotent. Assumption: it is called
    once, on the main thread, from ``create_server`` before the server runs, so nothing else
    writes the setting at the same time; it is not safe to call from request handlers or threads.
    It must run before the app is built: Panel reads the setting when each model is created.
    """
    if palette.PAGE_CSS not in pn.config.raw_css:
        pn.config.raw_css = [*pn.config.raw_css, palette.PAGE_CSS]


def create_server(host: str, port: int, root: Path | None = None) -> Any:
    """Bind ``host:port`` and return the not-yet-running Panel server.

    ``root`` is the Projects folder for this run; ``None`` means the saved one.
    """
    install_page_css()

    def app() -> Any:  # a plain function: Panel calls only functions, not a functools.partial
        return build_app(root)

    server = get_server(
        app,
        port=port,
        address=host,
        websocket_origin=sorted(allowed_hosts(host, port)),
        show=False,
        start=False,
        verbose=False,
    )
    server._http.request_callback = _Guard(server._http.request_callback, host, port)
    return server
