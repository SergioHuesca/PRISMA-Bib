"""The net patch id of ``scripts/train_patch_id.py`` in a throwaway repository.

The merge train relies on two properties: the id survives main moving on (the reviewed
content did not change), and it changes when the PR's own content changes (the review is
stale). Each assertion below guards one of them.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "train_patch_id.py"
_spec = importlib.util.spec_from_file_location("train_patch_id", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
net_patch_id = _module.net_patch_id


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _write(repo: Path, lines: list[str]) -> None:
    (repo / "f.txt").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def _lines(repo: Path) -> list[str]:
    return (repo / "f.txt").read_text(encoding="utf-8").splitlines()


@pytest.mark.integration
def test_net_patch_id__survives_main_moving_and_changes_with_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, [f"line {i}" for i in range(1, 21)])
    base = _commit(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)

    _git(repo, "checkout", "-q", "-b", "pr")
    lines = _lines(repo)
    lines[9] = "reviewed change"
    _write(repo, lines)
    head = _commit(repo, "pr")
    monkeypatch.chdir(repo)
    reviewed = net_patch_id(head)
    assert len(reviewed) == 12

    # main moves on: an unrelated line above the PR's hunk shifts its line numbers.
    _git(repo, "checkout", "-q", "main")
    _write(repo, ["unrelated top"] + [f"line {i}" for i in range(1, 21)])
    main_tip = _commit(repo, "main moves")
    _git(repo, "update-ref", "refs/remotes/origin/main", main_tip)
    assert net_patch_id(head) == reviewed

    # The PR's own content changes after review: the id must change.
    _git(repo, "checkout", "-q", "pr")
    lines = _lines(repo)
    lines[9] = "a different change"
    _write(repo, lines)
    edited = _commit(repo, "pr edited")
    assert net_patch_id(edited) != reviewed

    # A head with no net change against main has no patch id.
    with pytest.raises(ValueError, match="no net change"):
        net_patch_id(main_tip)
