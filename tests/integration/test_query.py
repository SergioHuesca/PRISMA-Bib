"""Integration tests for ``build_query_for_project`` (BUILD_PLAN §3.1, lines 318-340).

``build_query_for_project`` reads ``project.toml`` from disk, so it is an
integration test rather than a unit one.

Why this file exists at all: ``capture_search`` calls this function whenever
``query is None``, which is the ordinary path, and it was previously executed by
no test. That matters more here than coverage arithmetic usually does. This
module already shipped a defect that rendered a *silently wrong* Boolean query --
given §3.1's real ``compound_terms = [{ all = [...] }]`` it emitted
``TITLE-ABS-KEY("all")`` and raised nothing. A wrong query produces a wrong
corpus, and every count, figure, and cited number downstream is then wrong with
no signal anywhere. BUILD_PLAN §1.4 names that failure class as the reason this
architecture exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.errors import ConfigError, ValidationError
from prismabib.project import Project
from prismabib.query import build_query_for_project

# BUILD_PLAN §3.1 lines 327-336, verbatim.
_SPEC_QUERY_TABLE = """
[query]
terms = [
  "video anomaly detection",
  "surveillance anomaly detection",
]
compound_terms = [
  { all = ["abnormal event detection", "video"] },
]
fields = ["TITLE-ABS-KEY"]
"""

# BUILD_PLAN line 776, verbatim.
_SPEC_QUERY_STRING = (
    'TITLE-ABS-KEY("video anomaly detection") '
    'OR TITLE-ABS-KEY("surveillance anomaly detection") '
    'OR (TITLE-ABS-KEY("abnormal event detection") AND TITLE-ABS-KEY("video"))'
)


def _project_with_query(tmp_path: Path, query_table: str) -> Project:
    """Create a project whose ``project.toml`` carries ``query_table``."""
    project = Project.init("demo", title="Demo", root=tmp_path)
    toml_path = project.root / "project.toml"
    body = toml_path.read_text(encoding="utf-8").split("[query]")[0]
    toml_path.write_text(body + query_table.lstrip("\n"), encoding="utf-8")
    return project


@pytest.mark.integration
def test_build_query_for_project__spec_example__renders_the_frozen_string(
    tmp_path: Path,
) -> None:
    project = _project_with_query(tmp_path, _SPEC_QUERY_TABLE)

    rendered = build_query_for_project(project)

    assert rendered == _SPEC_QUERY_STRING


@pytest.mark.integration
def test_build_query_for_project__compound_group__renders_as_an_and_group(
    tmp_path: Path,
) -> None:
    """The production path through the bare-sequence branch of the coercer.

    ``build_query_for_project`` passes ``[group.all for group in ...]`` -- plain
    lists of strings, not the ``{"all": [...]}`` mappings that appear in the TOML.
    So production always takes the sequence branch while a unit test passing the
    mapping form exercises the other one. Both need covering; this is the half
    that actually runs.
    """
    project = _project_with_query(
        tmp_path,
        '[query]\nterms = []\ncompound_terms = [{ all = ["a", "b", "c"] }]\nfields = ["TITLE-ABS-KEY"]\n',
    )

    rendered = build_query_for_project(project)

    assert rendered == ('(TITLE-ABS-KEY("a") AND TITLE-ABS-KEY("b") AND TITLE-ABS-KEY("c"))')


@pytest.mark.integration
def test_build_query_for_project__no_query_table__raises_config_error(
    tmp_path: Path,
) -> None:
    project = Project.init("demo", title="Demo", root=tmp_path)
    toml_path = project.root / "project.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8").split("[query]")[0], encoding="utf-8"
    )

    with pytest.raises(ConfigError) as excinfo:
        build_query_for_project(project)

    assert "[query]" in str(excinfo.value)


@pytest.mark.integration
def test_build_query_for_project__empty_terms__raises_rather_than_matching_everything(
    tmp_path: Path,
) -> None:
    """An empty query must fail loudly, not quietly return the whole database.

    A freshly scaffolded ``project.toml`` has empty ``terms``. Rendering that as
    an empty string would send an unbounded search and silently define the corpus
    as "everything", which no reviewer would ever catch from the output.
    """
    project = _project_with_query(
        tmp_path,
        '[query]\nterms = []\ncompound_terms = []\nfields = ["TITLE-ABS-KEY"]\n',
    )

    with pytest.raises(ValidationError):
        build_query_for_project(project)


@pytest.mark.integration
def test_build_query_for_project__malformed_toml__raises_config_error_naming_the_path(
    tmp_path: Path,
) -> None:
    project = Project.init("demo", title="Demo", root=tmp_path)
    (project.root / "project.toml").write_text("[query\nterms = [", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        build_query_for_project(project)

    assert "project.toml" in str(excinfo.value)


@pytest.mark.integration
def test_build_query_for_project__unknown_compound_key__raises_config_error(
    tmp_path: Path,
) -> None:
    """``{any = [...]}`` must be rejected, never silently dropped.

    Dropping it would narrow the corpus without a word of warning -- the operator
    would get a smaller result set and no reason to doubt it.
    """
    project = _project_with_query(
        tmp_path,
        '[query]\nterms = ["x"]\ncompound_terms = [{ any = ["a", "b"] }]\nfields = ["TITLE-ABS-KEY"]\n',
    )

    with pytest.raises(ConfigError):
        build_query_for_project(project)


@pytest.mark.integration
def test_build_query_for_project__missing_project_toml__raises_config_error(
    tmp_path: Path,
) -> None:
    """A project directory without its ``project.toml`` names the expected path."""
    project = Project.init("demo", title="Demo", root=tmp_path)
    (project.root / "project.toml").unlink()

    with pytest.raises(ConfigError) as excinfo:
        build_query_for_project(project)

    assert str(project.root / "project.toml") in str(excinfo.value)


@pytest.mark.integration
def test_build_query_for_project__bare_string_compound_term__names_the_fix(
    tmp_path: Path,
) -> None:
    """The most likely `[query]` mistake must get the message written for it.

    ``compound_terms = ["computer vision", "video"]`` is the natural thing to
    write if you read ``terms`` and assume ``compound_terms`` takes the same
    shape. ``_coerce_compound_group`` already carries a message that names the
    mistake and writes out the corrected line -- but ``_QuerySpec`` validated
    first, so a ``project.toml`` author instead got a raw Pydantic
    ``model_type`` error naming ``_CompoundTerm``, a private class they have
    never heard of and cannot look up.

    That made the best error in this module reachable only by direct
    ``build_query`` callers, i.e. essentially never. This is not hypothetical:
    the first person to write a real query for this tool hit it immediately
    and got the Pydantic dump.
    """
    project = _project_with_query(
        tmp_path,
        '[query]\nterms = ["baseball"]\ncompound_terms = ["computer vision"]\n'
        'fields = ["TITLE-ABS-KEY"]\n',
    )

    with pytest.raises(ConfigError) as excinfo:
        build_query_for_project(project)

    message = str(excinfo.value)
    assert "must not be a bare string" in message
    assert "{'all': ['computer vision']}" in message
    # The Pydantic dump names a private class the reader cannot act on.
    assert "_CompoundTerm" not in message


@pytest.mark.integration
def test_build_query_for_project__single_group_not_a_list__names_the_missing_brackets(
    tmp_path: Path,
) -> None:
    """``compound_terms = { all = [...] }`` must not be diagnosed as a bare string.

    TOML's inline-table syntax makes the un-bracketed form look right, and a
    reader who has seen ``{ all = [...] }`` in the docs may well drop the
    surrounding brackets. Iterating a mapping yields its *keys*, so the
    pre-Pydantic pass used to hand this author
    ``did you mean the single-term group {'all': ['all']}?`` -- a suggestion
    about the literal word ``all`` that is worse than the Pydantic error it
    replaced, because it is confidently wrong rather than merely obscure.
    """
    project = _project_with_query(
        tmp_path,
        '[query]\nterms = ["baseball"]\ncompound_terms = { all = ["a", "b"] }\n'
        'fields = ["TITLE-ABS-KEY"]\n',
    )

    with pytest.raises(ConfigError) as excinfo:
        build_query_for_project(project)

    message = str(excinfo.value)
    assert "must be a list of groups, not a single group" in message
    assert "[{'all': ['a', 'b']}]" in message
    # The old diagnosis: a bare-string complaint about the key name.
    assert "bare string" not in message
    assert "{'all': ['all']}" not in message


#: Every ``[query].compound_terms`` shape ``tomllib`` can produce that
#: ``_QuerySpec`` rejects, paired with the phrase the author must be told. The
#: point of enumerating them is the *second* assertion in the test below: the
#: pre-Pydantic pass has to be total over this key, because any shape it lets
#: through reaches ``_QuerySpec`` and comes back as a dump naming a private
#: class -- the exact failure this pass exists to prevent.
_MALFORMED_COMPOUND_TERMS: tuple[tuple[str, str, str], ...] = (
    (
        "single_inline_table",
        'compound_terms = { all = ["a", "b"] }',
        "not a single group",
    ),
    (
        "single_table_header",
        '[query.compound_terms]\nall = ["a", "b"]',
        "not a single group",
    ),
    (
        "table_of_tables",
        '[query.compound_terms.first]\nall = ["a"]',
        "not a single table",
    ),
    (
        "bare_strings",
        'compound_terms = ["computer vision", "video"]',
        "must not be a bare string",
    ),
    (
        "one_bare_string",
        'compound_terms = "computer vision"',
        "not a single string",
    ),
    ("integer", "compound_terms = 3", "must be a list of groups"),
    ("boolean", "compound_terms = true", "must be a list of groups"),
    ("date", "compound_terms = 2026-08-26", "must be a list of groups"),
    (
        "nested_list",
        'compound_terms = [["a", "b"]]',
        "not a bare list",
    ),
    (
        "mixed_entries",
        'compound_terms = [{ all = ["a"] }, 7]',
        "entry must be a table",
    ),
    (
        "all_is_a_string",
        'compound_terms = [{ all = "video" }]',
        "'all' value must be a list of strings",
    ),
    (
        "all_holds_numbers",
        "compound_terms = [{ all = [1, 2] }]",
        "'all' value must be a list of strings",
    ),
    (
        "unknown_group_key",
        'compound_terms = [{ any = ["a", "b"] }]',
        "exactly the key 'all'",
    ),
)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("compound_terms_line", "expected_phrase"),
    [pytest.param(line, phrase, id=name) for name, line, phrase in _MALFORMED_COMPOUND_TERMS],
)
def test_build_query_for_project__malformed_compound_terms__names_the_mistake(
    tmp_path: Path, compound_terms_line: str, expected_phrase: str
) -> None:
    """Every wrong ``compound_terms`` shape is diagnosed here, never by Pydantic.

    Two properties at once. First, the message names the actual mistake.
    Second -- and this is what stops the two validation paths from drifting --
    no message reaches the author as a Pydantic dump: if this pass ever accepts
    a shape ``_QuerySpec`` rejects, ``_CompoundTerm`` reappears in the output
    and this test goes red. It also pins the failure *type*: a scalar used to
    escape as an uncaught ``TypeError``, which the CLI reports as a bug in
    prismabib rather than a mistake in the file.
    """
    project = _project_with_query(
        tmp_path, f'[query]\nterms = ["baseball"]\n{compound_terms_line}\n'
    )

    with pytest.raises(ConfigError) as excinfo:
        build_query_for_project(project)

    message = str(excinfo.value)
    assert expected_phrase in message
    assert "_CompoundTerm" not in message
    assert "validation error" not in message


@pytest.mark.integration
@pytest.mark.parametrize(
    "query_table",
    [
        pytest.param(
            '[query]\nterms = []\ncompound_terms = [{ all = ["a", "b"] }]\n'
            'fields = ["TITLE-ABS-KEY"]\n',
            id="inline_table_list",
        ),
        pytest.param(
            '[query]\nterms = []\nfields = ["TITLE-ABS-KEY"]\n\n'
            '[[query.compound_terms]]\nall = ["a", "b"]\n',
            id="doubled_table_header",
        ),
    ],
)
def test_build_query_for_project__both_toml_spellings__render_the_same_group(
    tmp_path: Path, query_table: str
) -> None:
    """The ``[[query.compound_terms]]`` form the error messages recommend must work.

    An error message that suggests a spelling the parser rejects is worse than
    no suggestion. It is also the shape a longer real query naturally grows
    into, so the stricter pre-Pydantic pass must not reject it.
    """
    project = _project_with_query(tmp_path, query_table)

    rendered = build_query_for_project(project)

    assert rendered == '(TITLE-ABS-KEY("a") AND TITLE-ABS-KEY("b"))'


@pytest.mark.integration
def test_build_query_for_project__empty_all_group__still_raises_validation_error(
    tmp_path: Path,
) -> None:
    """An empty ``all`` list is a ``ValidationError``, not a ``ConfigError``.

    The shape is legal TOML and legal §3.1; what is wrong is the *content*.
    The pre-Pydantic pass sits in front of that check and must not swallow it
    into the wrong error class, which callers distinguish.
    """
    project = _project_with_query(
        tmp_path,
        '[query]\nterms = ["baseball"]\ncompound_terms = [{ all = [] }]\n'
        'fields = ["TITLE-ABS-KEY"]\n',
    )

    with pytest.raises(ValidationError):
        build_query_for_project(project)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("misspelled", "suggested", "query_table"),
    [
        pytest.param(
            "compound_term",
            "compound_terms",
            '[query]\nterms = ["baseball"]\n'
            'compound_term = [{ all = ["a", "b"] }]\nfields = ["TITLE-ABS-KEY"]\n',
            id="compound_term",
        ),
        pytest.param(
            "field",
            "fields",
            '[query]\nterms = ["baseball"]\ncompound_terms = []\nfield = ["AUTHKEY"]\n',
            id="field",
        ),
        pytest.param(
            "term",
            "terms",
            '[query]\nterm = ["baseball"]\n'
            'compound_terms = [{ all = ["a"] }]\nfields = ["TITLE-ABS-KEY"]\n',
            id="term",
        ),
    ],
)
def test_build_query_for_project__misspelled_query_key__raises_instead_of_ignoring_it(
    tmp_path: Path, misspelled: str, suggested: str, query_table: str
) -> None:
    """A dropped ``s`` must not silently narrow the corpus.

    ``_QuerySpec`` took Pydantic's default ``extra="ignore"``, so
    ``compound_term = [{ all = [...] }]`` was discarded and the query rendered
    from whatever keys were left -- a search narrower than the file describes,
    returning a plausible-looking result set with nothing raised anywhere.
    ``field = ["AUTHKEY"]`` searched ``TITLE-ABS-KEY`` instead. That is the
    silently-wrong-corpus class BUILD_PLAN §1.4 names, and it is worse than a
    crash because no reviewer can see it in the output.
    """
    project = _project_with_query(tmp_path, query_table)

    with pytest.raises(ConfigError) as excinfo:
        build_query_for_project(project)

    message = str(excinfo.value)
    assert misspelled in message
    assert f"did you mean {suggested!r}?" in message
    assert "_QuerySpec" not in message


@pytest.mark.integration
def test_build_query_for_project__query_is_not_a_table__names_the_expected_shape(
    tmp_path: Path,
) -> None:
    """A scalar ``query`` key must not surface ``_QuerySpec`` to the author.

    Same defect class as the compound-term one: the reader is shown the name of
    a private class instead of the shape their file should have.
    """
    project = Project.init("demo", title="Demo", root=tmp_path)
    toml_path = project.root / "project.toml"
    body = toml_path.read_text(encoding="utf-8").split("[query]")[0]
    toml_path.write_text('query = "video anomaly detection"\n\n' + body, encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        build_query_for_project(project)

    message = str(excinfo.value)
    assert "[query] must be a table" in message
    assert "_QuerySpec" not in message
