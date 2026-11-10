"""``prismabib gui``: host, port and origin rules, and the launcher around them (ticket #318)."""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap

import pytest
from typer.testing import CliRunner

from prismabib.cli import app
from prismabib.errors import ConfigError
from prismabib.gui.launch import (
    _create_server,  # pyright: ignore[reportPrivateUsage]
    is_allowed,
    pick_port,
    require_loopback,
    serve,
)

runner = CliRunner()
#: Help and error text is drawn by rich, which colours it and wraps it to the terminal width on
#: some machines (a CI runner, FORCE_COLOR). Fix the width and strip the colour before asserting.
_PLAIN_ENV = {"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _plain(output: str) -> str:
    return _ANSI.sub("", output)


@pytest.mark.unit
@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com", "::", "", "::1"])
def test_require_loopback__non_loopback_host__is_refused_with_a_clear_message(host: str) -> None:
    with pytest.raises(ConfigError, match="loopback address only"):
        require_loopback(host)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("host", "bound"), [("127.0.0.1", "127.0.0.1"), ("localhost", "127.0.0.1")]
)
def test_require_loopback__loopback_host__is_accepted(host: str, bound: str) -> None:
    assert require_loopback(host) == bound


@pytest.mark.unit
@pytest.mark.parametrize(
    ("host_header", "origin", "allowed"),
    [
        ("127.0.0.1:5006", None, True),
        ("localhost:5006", None, True),
        ("LOCALHOST:5006", "http://localhost:5006", True),
        ("127.0.0.1:5006", "http://127.0.0.1:5006", True),
        ("evil.example:5006", None, False),  # DNS rebinding: the Host is the attacker's
        ("127.0.0.1:5007", None, False),  # another local port
        ("127.0.0.1", None, False),  # no port
        (None, None, False),
        ("127.0.0.1:5006", "http://evil.example", False),
        ("127.0.0.1:5006", "http://127.0.0.1:5007", False),
        ("127.0.0.1:5006", "null", False),
        ("127.0.0.1:5006", "https://127.0.0.1:5006", False),
    ],
)
def test_is_allowed__host_and_origin__only_the_local_app_passes(
    host_header: str | None, origin: str | None, allowed: bool
) -> None:
    assert is_allowed(host_header, origin, "127.0.0.1", 5006) is allowed


@pytest.mark.unit
def test_pick_port__preferred_port_busy__falls_back_to_the_os_choice() -> None:
    def busy_unless_any(_host: str, port: int) -> int:
        if port:
            raise OSError("in use")
        return 54321

    assert pick_port("127.0.0.1", 5006, bind=busy_unless_any) == 54321


@pytest.mark.unit
def test_pick_port__preferred_port_free__it_is_used() -> None:
    assert pick_port("127.0.0.1", 4321, bind=lambda _host, port: port or 9999) == 4321


@pytest.mark.unit
def test_pick_port__no_port_free__raises_an_error_saying_so() -> None:
    def never(_host: str, _port: int) -> int:
        raise OSError("in use")

    with pytest.raises(ConfigError, match="No free port"):
        pick_port("127.0.0.1", 5006, bind=never)


@pytest.mark.unit
def test_serve__no_free_port__raises_the_error_before_any_browser_opens() -> None:
    def never(_host: str, _port: int) -> int:
        raise OSError("in use")

    with pytest.raises(ConfigError, match="No free port"):
        serve(bind=never, open_browser=lambda _url: pytest.fail("no browser"), run=lambda _s: None)


@pytest.mark.unit
def test_create_server__port_taken_between_picking_and_binding__retries_and_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ports: list[int] = []

    def create(_host: str, port: int) -> str:
        ports.append(port)
        if len(ports) == 1:
            raise OSError("address in use")  # someone took the port after it was picked
        return "server"

    picks = iter([5006, 6001])
    server = _create_server(create, "127.0.0.1", 5006, lambda _h, _p: next(picks))

    assert server == "server" and ports == [5006, 6001]
    assert "5006 was taken a moment ago" in capsys.readouterr().out


@pytest.mark.unit
def test_create_server__port_always_taken__gives_up_with_the_no_free_port_error() -> None:
    def create(_host: str, _port: int) -> str:
        raise OSError("address in use")

    with pytest.raises(ConfigError, match="No free port"):
        _create_server(create, "127.0.0.1", 5006, lambda _h, p: p or 6001)


@pytest.mark.unit
def test_serve__refuses_a_non_loopback_host_before_binding_anything() -> None:
    with pytest.raises(ConfigError, match="loopback address only"):
        serve(host="0.0.0.0", run=lambda _s: pytest.fail("must not run"))


@pytest.mark.unit
def test_gui_command__has_no_option_to_bind_elsewhere() -> None:
    result = runner.invoke(app, ["gui", "--host", "0.0.0.0"], env=_PLAIN_ENV)

    assert result.exit_code != 0
    assert "No such option" in _plain(result.output)


@pytest.mark.unit
def test_gui_command__help_works_and_names_the_no_browser_flag() -> None:
    result = runner.invoke(app, ["gui", "--help"], env=_PLAIN_ENV)

    assert result.exit_code == 0
    assert "--no-browser" in _plain(result.output)


@pytest.mark.unit
def test_gui_help__base_install__never_imports_panel_or_param() -> None:
    """Run in a fresh interpreter where the extra's packages cannot be imported at all."""
    code = textwrap.dedent(
        """
        import sys
        for name in ("panel", "param", "bokeh", "tornado"):
            sys.modules[name] = None  # a base install: importing any of these raises
        from typer.testing import CliRunner
        from prismabib.cli import app
        import re
        result = CliRunner().invoke(app, ["gui", "--help"], env=ENV)
        assert result.exit_code == 0, result.output
        assert "--no-browser" in re.sub(r"\\x1b\\[[0-9;?]*[A-Za-z]", "", result.output), result.output
        """
    ).replace("ENV", repr(_PLAIN_ENV))
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=120
    )
    assert done.returncode == 0, done.stderr


@pytest.mark.unit
def test_gui_command__extra_not_installed__says_how_to_install_it() -> None:
    code = textwrap.dedent(
        """
        import sys
        sys.modules["panel"] = None
        from prismabib.errors import ConfigError
        from prismabib.gui.launch import serve
        try:
            serve(run=lambda s: None)
        except ConfigError as exc:
            assert "prismabib[gui]" in str(exc), exc
        else:
            raise SystemExit("no error")
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=120
    )
    assert done.returncode == 0, done.stderr
