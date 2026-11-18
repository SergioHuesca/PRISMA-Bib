"""``prismabib gui``: the launcher, its host, port and origin rules, and its shutdown.

Nothing here imports Panel at module level, so ``prismabib gui --help`` works on a base install.
Panel (the ``gui`` extra) is imported by :mod:`prismabib.gui.server` only when the app runs.

The app is local-only: it binds a loopback address, there is no option to bind elsewhere, and the
server rejects any request whose Host or Origin is not the local app on the chosen port.

When the server stops (the Stop button, Ctrl+C or a closed terminal) the launcher cancels every
running task, waits a bounded time for them, and releases the Project locks it holds.
"""

from __future__ import annotations

import ipaddress
import signal
import socket
import threading
import webbrowser
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Final

import typer

from prismabib.doctor_checks import projects_root_check
from prismabib.errors import ConfigError
from prismabib.gui import stop_model

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


def require_usable_root(root: Path) -> Path:
    """Return ``root`` as an absolute path, or refuse (one sentence) a folder the app cannot use."""
    root = root.expanduser().absolute()
    if not root.is_dir():
        raise ConfigError(f"The Projects folder {root} does not exist; choose a folder that does.")
    if projects_root_check(root).level == "FAIL":
        raise ConfigError(
            f"prismabib cannot write to the Projects folder {root}; choose a folder you can write to."
        )
    return root


def _run_forever(server: Any) -> None:
    server.run_until_shutdown()


#: How long the app waits for a running task to reach a safe point once it is asked to stop.
SHUTDOWN_WAIT_SECONDS: Final = 30.0
#: Tries at binding the chosen port: another program can take it between picking and binding.
_BIND_ATTEMPTS: Final = 3


def _create_server(
    create: Callable[[str, int], Any], host: str, port: int, bind: Callable[[str, int], int]
) -> Any:
    """Create the server on ``port`` (or another free one); the port can be taken in between."""
    chosen = pick_port(host, port, bind)
    for attempt in range(_BIND_ATTEMPTS):
        try:
            return create(host, chosen)
        except OSError:
            if attempt == _BIND_ATTEMPTS - 1:
                break
            typer.echo(f"Port {chosen} was taken a moment ago; choosing another one.")
            chosen = pick_port(host, 0, bind)
    raise ConfigError(f"No free port on {host}. Close a program that holds ports and try again.")


def _shut_down(wait: float, *, say_stopped: bool = True) -> None:
    """Ask running tasks to stop, wait for them, and release the Project locks, saying what happens.

    A lock whose task is still running is released the moment that task ends, never earlier.
    ``say_stopped=False`` leaves out the success line (the app failed to start; the error says so).
    """
    from prismabib.gui import service, tasks

    running = stop_model.running_names()
    if running:
        typer.echo(
            f"Stopping: asking {', '.join(running)} to stop at the next safe point. A capture "
            f"keeps the pages already fetched and resumes the next time (waiting up to "
            f"{wait:g} seconds)."
        )
    late = tasks.stop_all(wait)
    for session in service.held_sessions():
        tasks.runner_for(session.project).when_idle(session.close)
    if late:
        typer.echo(
            f"{', '.join(late)} did not stop within {wait:g} seconds. The app exits when it "
            "does, and its Project stays locked until then."
        )
    elif say_stopped:
        typer.echo("prismabib has stopped. Nothing running was left behind.")


def _no_op() -> None:
    """Stand-in for ``restore``/``mute`` until the handlers are installed."""


def install_signal_handlers() -> tuple[Callable[[], None], Callable[[], None]]:
    """Make SIGTERM (and SIGHUP where it exists) stop the app like Ctrl+C; return (undo, mute).

    ``mute`` makes only the SIGTERM and SIGHUP handlers do nothing: ``serve`` calls it when the
    shutdown begins, so one of those signals arriving then (after Stop, say) cannot interrupt
    it. Ctrl+C (SIGINT) is deliberately left alone, so a second Ctrl+C can still force-quit a
    stuck shutdown; the Project lock may then stay until the next window takes over. Python
    refuses a handler off the main thread, so there this installs nothing.
    """
    if threading.current_thread() is not threading.main_thread():
        return (lambda: None), (lambda: None)
    stopping = False

    def handler(_signum: int, _frame: object) -> None:
        nonlocal stopping
        # Only SIGTERM and SIGHUP are muted once the shutdown begins; SIGINT is not ours, so a
        # second Ctrl+C still interrupts (the escape hatch).
        if not stopping:
            stopping = True
            raise KeyboardInterrupt

    def mute() -> None:
        nonlocal stopping
        stopping = True

    # SIGHUP is not defined on Windows. Panel sets its own SIGINT handler when the server is
    # created, so SIGINT is saved here too and put back with the rest.
    names = ("SIGINT", "SIGTERM", "SIGHUP")
    found = {s: signal.getsignal(s) for n in names if (s := getattr(signal, n, None))}
    for sig in found:
        if sig != signal.SIGINT:
            signal.signal(sig, handler)

    def restore() -> None:
        for sig, previous in found.items():
            signal.signal(sig, previous if previous is not None else signal.SIG_DFL)

    return restore, mute


