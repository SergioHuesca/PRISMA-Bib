"""Settings locations, the reviewer name, and the stop rules (ticket #323)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from prismabib.errors import ValidationError
from prismabib.gui import settings_model as sm
from prismabib.gui import stop_model
from prismabib.user_settings import (
    REVIEWER_NAME_KEY,
    USER_FILE_OVERRIDE_VAR,
    read_user_setting,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _user_file(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path_factory.mktemp("user") / "settings.env"
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(path))
    return path


@pytest.fixture(autouse=True)
def _clean_stop_state() -> Iterator[None]:
    stop_model.install_stopper(None)
    yield
    stop_model.install_stopper(None)
    for name in stop_model.running_names():  # a failed test must not leak a task
        raise AssertionError(f"task {name} left registered")


def test_locations__explicit_root__names_the_folder_and_the_per_user_file(
    tmp_path: Path, _user_file: Path
) -> None:
    where = sm.locations(tmp_path)
    assert where.projects_folder == tmp_path
    assert where.settings_file == _user_file.resolve()


def test_save_reviewer_name__new_value__is_read_back_from_the_per_user_file(
    _user_file: Path,
) -> None:
    assert sm.reviewer_name() == ""
    assert sm.save_reviewer_name("  Ada L.  ") == "Ada L."
    assert read_user_setting(REVIEWER_NAME_KEY) == "Ada L."
    assert sm.save_reviewer_name("Grace H.") == "Grace H."
    assert sm.reviewer_name() == "Grace H."


def test_save_reviewer_name__blank__is_refused_and_keeps_the_old_name() -> None:
    sm.save_reviewer_name("Ada")
    with pytest.raises(ValidationError):
        sm.save_reviewer_name("   ")
    assert sm.reviewer_name() == "Ada"


def test_request_stop__nothing_running__stops_without_asking() -> None:
    stopped: list[int] = []
    stop_model.install_stopper(lambda: stopped.append(1))
    assert stop_model.request_stop(confirmed=False) == stop_model.StopRequest(True)
    assert stopped == [1]


def test_request_stop__task_running__asks_first_and_changes_nothing() -> None:
    stopped: list[int] = []
    stop_model.install_stopper(lambda: stopped.append(1))
    cancel = threading.Event()
    task = stop_model.register("capture", cancel)
    try:
        result = stop_model.request_stop(confirmed=False)
        assert not result.stopping
        assert result.confirm is not None and "unsealed run" in result.confirm
        assert "capture" in result.confirm
        assert stopped == [] and not cancel.is_set()
    finally:
        stop_model.unregister(task)


def test_request_stop__confirmed__cancels_every_task_and_stops() -> None:
    stopped: list[int] = []
    stop_model.install_stopper(lambda: stopped.append(1))
    flags = [threading.Event(), threading.Event()]
    tasks = [stop_model.register(f"t{i}", f) for i, f in enumerate(flags)]
    try:
        assert stop_model.request_stop(confirmed=True).stopping
        assert all(f.is_set() for f in flags) and stopped == [1]
    finally:
        for t in tasks:
            stop_model.unregister(t)
