"""``prismabib cascade``: run the field-level cascade over what is still missing (ADR 0041, PR4).

Order: Scopus STANDARD by DOI (level 2), Scopus META by Scopus id (level 3), then OpenAlex
(level 4), each through its own Layer 0 writer. Levels 2 and 3 run only with ``--opt-in`` and an
explicit ``--scopus-budget``. Identifiers a *sealed* earlier run already answered (anything but
``refused``) are not asked again. An unsealed Scopus run is never resumed; OpenAlex resumes a
refusal-free unsealed run with an identical plan. The closing report is
read back from the sealed manifests on disk, not from this process's memory.

Not here yet: the ``openalex`` command, the store rebuild, and the Decision 9 step that gets a
DOI for a DOI-less candidate from level 4 before levels 2 and 3 (such a candidate goes to level 4
by title and year only).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import duckdb
import typer

from prismabib.capture.candidates import CandidateLog
from prismabib.capture.cascade import capture_scopus_cascade
from prismabib.capture.layout import (
    OPENALEX_DIRNAME,
    RUN_MANIFEST_FILENAME,
    SCOPUS_CASCADE_DIRNAME,
    is_sealed,
)
from prismabib.capture.manifest import CascadeRunManifest, OpenAlexRunManifest
from prismabib.capture.openalex import _held as _oa_held
from prismabib.capture.openalex import capture_openalex
from prismabib.errors import StoreError, ValidationError
from prismabib.project import Project
from prismabib.sources.openalex import normalise_doi
from prismabib.titles import normalise_title

_FIELDS = ("abstract", "authors", "affiliations", "keywords")
_SCOPUS_PREFIX = "scopus:2-s2.0-"
_Key = str | tuple[str, int]  # a DOI, or (normalised title, year)


@dataclass(frozen=True)
class _Target:
    """A record or candidate with a field still missing."""

    doi: str | None
    title: str
    year: int
    scopus_id: str | None = None
    needs_affiliations: bool = True

    @property
    def key(self) -> _Key:
        return self.doi or (normalise_title(self.title), self.year)


def _targets(project: Project) -> list[_Target]:
    found: dict[_Key, _Target] = {}
    if project.db_path.is_file():
        try:
            con = duckdb.connect(str(project.db_path), read_only=True)
            try:
                cols = ", ".join(f"{f}_outcome" for f in _FIELDS)
                rows = con.execute(
                    f"SELECT record_id, doi, title, year, {cols} FROM records"
                ).fetchall()
            finally:
                con.close()
        except duckdb.Error as exc:
            raise StoreError(f"cannot read the store {project.db_path}: {exc}") from exc
        for record_id, doi, title, year, *outcomes in rows:
            if all(o == "value" for o in outcomes):
                continue
            ndoi = normalise_doi(doi) if doi else None
            sid = (
                record_id.removeprefix(_SCOPUS_PREFIX)
                if record_id.startswith(_SCOPUS_PREFIX)
                else None
            )
            t = _Target(ndoi, title or "", year or 0, sid, outcomes[2] != "value")
            found.setdefault(t.key, t)
    log = CandidateLog(project.root / "decisions" / "candidates.jsonl")
    for view in log.view():
        doi = normalise_doi(view.doi) if view.doi else None
        t = _Target(doi, view.event.title, view.event.year)
        found.setdefault(t.key, t)
    return list(found.values())


def _sealed(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return sorted(d for d in base.iterdir() if d.is_dir() and is_sealed(d))


def _cascade_manifests(project: Project) -> list[CascadeRunManifest]:
    base = project.raw_dir / SCOPUS_CASCADE_DIRNAME
    return [
        CascadeRunManifest.model_validate_json(
            (d / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        for d in _sealed(base)
    ]


def _openalex_manifests(project: Project) -> list[OpenAlexRunManifest]:
    base = project.raw_dir / OPENALEX_DIRNAME
    return [
        OpenAlexRunManifest.model_validate_json(
            (d / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        for d in _sealed(base)
    ]


def _final_scopus(project: Project, view: Literal["STANDARD", "META"]) -> dict[str, str]:
    """Identifier -> outcome across sealed runs; ``value`` wins, else the latest."""
    final: dict[str, str] = {}
    for m in _cascade_manifests(project):
        for r in m.requests:
            if r.view == view:
                for o in r.outcomes:
                    if final.get(o.identifier) != "value":
                        final[o.identifier] = o.outcome
    return final


def _final_openalex(project: Project) -> dict[_Key, str]:
    final: dict[_Key, str] = {}
    for m in _openalex_manifests(project):
        for r in m.requests:
            for o in r.outcomes:
                i = o.identifier
                key: _Key = i if isinstance(i, str) else (normalise_title(i.title), i.year)
                if final.get(key) != "value":
                    final[key] = o.outcome
    return final


def _held(final: dict[str, str]) -> set[str]:
    return {i for i, o in final.items() if o != "refused"}


def _plan(project: Project, targets: list[_Target]) -> tuple[list[str], list[str]]:
    """Level 2 DOIs and level 3 Scopus ids still to ask, given the sealed runs on disk."""
    standard = _final_scopus(project, "STANDARD")
    held2, held3 = _held(standard), _held(_final_scopus(project, "META"))
    dois = [t.doi for t in targets if t.doi and t.needs_affiliations and t.doi not in held2]
    ids = [
        t.scopus_id
        for t in targets
        if t.scopus_id
        and t.needs_affiliations
        and t.scopus_id not in held3
        and standard.get(t.doi or "") != "value"
    ]
    return list(dict.fromkeys(dois)), list(dict.fromkeys(ids))


def _line(label: str, final: dict[str, str], *names: str) -> str:
    counts = Counter(final.values())
    return f"{label}: " + ", ".join(f"{n} {counts[n]}" for n in names)


def _report(project: Project, targets: list[_Target]) -> list[str]:
    std, meta = _final_scopus(project, "STANDARD"), _final_scopus(project, "META")
    oa = _final_openalex(project)
    remaining = sum(
        not (
            oa.get(t.key) == "value"
            or std.get(t.doi or "") == "value"
            or meta.get(t.scopus_id or "") == "value"
        )
        for t in targets
    )
    oa_counts = Counter(oa.values())
    return [
        _line("level 2", std, "value", "absent", "not_found", "refused"),
        _line("level 3", meta, "value", "absent", "not_found", "refused"),
        "openalex: " + ", ".join(f"{n} {oa_counts[n]}" for n in ("value", "not_found", "refused")),
        f"remaining unavailable: {remaining}",
    ]


def cascade(
    slug: Annotated[str, typer.Argument(help="Project slug.")],
    opt_in: Annotated[
        bool,
        typer.Option(
            "--opt-in",
            help="Enable Scopus levels 2 and 3 (and record their refusals). Needs --scopus-budget.",
        ),
    ] = False,
    scopus_budget: Annotated[
        int | None,
        typer.Option(
            "--scopus-budget",
            help=(
                "Cap on Scopus HTTP responses for levels 2 and 3 together. Required with "
                "--opt-in. The cap is approximate: a retried request can overshoot it by a few."
            ),
        ),
    ] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Ask OpenAlex again for held identifiers.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan per level. No HTTP call, no write.")
    ] = False,
    root: Annotated[Path | None, typer.Option("--root", "-r", show_default=False)] = None,
) -> None:
    """Fill missing fields: Scopus STANDARD, Scopus META, then OpenAlex.

    Levels 2 and 3 run only with --opt-in and --scopus-budget. Identifiers already
    answered by a sealed earlier run are not asked again. An unsealed Scopus run is not
    resumed; OpenAlex resumes a refusal-free unsealed run with an identical plan.
    """
    # Imported here: cli.py imports this module to register the command.
    from prismabib.cli import _echo, _reporting_errors

    with _reporting_errors():
        if opt_in and scopus_budget is None:
            raise ValidationError("--opt-in needs an explicit --scopus-budget N (requests).")
        if scopus_budget is not None and not opt_in:
            raise ValidationError("--scopus-budget only applies with --opt-in.")
        if scopus_budget is not None and scopus_budget < 1:
            raise ValidationError("--scopus-budget must be at least 1.")
        project = Project.open(slug, root=root)
        targets = _targets(project)
        dois, ids = _plan(project, targets) if opt_in else ([], [])
        quoted = [d for d in dict.fromkeys(dois) if '"' in d]
        for d in quoted:  # capture_scopus_cascade rejects the whole batch for one such DOI
            _echo(f"level 2: skipped DOI {d!r} (contains a double quote)")
        dois = [d for d in dois if d not in quoted]
        oa_held = set() if refresh else _oa_held(project.raw_dir / OPENALEX_DIRNAME)
        oa_dois = list(dict.fromkeys(t.doi for t in targets if t.doi and t.doi not in oa_held))
        oa_titles = [(t.title, t.year) for t in targets if not t.doi and t.key not in oa_held]

        if dry_run:
            _echo(
                f"level 2: {len(dois)} DOIs planned" if opt_in else "level 2: skipped (no --opt-in)"
            )
            _echo(f"level 3: up to {len(ids)} Scopus ids planned" if opt_in else "level 3: skipped")
            _echo(f"openalex: {len(oa_dois)} DOIs, {len(oa_titles)} titles planned")
            return

        left = scopus_budget or 0
        if opt_in and dois:
            m2 = capture_scopus_cascade(
                project, dois=dois, fallback_opt_in=True, budget_requests=left
            )
            left -= m2.requests_made if m2 else 0
            quota = bool(m2 and m2.stop_reason == "quota")
        else:
            quota = False
        if opt_in and not quota and left >= 1:
            ids = _plan(project, targets)[1]  # level 2 may have answered some records' DOIs
            if ids:
                capture_scopus_cascade(
                    project, scopus_ids=ids, fallback_opt_in=True, budget_requests=left
                )
        capture_openalex(
            project,
            dois=oa_dois,
            titles=oa_titles,
            fallback_opt_in=opt_in,
            refresh=refresh,
        )
        for line in _report(project, targets):
            _echo(line)
        _echo("Fetched data reaches `records` only after `prismabib build --rebuild`.")


__all__ = ["cascade"]