def serve(
    host: str = LOOPBACK,
    port: int = PREFERRED_PORT,
    *,
    no_browser: bool = False,
    root: Path | None = None,
    open_browser: Callable[[str], object] = webbrowser.open,
    bind: Callable[[str, int], int] = _try_bind,
    run: Callable[[Any], None] = _run_forever,
    shutdown_wait: float = SHUTDOWN_WAIT_SECONDS,
) -> str:
    """Start the app on a loopback address, open the browser unless told not to, and block.

    ``root`` is the Projects folder for this run: it wins over the saved folder, is never saved,
    and is refused (before any server exists) when it is missing or not writable.

    When ``run`` returns (the Stop button, Ctrl+C or a closed terminal) every running task is
    asked to stop, joined for up to ``shutdown_wait`` seconds, and the Project locks are released.
    Once the shutdown begins only SIGTERM and SIGHUP are muted; Ctrl+C is deliberately left
    alone, so a second Ctrl+C can force-quit a stuck shutdown (the lock may then stay until the
    next window takes over). ``open_browser``, ``bind`` and ``run`` are seams for tests. Returns the URL it served.
    """
    host = require_loopback(host)
    if root is not None:
        root = require_usable_root(root)
    try:
        from prismabib.gui.server import create_server
    except ImportError as exc:
        raise ConfigError(
            "The app needs the `gui` extra. Install it with: "
            'uv tool install "prismabib[gui] @ git+https://github.com/SergioHuesca/PRISMA-Bib"'
        ) from exc
    # Before the server: it sets a SIGINT handler of its own.
    restore = mute = _no_op
    url = ""  # stays empty when a stop arrives before the server exists
    failed = False
    try:
        restore, mute = install_signal_handlers()  # inside the try: restore runs on any exit
        try:
            server = _create_server(partial(create_server, root=root), host, port, bind)
            # "Stop the app": the loop stops, run returns, and serve returns.
            stop_model.install_stopper(lambda: server.io_loop.add_callback(server.io_loop.stop))
            url = f"http://{host}:{server.port}/"
            typer.echo(f"prismabib is running at {url} (Ctrl+C to stop)")
            if not no_browser:
                open_browser(url)
            run(server)
        except KeyboardInterrupt:  # Ctrl+C or a system stop is a normal way to stop
            pass
        except ConfigError:  # e.g. no free port: still release locks, but do not claim a stop
            failed = True
            raise
        finally:
            mute()  # SIGTERM/SIGHUP now must not interrupt the shutdown, which frees the locks
            _shut_down(shutdown_wait, say_stopped=not failed)
    finally:
        restore()
    return url


def register_gui_command(app: typer.Typer, root_option: Any) -> None:
    """Add ``gui`` to ``app`` (called once from ``cli.py``), with the shared ``--root`` option."""

    @app.command("gui")
    def gui(
        no_browser: Annotated[
            bool, typer.Option("--no-browser", help="Do not open the default browser.")
        ] = False,
        port: Annotated[
            int,
            typer.Option(help="Preferred port. A free one is chosen when it is busy."),
        ] = PREFERRED_PORT,
        root: Path | None = None,
    ) -> None:
        """Open the local app in your browser. It serves on 127.0.0.1 only."""
        # Imported here: cli.py imports this module while it is still being defined.
        from prismabib.cli import reporting_errors

        with reporting_errors():
            serve(port=port, no_browser=no_browser, root=root)

    # The annotations are strings (``from __future__``) and the option is a parameter, so attach
    # the shared definition directly; typer reads ``__annotations__`` when the command is built.
    gui.__annotations__["root"] = Annotated[Path | None, root_option]
