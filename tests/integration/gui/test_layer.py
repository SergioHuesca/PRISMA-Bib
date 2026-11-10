"""The layer every screen calls: progress, errors, locks, opening (ticket #319)."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from prismabib.cli import app
from prismabib.errors import (
    AuthError,
    CaptureCancelledError,
    ConfigError,
    OperationCancelled,
    ProjectLockedError,
    ProjectVersionError,
    QuotaExceededError,
    RateLimitError,
    StepLockedError,
    UpstreamError,
    ValidationError,
)
from prismabib.gui import layer, path_limit
from prismabib.gui import new_project_model as npm
from prismabib.gui.layer import ErrorClass
from prismabib.project import Project
from prismabib.source_mode import load_mode_events
from prismabib.sources.openalex import DailyBudgetExhaustedError
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _user_file(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path_factory.mktemp("user") / "settings.env"
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(path))
    return path


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return layer.create_project(
        "demo", title="Demo", root=tmp_path, source_mode="openalex", reviewer="Ada"
    )


def _seal(project: Project, name: str = "20260101T000000Z-aaaaaaaa") -> Path:
    run = project.raw_dir / name
    run.mkdir(parents=True)
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    return run


def _decide(project: Project) -> None:
    project.decisions_path.write_text('{"schema_version": 1}\n', encoding="utf-8")


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


# -- step progress -------------------------------------------------------------------------------


def test_step_progress__new_project__only_source_mode_done(project: Project) -> None:
    progress = layer.step_progress(project)

    assert list(progress) == list(layer.STEPS)
    assert [s for s, done in progress.items() if done] == ["source_mode"]
    assert layer.first_unfinished(progress) == "query"


def test_step_progress__query_and_criteria_saved__marked_done(project: Project) -> None:
    toml = project.root / "project.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace('search = ""', 'search = "carbon"'),
        encoding="utf-8",
    )
    criteria = project.root / "criteria.yaml"
    criteria.write_text(
        criteria.read_text(encoding="utf-8").replace("version: 0.1.0", "version: 1.0.0"),
        encoding="utf-8",
    )

    progress = layer.step_progress(project)

    assert progress["query"] and progress["criteria"]
    assert layer.first_unfinished(progress) == "capture"


def _edit_criteria(project: Project, old: str, new: str) -> None:
    path = project.root / "criteria.yaml"
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_criteria_written__unmodified_scaffold__is_not_written(project: Project) -> None:
    assert layer.criteria_written(project) is False
    assert layer.step_progress(project)["criteria"] is False


def test_criteria_written__scaffold_made_in_an_earlier_year__is_still_not_written(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    year = datetime.now(UTC).year
    (first,) = load_mode_events(project)
    created_last_year = first.model_copy(update={"ts": first.ts.replace(year=year - 1)})
    monkeypatch.setattr(layer, "load_mode_events", lambda _project: [created_last_year])
    _edit_criteria(project, f"year_end: {year}", f"year_end: {year - 1}")

    assert layer.criteria_written(project) is False  # year_end is the creation year, untouched


@pytest.mark.parametrize(
    ("field", "old", "new"),
    [
        ("version", "version: 0.1.0", "version: 0.2.0"),
        ("temporal.year_start", "year_start: 1900", "year_start: 1990"),
        ("temporal.year_end", f"year_end: {datetime.now(UTC).year}", "year_end: 2001"),
        ("subject_areas", "subject_areas: []", "subject_areas: [COMP]"),
        ("doc_types.include", "  include: []", "  include: [ar]"),
        ("doc_types.conference_whitelist", "conference_whitelist: []", "conference_whitelist: [X]"),
        ("languages", "languages: []", "languages: [English]"),
        ("manual_abstract", "    - OFF_TOPIC\n", "    - OFF_TOPIC\n    - TOO_OLD\n"),
        ("manual_fulltext", "    - NO_FULL_TEXT\n", "    - NO_FULL_TEXT\n    - PAYWALL\n"),
    ],
)
def test_criteria_written__one_field_edited_by_hand__is_written(
    project: Project, field: str, old: str, new: str
) -> None:
    _edit_criteria(project, old, new)

    assert layer.criteria_written(project) is True, field
    assert layer.step_progress(project)["criteria"] is True


def test_criteria_written__unreadable_file__is_not_written(project: Project) -> None:
    (project.root / "criteria.yaml").write_text("version: [", encoding="utf-8")

    assert layer.criteria_written(project) is False


def test_step_progress__unsealed_run__capture_not_done_but_locks_hold(project: Project) -> None:
    (project.raw_dir / "openalex-search" / "r1").mkdir(parents=True)

    assert layer.step_progress(project)["capture"] is False
    assert layer.locks(project)["query"] is not None


def test_step_progress__sealed_run_and_built_store__capture_and_build_done(
    project: Project,
) -> None:
    _seal(project)
    project.db_path.write_bytes(b"duckdb")

    progress = layer.step_progress(project)

    assert progress["capture"] and progress["build"]
    assert not progress["screening"] and not progress["flow"]
    assert layer.first_unfinished(progress) == "query"


def test_step_progress__decisions_and_export__later_steps_done(project: Project) -> None:
    project.db_path.write_bytes(b"duckdb")
    _decide(project)
    (project.root / "exports" / "bundle.zip").write_bytes(b"z")

    progress = layer.step_progress(project)

    assert progress["screening"] and progress["flow"] and progress["export"]


def test_step_progress__project_made_by_the_cli__reads_the_same(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["init", "cli", "--actor", "Ada", "--sources", "scopus", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output

    opened = layer.open_project("cli", root=tmp_path)

    progress = layer.step_progress(opened)
    assert progress["source_mode"] and not progress["query"] and not progress["capture"]
    assert layer.first_unfinished(progress) == "query"


# -- errors --------------------------------------------------------------------------------------

CASES: list[tuple[BaseException, ErrorClass, str]] = [
    (ValidationError("please answer y, n or not sure"), ErrorClass.INPUT, "please answer"),
    (ConfigError("Project directory x is missing its project.toml"), ErrorClass.PREREQUISITE, "x"),
    (ConfigError("SCOPUS_API_KEY is not set"), ErrorClass.CREDENTIALS, "key is missing"),
    (AuthError("HTTP 401"), ErrorClass.CREDENTIALS, "did not accept"),
    (QuotaExceededError("weekly"), ErrorClass.QUOTA, "quota"),
    (DailyBudgetExhaustedError("daily"), ErrorClass.QUOTA, "quota"),
    (httpx.ConnectError("offline"), ErrorClass.NETWORK, "interrupted or is offline"),
    (RateLimitError("429"), ErrorClass.NETWORK, "interrupted or is offline"),
    (UpstreamError("503"), ErrorClass.NETWORK, "interrupted or is offline"),
    (ProjectLockedError("held"), ErrorClass.PROJECT_LOCKED, "Another window"),
    (StepLockedError("The query is read-only because x."), ErrorClass.STEP_LOCKED, "read-only"),
    (ProjectVersionError("newer", "2"), ErrorClass.VERSION, "version 2"),
    (CaptureCancelledError("stop"), ErrorClass.CANCELLED, "Stopped"),
    (OperationCancelled("stop"), ErrorClass.CANCELLED, "Stopped"),
    (RuntimeError("boom"), ErrorClass.UNEXPECTED, "unexpected"),
]


@pytest.mark.parametrize(("exc", "kind", "fragment"), CASES, ids=lambda v: type(v).__name__)
def test_translate__library_error__class_sentence_and_next_step(
    exc: BaseException, kind: ErrorClass, fragment: str
) -> None:
    message = layer.translate(exc)

    assert message.error_class is kind
    assert fragment in message.sentence
    assert message.next_step.endswith((".", ")"))
    assert message.next_step != message.sentence


def test_error_classes__every_class__has_a_translation_and_a_test() -> None:
    assert set(layer.SENTENCES) == set(layer.NEXT_STEPS) == set(ErrorClass)
    assert {kind for _, kind, _ in CASES} == set(ErrorClass)


def test_translate__unexpected_error__no_traceback_and_no_message_in_text_or_log(
    _user_file: Path,
) -> None:
    secret = "sk-FAKE-KEY-123"
    try:
        raise RuntimeError(f"Traceback (most recent call last): key={secret}")
    except RuntimeError as exc:
        message = layer.translate(exc)

    text = f"{message.sentence} {message.next_step}"
    log = _user_file.with_name("gui.log")
    assert "Traceback" not in text and secret not in text
    assert str(log) in message.sentence
    assert "RuntimeError" in log.read_text(encoding="utf-8")
    assert secret not in log.read_text(encoding="utf-8")


# -- locks ---------------------------------------------------------------------------------------


def test_locks__source_mode__refused_once_a_run_exists(project: Project) -> None:
    (project.raw_dir / "openalex-search" / "r1").mkdir(parents=True)

    with pytest.raises(StepLockedError, match="Run exists"):
        layer.set_source_mode(layer.Session(project), "scopus", reviewer="Ada")

    assert project.source_mode == "openalex"


def test_locks__source_mode__refused_once_a_decision_exists(project: Project) -> None:
    _decide(project)

    with pytest.raises(StepLockedError, match="Decision has been logged"):
        layer.set_source_mode(layer.Session(project), "scopus", reviewer="Ada")


def test_locks__query__read_only_once_the_first_run_starts(project: Project) -> None:
    layer.require_unlocked(project, "query")
    _seal(project)

    with pytest.raises(StepLockedError, match="query is read-only because a Run"):
        layer.require_unlocked(project, "query")


def test_locks__criteria__read_only_once_the_first_decision_is_logged(project: Project) -> None:
    _seal(project)
    layer.require_unlocked(project, "criteria")
    _decide(project)

    with pytest.raises(StepLockedError, match="Criteria are read-only because a Decision"):
        layer.require_unlocked(project, "criteria")


def test_set_source_mode__unlocked__changes_the_mode(project: Project) -> None:
    layer.set_source_mode(layer.Session(project), "scopus", reviewer="Ada")

    assert project.source_mode == "scopus"


# -- creating and opening ------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["", "   "])
def test_create_project__blank_reviewer__refused_and_nothing_written(
    tmp_path: Path, name: str
) -> None:
    with pytest.raises(ValidationError, match="reviewer name"):
        layer.create_project("x", title="X", root=tmp_path, source_mode="openalex", reviewer=name)

    assert not (tmp_path / "x").exists()


def test_create_project__existing_folder__refused_not_overwritten(project: Project) -> None:
    before = _tree_digest(project.root)

    with pytest.raises(ValidationError, match="already exists"):
        layer.create_project(
            "demo", title="Other", root=project.root.parent, source_mode="scopus", reviewer="Bo"
        )

    assert _tree_digest(project.root) == before


def _root_for_total(tmp_path: Path, slug: str, total: int) -> Path:
    """A Projects folder under ``tmp_path`` whose deepest Project path is ``total`` characters."""
    deepest = len(str(tmp_path / "r" / slug / path_limit.LONGEST_RELATIVE_PATH))
    if total < deepest:
        pytest.skip("this machine's temp folder is too long to build a path of that length")
    root = tmp_path / ("r" * (1 + total - deepest))
    assert len(str(root / slug / path_limit.LONGEST_RELATIVE_PATH)) == total
    return root


def test_create_project__deepest_path_of_240_characters__is_created(
    tmp_path: Path, real_path_limit: int
) -> None:
    root = _root_for_total(tmp_path, "demo", 240)

    layer.create_project("demo", title="D", root=root, source_mode="openalex", reviewer="Ada")

    assert (root / "demo" / "project.toml").is_file()


def test_create_project__deepest_path_of_241_characters__refused_before_writing_anything(
    tmp_path: Path, real_path_limit: int
) -> None:
    root = _root_for_total(tmp_path, "demo", 241)

    with pytest.raises(ValidationError, match="240") as caught:
        layer.create_project("demo", title="D", root=root, source_mode="openalex", reviewer="Ada")

    assert "241" in str(caught.value)  # it says how long the path would be, and the limit
    assert not root.exists()  # not even the Projects folder was made


@pytest.mark.parametrize(("slack", "refused"), [(0, False), (-1, True)])
def test_create_project__limit_measured_on_this_machine__boundary_is_inclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slack: int, refused: bool
) -> None:
    """The same boundary as 240/241, whatever length this machine's temp folder has."""
    deepest = len(str(path_limit.deepest_path(tmp_path / "root", "demo")))
    monkeypatch.setattr(path_limit, "MAX_PATH_CHARS", deepest + slack)

    def create() -> None:
        layer.create_project(
            "demo", title="D", root=tmp_path / "root", source_mode="openalex", reviewer="Ada"
        )

    if refused:
        with pytest.raises(ValidationError, match="limit"):
            create()
        assert not (tmp_path / "root").exists()
    else:
        create()
        assert (tmp_path / "root" / "demo" / "project.toml").is_file()


