"""``prismabib gui``: the launcher and its host, port and origin rules (ticket #318).

Nothing here imports Panel at module level, so ``prismabib gui --help`` works on a base install.
Panel (the ``gui`` extra) is imported by :mod:`prismabib.gui.server` only when the app runs.

The app is local-only: it binds a loopback address, there is no option to bind elsewhere, and the
server rejects any request whose Host or Origin is not the local app on the chosen port.
"""

from __future__ import annotations

import ipaddress
import socket
import webbrowser
from collections.abc import Callable
from typing import Annotated, Any, Final

import typer

from prismabib.errors import ConfigError

LOOPBACK: Final = "127.0.0.1"
#: Tried first; when it is busy the OS picks a free port.
PREFERRED_PORT: Final = 5006


def require_loopback(host: str) -> str:
    """Return the address to bind, or refuse a host that is not an IPv4 loopback address."""
    if host == "localhost":
        return LOOPBACK
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if not (isinstance(address, ipaddress.IPv4Address) and address.is_loopback):
        raise ConfigError(
            f"prismabib gui serves on the loopback address only (127.0.0.1); refusing host "
            f"{host!r}. The app has no setting to bind elsewhere."
        )
    return host


def allowed_hosts(host: str, port: int) -> frozenset[str]:
    """The ``Host`` header values that name the local app on ``port``."""
    return frozenset({f"{host}:{port}", f"localhost:{port}"})


def is_allowed(host_header: str | None, origin: str | None, host: str, port: int) -> bool:
    """True when the request is addressed to the local app and, if it names an origin, is from it.

    A missing ``Origin`` is fine (a plain navigation sends none); a present one must be the app's
    own, so ``null`` and any other site are refused. A wrong ``Host`` is a DNS-rebinding attempt.
    """
    names = allowed_hosts(host, port)
    if host_header is None or host_header.lower() not in names:
        return False
    return origin is None or origin.lower() in {f"http://{n}" for n in names}


def _try_bind(host: str, port: int) -> int:
    """Bind-and-release ``host:port`` (0 = any free port); return the port. Raises ``OSError``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, port))
        return int(probe.getsockname()[1])


def pick_port(host: str, preferred: int, bind: Callable[[str, int], int] = _try_bind) -> int:
    """The preferred port when free, else one the OS picks; error when neither works."""
    for candidate in dict.fromkeys((preferred, 0)):
        try:
            return bind(host, candidate)
        except OSError:
            continue
    raise ConfigError(f"No free port on {host}. Close a program that holds ports and try again.")


def _run_forever(server: Any) -> None:
    server.run_until_shutdown()


def serve(
    host: str = LOOPBACK,
    port: int = PREFERRED_PORT,
    *,
    no_browser: bool = False,
    open_browser: Callable[[str], object] = webbrowser.open,
    bind: Callable[[str, int], int] = _try_bind,
    run: Callable[[Any], None] = _run_forever,
) -> str:
    """Start the app on a loopback address, open the browser unless told not to, and block.

    ``open_browser``, ``bind`` and ``run`` are seams for tests. Returns the URL it served.
    """
    host = require_loopback(host)
    chosen = pick_port(host, port, bind)
    try:
        from prismabib.gui.server import create_server
    except ImportError as exc:
        raise ConfigError(
            "The app needs the `gui` extra. Install it with: "
            'uv tool install "prismabib[gui] @ git+https://github.com/SergioHuesca/PRISMA-Bib"'
        ) from exc
    server = create_server(host, chosen)
    url = f"http://{host}:{server.port}/"
    typer.echo(f"prismabib is running at {url} (Ctrl+C to stop)")
    if not no_browser:
        open_browser(url)
    run(server)
    return url


def register_gui_command(app: typer.Typer) -> None:
    """Add ``gui`` to ``app`` (called once from ``cli.py``)."""

    @app.command("gui")
    def gui(
        no_browser: Annotated[
            bool, typer.Option("--no-browser", help="Do not open the default browser.")
        ] = False,
        port: Annotated[
            int,
            typer.Option(help="Preferred port. A free one is chosen when it is busy."),
        ] = PREFERRED_PORT,
    ) -> None:
        """Open the local app in your browser. It serves on 127.0.0.1 only."""
        # Imported here: cli.py imports this module while it is still being defined.
        from prismabib.cli import _reporting_errors

        with _reporting_errors():
            serve(port=port, no_browser=no_browser)
