"""``build_openalex_query_for_project`` reads ``[openalex_query]`` (ADR 0042 Decision 7, 38 and 40)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.capture.openalex_search_commands import _duration
from prismabib.errors import ConfigError, ValidationError
from prismabib.project import Project
from prismabib.query import build_openalex_query_for_project, build_query_for_project

pytestmark = pytest.mark.integration


def project_with(tmp_path: Path, extra: str) -> Project:
    project = Project.init("demo", title="Demo", root=tmp_path, source_mode="openalex", actor="t")
    path = project.root / "project.toml"
    # Drop the empty scaffold a new mode-openalex project carries, then add the test's table.
    base = path.read_text(encoding="utf-8").split("\n# The OpenAlex search")[0]
    path.write_text(base + "\n" + extra, encoding="utf-8")
    return project


def test_project__search_string__is_returned_as_written(tmp_path: Path) -> None:
    project = project_with(tmp_path, "[openalex_query]\nsearch = '\"carbon capture\" AND policy'\n")

    assert build_openalex_query_for_project(project) == '"carbon capture" AND policy'


def test_project__unicode_and_multiline__accepted(tmp_path: Path) -> None:
    project = project_with(
        tmp_path, '[openalex_query]\nsearch = """\nenergía OR "política pública"\n"""\n'
    )

    assert build_openalex_query_for_project(project) == 'energía OR "política pública"\n'


def test_project__renders_search_only_and_leaves_the_scopus_query_alone(tmp_path: Path) -> None:
    """Verification 40: no select or filter in the string; the [query] table renders as before."""
    project = project_with(
        tmp_path,
        "[openalex_query]\nsearch = 'solar'\n",
    )
    text = (project.root / "project.toml").read_text(encoding="utf-8")
    (project.root / "project.toml").write_text(
        text.replace("\nterms = []", '\nterms = ["x y"]'), encoding="utf-8"
    )

    assert build_openalex_query_for_project(project) == "solar"
    assert build_query_for_project(project) == 'TITLE-ABS-KEY("x y")'


def test_project__missing_table__names_the_file_and_shows_an_example(tmp_path: Path) -> None:
    project = project_with(tmp_path, "")

    with pytest.raises(ConfigError, match=r"no \[openalex_query\] table") as caught:
        build_openalex_query_for_project(project)
    assert "project.toml" in str(caught.value)
    assert "search = " in str(caught.value)


def test_project__table_is_not_a_table__refused(tmp_path: Path) -> None:
    project = project_with(tmp_path, "")
    path = project.root / "project.toml"
    path.write_text("openalex_query = 'solar'\n" + path.read_text(encoding="utf-8"))

    with pytest.raises(ConfigError, match=r"no \[openalex_query\] table"):
        build_openalex_query_for_project(project)


@pytest.mark.parametrize("value", ["''", "'   '"])
def test_project__blank_query__refused(tmp_path: Path, value: str) -> None:
    project = project_with(tmp_path, f"[openalex_query]\nsearch = {value}\n")

    with pytest.raises(ValidationError, match="empty"):
        build_openalex_query_for_project(project)


def test_project__missing_search_key__refused(tmp_path: Path) -> None:
    project = project_with(tmp_path, "[openalex_query]\n")

    with pytest.raises(ConfigError, match="needs search"):
        build_openalex_query_for_project(project)


def test_project__search_not_a_string__refused(tmp_path: Path) -> None:
    project = project_with(tmp_path, "[openalex_query]\nsearch = ['a', 'b']\n")

    with pytest.raises(ConfigError, match="needs search"):
        build_openalex_query_for_project(project)


@pytest.mark.parametrize("key", ["filter", "search.title", "sort", "sample", "select"])
def test_project__extra_key__refused_and_named(tmp_path: Path, key: str) -> None:
    project = project_with(tmp_path, f"[openalex_query]\nsearch = 'a'\n'{key}' = 'x'\n")

    with pytest.raises(ConfigError) as caught:
        build_openalex_query_for_project(project)
    assert repr(key) in str(caught.value)
    assert "Only 'search' is allowed" in str(caught.value)


def test_project__invalid_toml_and_missing_file__config_error(tmp_path: Path) -> None:
    project = project_with(tmp_path, "[openalex_query\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        build_openalex_query_for_project(project)

    (project.root / "project.toml").unlink()
    with pytest.raises(ConfigError, match="not found"):
        build_openalex_query_for_project(project)


def test_project__scopus_syntax_in_the_table__refused_not_sent(tmp_path: Path) -> None:
    project = project_with(tmp_path, "[openalex_query]\nsearch = 'TITLE-ABS-KEY(\"x\")'\n")

    with pytest.raises(ValidationError, match="Scopus syntax"):
        build_openalex_query_for_project(project)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (None, "at 00:00 UTC"),
        (float("inf"), "at 00:00 UTC"),
        (-5.0, "at 00:00 UTC"),
        (0.0, "in 0 min"),
        (59.0, "in 1 min"),
        (45 * 60, "in 45 min"),
        (3600.0, "in 1 h 00 min (00:00 UTC)"),
        (34265.0, "in 9 h 32 min (00:00 UTC)"),
    ],
)
def test_duration__reset_text(seconds: float | None, text: str) -> None:
    assert _duration(seconds) == text


# -- the scaffold written for a new mode-openalex project --------------------------------------


def _new_toml(tmp_path: Path, mode: str | None, name: str = "demo") -> str:
    project = Project.init(name, title="Demo", root=tmp_path, source_mode=mode, actor="t")  # type: ignore[arg-type]
    return (project.root / "project.toml").read_text(encoding="utf-8")


def test_scaffold__only_mode_openalex_gets_an_openalex_query_section(tmp_path: Path) -> None:
    import tomllib

    texts = {
        m: _new_toml(tmp_path, m, f"p-{i}")
        for i, m in enumerate([None, "scopus", "scopus+openalex", "openalex"])
    }

    for mode in (None, "scopus", "scopus+openalex"):
        assert "openalex_query" not in texts[mode]
    assert "[openalex_query]" in texts["openalex"]
    # The Scopus-side bytes are exactly the legacy ones plus the [sources] table, as before.
    legacy = texts[None].replace('slug = "p-0"', 'slug = "X"')
    for mode in ("scopus", "scopus+openalex"):
        same = texts[mode].replace(f'slug = "p-{1 if mode == "scopus" else 2}"', 'slug = "X"')
        assert same == legacy + f'\n[sources]\nmode = "{mode}"\n'
    assert tomllib.loads(texts["openalex"])["openalex_query"] == {"search": ""}


def test_scaffold__unedited_placeholder__is_refused_and_never_returned(tmp_path: Path) -> None:
    project = Project.init("demo", title="Demo", root=tmp_path, source_mode="openalex", actor="t")

    with pytest.raises(ValidationError, match="edit the search placeholder") as caught:
        build_openalex_query_for_project(project)
    assert "project.toml" in str(caught.value)


def test_scaffold__edited_placeholder__works(tmp_path: Path) -> None:
    project = Project.init("demo", title="Demo", root=tmp_path, source_mode="openalex", actor="t")
    path = project.root / "project.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('search = ""', "search = 'solar OR wind'"),
        encoding="utf-8",
    )

    assert build_openalex_query_for_project(project) == "solar OR wind"


def test_scaffold__sources_set_on_existing_project__adds_no_section(tmp_path: Path) -> None:
    from prismabib.source_mode import set_mode

    project = Project.init("demo", title="Demo", root=tmp_path)
    before = (project.root / "project.toml").read_text(encoding="utf-8")
    set_mode(project, "scopus", actor="t")
    set_mode(project, "openalex", actor="t")

    after = (project.root / "project.toml").read_text(encoding="utf-8")
    assert "openalex_query" not in after
    assert after.startswith(before)
    with pytest.raises(ConfigError, match=r"no \[openalex_query\] table"):
        build_openalex_query_for_project(project)