def test_create_project__very_long_name__refused_with_the_same_sentence(
    tmp_path: Path, real_path_limit: int
) -> None:
    with pytest.raises(ValidationError, match="240"):
        layer.create_project(
            "a" * 200, title="D", root=tmp_path, source_mode="openalex", reviewer="Ada"
        )

    assert list(tmp_path.iterdir()) == []


def test_new_project_screen__default_root_too_long__refuses_with_the_limit(
    tmp_path: Path, real_path_limit: int
) -> None:
    root = _root_for_total(tmp_path, "demo", 260)

    assert "240" in (npm.folder_refusal("Demo", root) or "")


def _log_with_version(project: Project, version: str) -> None:
    project.decisions_path.write_text(f'{{"schema_version": {version}}}\n', encoding="utf-8")


def test_open_project__one_version_older__opens(project: Project) -> None:
    _log_with_version(project, "0")

    assert layer.open_project("demo", root=project.root.parent).slug == "demo"


@pytest.mark.parametrize(
    ("content", "kind", "found"),
    [
        ('{"schema_version": 2}\n', "newer", "2"),
        ('{"schema_version": 7}\n{"schema_version": 1}\n', "newer", "7"),
        ('{"schema_version": -1}\n', "older", "-1"),
        ("{not json\n", "unreadable", None),
        ('{"no_version": 1}\n', "unreadable", None),
        ('{"schema_version": "1"}\n', "unreadable", None),
    ],
)
def test_open_project__other_version__refused_with_message_and_folder_untouched(
    project: Project, content: str, kind: str, found: str | None
) -> None:
    project.decisions_path.write_text(content, encoding="utf-8")
    before = _tree_digest(project.root)

    with pytest.raises(ProjectVersionError) as caught:
        layer.open_project("demo", root=project.root.parent)

    message = layer.translate(caught.value)
    assert message.error_class is ErrorClass.VERSION
    assert (found is None) or f"version {found}" in message.sentence
    assert "has not been changed" in message.sentence
    assert caught.value.kind == kind
    assert _tree_digest(project.root) == before


# -- no subprocess -------------------------------------------------------------------------------


def test_layer__operations__never_start_a_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess started")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)

    made = layer.create_project("p", title="P", root=tmp_path, source_mode="scopus", reviewer="Ada")
    opened = layer.open_project("p", root=tmp_path)
    layer.set_source_mode(layer.Session(opened), "scopus+openalex", reviewer="Ada")
    layer.step_progress(opened)
    layer.locks(opened)
    layer.translate(RuntimeError("x"))

    assert made.slug == "p"
