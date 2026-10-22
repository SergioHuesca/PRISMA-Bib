"""The 20-hex id of ``scripts/train_patch_id.py`` in throwaway repositories.

The id must change whenever the PR's own bytes change (whitespace, indentation, line
endings, file modes, paths) and must stay put when main moves on. Each test guards one of
those properties and builds its own repository under tmp_path.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "train_patch_id.py"
_spec = importlib.util.spec_from_file_location("train_patch_id", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
net_patch_id = _module.net_patch_id

BASE = b"def f():\n    x = 1\n    return x\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _branch(
    root: Path,
    base_files: dict[str, bytes],
    change: Callable[[Path], None],
) -> tuple[Path, str]:
    """Commit base_files on main as origin/main, then apply change on branch pr."""
    repo = root
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    # Git for Windows sets autocrlf=true, which stores a CRLF-only edit as LF and so hides
    # it from the blobs the id is computed from. Pin it off so the test sees the bytes.
    _git(repo, "config", "core.autocrlf", "false")
    for name, data in base_files.items():
        (repo / name).write_bytes(data)
    base = _commit_all(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    _git(repo, "checkout", "-q", "-b", "pr")
    change(repo)
    return repo, _commit_all(repo, "pr")


def _write_m(repo: Path, data: bytes) -> None:
    (repo / "m.py").write_bytes(data)


def _id_of(repo: Path, head: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.chdir(repo)
    return net_patch_id(head)


def _content_id(root: Path, base: bytes, pr: bytes, monkeypatch: pytest.MonkeyPatch) -> str:
    repo, head = _branch(root, {"m.py": base}, lambda r: _write_m(r, pr))
    return _id_of(repo, head, monkeypatch)


_HEX20 = re.compile(r"[0-9a-f]{20}")


@pytest.mark.integration
def test_id__is_20_hex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _HEX20.fullmatch(
        _content_id(tmp_path / "a", BASE, b"def f():\n    x = 2\n", monkeypatch)
    )


@pytest.mark.integration
def test_id__indenting_one_line_changes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    flat = _content_id(tmp_path / "a", BASE, b"def f():\n    x = 2\n    return x\n", monkeypatch)
    indented = _content_id(
        tmp_path / "b", BASE, b"def f():\n        x = 2\n    return x\n", monkeypatch
    )
    assert flat != indented


@pytest.mark.integration
def test_id__spacing_inside_an_expression_changes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tight = _content_id(tmp_path / "a", b"T=0\n", b"T=1\n", monkeypatch)
    spaced = _content_id(tmp_path / "b", b"T=0\n", b"T = 1\n", monkeypatch)
    assert tight != spaced


@pytest.mark.integration
def test_id__trailing_space_changes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plain = _content_id(tmp_path / "a", b"x = 1\n", b"x = 2\n", monkeypatch)
    trailing = _content_id(tmp_path / "b", b"x = 1\n", b"x = 2 \n", monkeypatch)
    assert plain != trailing


@pytest.mark.integration
def test_id__crlf_only_edit_changes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lf = _content_id(tmp_path / "a", b"x = 1\n", b"x = 2\n", monkeypatch)
    crlf = _content_id(tmp_path / "b", b"x = 1\n", b"x = 2\r\n", monkeypatch)
    assert lf != crlf


@pytest.mark.integration
def test_id__unrelated_main_commit_does_not_change_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # main moves on: a line added above the PR's hunk (shifts line numbers) and a change to
    # another file. The PR's own bytes are the same, so the id must be the same.
    repo, head = _branch(
        tmp_path / "a",
        {"m.py": BASE, "other.py": b"o = 1\n"},
        lambda r: _write_m(r, b"def f():\n    x = 2\n    return x\n"),
    )
    before = _id_of(repo, head, monkeypatch)
    _git(repo, "checkout", "-q", "main")
    _write_m(repo, b"# added above\n" + BASE)
    (repo / "other.py").write_bytes(b"o = 2\n")
    tip = _commit_all(repo, "main moves")
    _git(repo, "update-ref", "refs/remotes/origin/main", tip)
    assert net_patch_id(head) == before


@pytest.mark.integration
def test_id__chmod_changes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The content is unchanged; only the mode is. Filesystem chmod is not reliable on every
    # platform, so the index mode is set directly.
    mode_only, head = _branch(
        tmp_path / "a",
        {"m.py": BASE},
        lambda r: _git(r, "update-index", "--chmod=+x", "m.py"),
    )
    mode_id = _id_of(mode_only, head, monkeypatch)
    content = _content_id(tmp_path / "b", BASE, b"def f():\n    x = 2\n    return x\n", monkeypatch)
    assert mode_id != content


@pytest.mark.integration
def test_id__rename_with_edit_is_stable_and_differs_from_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def rename_and_edit(r: Path) -> None:
        _git(r, "mv", "m.py", "n.py")
        (r / "n.py").write_bytes(b"def f():\n    x = 2\n    return x\n")

    repo, head = _branch(tmp_path / "a", {"m.py": BASE}, rename_and_edit)
    renamed = _id_of(repo, head, monkeypatch)
    assert _HEX20.fullmatch(renamed)
    in_place = _content_id(
        tmp_path / "b", BASE, b"def f():\n    x = 2\n    return x\n", monkeypatch
    )
    assert renamed != in_place


@pytest.mark.integration
def test_id__same_change_on_different_bases_is_equal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The same reviewed edit on two histories with different unrelated content above it.
    first = _content_id(tmp_path / "a", BASE, b"def f():\n    x = 2\n    return x\n", monkeypatch)
    second = _content_id(
        tmp_path / "b",
        b"# other history\n" + BASE,
        b"# other history\ndef f():\n    x = 2\n    return x\n",
        monkeypatch,
    )
    assert first == second


@pytest.mark.integration
def test_id__no_net_change__raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, head = _branch(tmp_path / "a", {"m.py": BASE}, lambda r: None)
    # head equals origin/main: nothing changed.
    with pytest.raises(ValueError, match="no net change"):
        _id_of(repo, head, monkeypatch)
