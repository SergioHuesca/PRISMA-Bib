"""What the Query form validates, loads and writes, without widgets (ticket #327)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.errors import StepLockedError, ValidationError
from prismabib.gui import layer
from prismabib.gui.query_form_model import (
    ENRICHMENT_REASON,
    QueryInputs,
    chip_to_group,
    field_errors,
    group_to_chip,
    load,
    save_query,
)
from prismabib.project import Project
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR


@pytest.fixture(autouse=True)
def _user_file(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path_factory.mktemp("u") / "settings.env"))


def _make(tmp_path: Path, mode: str) -> Project:
    return layer.create_project(
        "demo",
        title="Demo",
        root=tmp_path,
        source_mode=mode,  # type: ignore[arg-type]
        reviewer="Ada",
    )


def _inputs(**kw: object) -> QueryInputs:
    base: dict[str, object] = {"terms": ["video anomaly"], "year_start": 2015, "year_end": 2024}
    return QueryInputs(**{**base, **kw})  # type: ignore[arg-type]


def test_chips__group_round_trip__joins_with_plus_and_keeps_cpp() -> None:
    assert chip_to_group(group_to_chip(["C++", "video"])) == ["C++", "video"]
    assert chip_to_group(" + ") == []


def test_save_query__scopus__writes_terms_compound_and_years_keeping_comments(
    tmp_path: Path,
) -> None:
    project = _make(tmp_path, "scopus")
    before = (project.root / "project.toml").read_text(encoding="utf-8")
    save_query(project, _inputs(compound_terms=[["abnormal event", "video"]]))
    text = (project.root / "project.toml").read_text(encoding="utf-8")
    assert '"video anomaly"' in text
    assert 'compound_terms = [\n  { all = ["abnormal event", "video"] },\n]' in text
    assert text.startswith(before.split("[query]")[0])  # comments above are untouched
    assert project.criteria.temporal.year_start == 2015
    assert project.criteria.temporal.year_end == 2024
    assert load(project).compound_terms == [["abnormal event", "video"]]


def test_save_query__openalex__writes_search_and_subject_areas(tmp_path: Path) -> None:
    project = _make(tmp_path, "openalex")
    save_query(project, _inputs(terms=["carbon capture", "O'Brien"], subject_areas=["COMP"]))
    assert project.criteria.subject_areas == ["COMP"]
    loaded = load(project)
    assert loaded.terms == ["carbon capture", "O'Brien"]
    assert loaded.subject_areas == ["COMP"]


def test_save_query__openalex_single_compound__is_an_and_search(tmp_path: Path) -> None:
    project = _make(tmp_path, "openalex")
    save_query(project, _inputs(terms=[], compound_terms=[["a b", "c"]]))
    assert """search = '"a b" AND "c"'""" in (project.root / "project.toml").read_text("utf-8")
    assert load(project).compound_terms == [["a b", "c"]]


@pytest.mark.parametrize("mode", ["scopus", "scopus+openalex"])
def test_field_errors__subject_areas_in_a_scopus_mode__names_enrichment(
    tmp_path: Path, mode: str
) -> None:
    project = _make(tmp_path, mode)
    assert field_errors(project, _inputs(subject_areas=["COMP"])) == {
        "subject_areas": ENRICHMENT_REASON
    }
    assert field_errors(project, _inputs()) == {}


def test_field_errors__openalex_unknown_subject_area__is_the_librarys_message(
    tmp_path: Path,
) -> None:
    project = _make(tmp_path, "openalex")
    errors = field_errors(project, _inputs(subject_areas=["NOPE"]))
    assert list(errors) == ["subject_areas"] and "unknown subject-area" in errors["subject_areas"]


def test_field_errors__each_wrong_field__is_named_with_the_librarys_message(
    tmp_path: Path,
) -> None:
    project = _make(tmp_path, "openalex")
    errors = field_errors(project, _inputs(terms=["a (b)"], year_start=2030, year_end=2020))
    assert "parentheses" in errors["terms"]
    assert "precedes" in errors["years"]
    assert field_errors(project, _inputs(terms=[]))["terms"].startswith("Add at least one")
    assert "year" in field_errors(project, _inputs(year_start=None))["years"]
    assert (
        "grouping"
        in field_errors(project, _inputs(terms=["a"], compound_terms=[["b", "c"]]))[
            "compound_terms"
        ]
    )


def test_save_query__invalid__raises_and_writes_nothing(tmp_path: Path) -> None:
    project = _make(tmp_path, "openalex")
    before = [(project.root / n).read_bytes() for n in ("project.toml", "criteria.yaml")]
    with pytest.raises(ValidationError):
        save_query(project, _inputs(terms=["a (b)"]))
    assert before == [(project.root / n).read_bytes() for n in ("project.toml", "criteria.yaml")]


def test_load__hand_edited_files__fill_the_form(tmp_path: Path) -> None:
    project = _make(tmp_path, "scopus")
    toml = project.root / "project.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace(
            "terms = []\ncompound_terms = []",
            'terms = ["x", "y"] # mine\ncompound_terms = [["p", "q"]]',
        ),
        encoding="utf-8",
    )
    loaded = load(project)
    assert loaded.terms == ["x", "y"] and loaded.compound_terms == [["p", "q"]]
    assert loaded.year_start == 1900 and loaded.load_problem is None


def test_load__hand_written_openalex_search__is_kept_as_text_and_replaced_on_save(
    tmp_path: Path,
) -> None:
    project = _make(tmp_path, "openalex")
    toml = project.root / "project.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace('search = ""', "search = 'policy NOT carbon'"),
        encoding="utf-8",
    )
    loaded = load(project)
    assert loaded.unparsed_search == "policy NOT carbon" and loaded.terms == []
    save_query(project, _inputs())
    assert "NOT carbon" not in toml.read_text(encoding="utf-8")


def test_load__broken_criteria__reports_instead_of_raising(tmp_path: Path) -> None:
    project = _make(tmp_path, "openalex")
    (project.root / "criteria.yaml").write_text("version: x\n", encoding="utf-8")
    loaded = load(project)
    assert loaded.load_problem and loaded.year_start is None


def test_layer_save_query__after_a_run_started__is_refused(tmp_path: Path) -> None:
    project = _make(tmp_path, "openalex")
    (project.raw_dir / "openalex_search" / "run1").mkdir(parents=True)
    with layer.Session(project) as session, pytest.raises(StepLockedError) as caught:
        layer.save_query(session, _inputs())
    assert "A different search needs a new Project" in str(caught.value)
    assert 'search = ""' in (project.root / "project.toml").read_text(encoding="utf-8")
