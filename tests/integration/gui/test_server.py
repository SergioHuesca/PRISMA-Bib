"""``prismabib gui`` against a real loopback server (ticket #318)."""

from __future__ import annotations

import asyncio
import http.client
import socket
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from pytest_socket import enable_socket, socket_allow_hosts

pytest.importorskip("panel")

from prismabib.gui.launch import pick_port, serve


@pytest.fixture(autouse=True)
def _loopback_only() -> Iterator[None]:
    """The suite bans sockets; this module needs loopback and nothing else."""
    enable_socket()
    socket_allow_hosts(["127.0.0.1"], allow_unix_socket=True)
    yield


class _Running:
    """Serve on a thread with its own event loop, as the CLI does on the main thread."""

    def __init__(self, **kwargs: Any) -> None:
        self.opened: list[str] = []
        self.server: Any = None
        self.error: BaseException | None = None
        ready = threading.Event()

        def run(server: Any) -> None:
            self.server = server
            server.start()
            ready.set()
            server.io_loop.start()

        def target() -> None:
            asyncio.set_event_loop(asyncio.new_event_loop())
            try:
                # port 0: parallel workers must not race for the preferred port
                serve(port=0, open_browser=self.opened.append, run=run, **kwargs)
            except Exception as exc:  # noqa: BLE001 -- handed to the test thread to assert on
                self.error = exc
                ready.set()

        self.thread = threading.Thread(target=target, daemon=True)
        self.thread.start()
        assert ready.wait(60)
        assert self.error is None, self.error
        self.port: int = self.server.port

    def get(self, headers: dict[str, str] | None = None, *, path: str = "/") -> int:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request("GET", path, headers=headers or {})
            return conn.getresponse().status
        finally:
            conn.close()

    def stop(self) -> None:
        loop = self.server.io_loop
        loop.add_callback(self.server.unlisten)
        loop.add_callback(loop.stop)
        self.thread.join(30)


@pytest.fixture
def running() -> Iterator[_Running]:
    r = _Running(no_browser=True)
    yield r
    r.stop()


@pytest.mark.integration
def test_serve__no_browser__binds_loopback_on_a_free_port_and_serves_the_empty_app(
    running: _Running,
) -> None:
    assert running.server.address == "127.0.0.1"
    assert running.server.port > 0
    assert running.get() == 200
    assert running.opened == []


@pytest.mark.integration
def test_serve__without_the_flag__opens_the_browser_at_the_bound_url() -> None:
    r = _Running(no_browser=False)
    try:
        assert r.opened == [f"http://127.0.0.1:{r.port}/"]
    finally:
        r.stop()


@pytest.mark.integration
def test_pick_port__preferred_port_busy__a_free_one_is_chosen() -> None:
    holder = socket.socket()
    try:
        holder.bind(("127.0.0.1", 0))
        busy = holder.getsockname()[1]
        chosen = pick_port("127.0.0.1", busy)
    finally:
        holder.close()
    assert chosen not in (0, busy)


@pytest.mark.integration
@pytest.mark.parametrize("which", ["host", "other-port", "origin"])
def test_server__request_not_from_the_local_app__is_rejected(running: _Running, which: str) -> None:
    good = f"127.0.0.1:{running.port}"
    headers = {
        "host": {"Host": f"attacker.example:{running.port}"},
        "other-port": {"Host": f"127.0.0.1:{running.port + 1}"},
        "origin": {"Host": good, "Origin": "http://attacker.example"},
    }[which]

    assert running.get(headers) == 403


@pytest.mark.integration
def test_server__localhost_name_and_matching_origin__are_accepted(running: _Running) -> None:
    host = f"localhost:{running.port}"

    assert running.get({"Host": host, "Origin": f"http://{host}"}) == 200
