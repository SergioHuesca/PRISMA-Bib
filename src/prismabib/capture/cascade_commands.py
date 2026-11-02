"""``prismabib cascade`` and ``prismabib openalex`` (ADR 0041, Decisions 7 to 9).

Order of ``cascade``: OpenAlex title search for DOI-less targets first (Decision 9), so a DOI
it finds is written back to the candidate log and reaches Scopus; then Scopus STANDARD by DOI
(level 2), Scopus META by Scopus id (level 3), then OpenAlex by DOI (level 4), each through
its own Layer 0 writer. Levels 2 and 3 run only with ``--opt-in`` and an
explicit ``--scopus-budget``. Identifiers a *sealed* earlier run already answered (anything but
``refused``) are not asked again. An unsealed Scopus run is never resumed; OpenAlex resumes a
refusal-free unsealed run with an identical plan. The first report table is read back from
the sealed manifests on disk, not from this process's memory. A real run then rebuilds the
store and prints the second table, per field, from the provenance columns of ``records``.
``openalex`` is the level 4 path alone, over the same targets.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import duckdb
import typer

from prismabib.capture.candidates import (
    AddResult,
    CandidateEvent,
    CandidateLog,
    CandidateRow,
)
from prismabib.capture.cascade import capture_scopus_cascade
from prismabib.capture.layout import (
    OPENALEX_DIRNAME,
    RUN_MANIFEST_FILENAME,
    SCOPUS_CASCADE_DIRNAME,
    is_sealed,
)
from prismabib.capture.manifest import (
    CascadeRunManifest,
    OpenAlexRunManifest,
    OpenAlexTitle,
)
from prismabib.capture.openalex import capture_openalex, held
from prismabib.errors import StoreError, ValidationError
from prismabib.project import Project
from prismabib.sources.openalex import lookup_outcome, normalise_doi, parse_work
from prismabib.store.load import build_store
from prismabib.titles import normalise_title

_FIELDS = ("abstract", "authors", "affiliations", "keywords")
_SCOPUS_PREFIX = "scopus:2-s2.0-"
_Key = str | tuple[str, int]  # a DOI, or (normalised title, year)
_REVIEWER = "prismabib-openalex"  # the name on a DOI write-back event


def _title_key(title: str, year: int) -> tuple[str, int]:
    return (normalise_title(title), year)


@dataclass(frozen=True)
class _Target:
    """A record or candidate with a field still missing."""

    doi: str | None
    title: str
    year: int
    scopus_id: str | None = None
    needs_affiliations: bool = True
    candidate_id: str | None = None

    @property
    def key(self) -> _Key:
        return self.doi or _title_key(self.title, self.year)


def _targets(project: Project) -> list[_Target]:
    found: dict[_Key, _Target] = {}
    record_titles: set[tuple[str, int]] = set()  # every record, complete or not
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
            record_titles.add(_title_key(title or "", year or 0))
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
        t = _Target(doi, view.event.title, view.event.year, candidate_id=view.event.candidate_id)
        if _title_key(t.title, t.year) not in record_titles:  # else it is a record already
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


def _openalex_inputs(
    project: Project, targets: list[_Target], refresh: bool
) -> tuple[list[str], list[tuple[str, int]]]:
    """DOIs and (title, year) pairs OpenAlex has not answered yet; unusable titles are dropped."""
    already = set() if refresh else held(project.raw_dir / OPENALEX_DIRNAME)
    dois = list(dict.fromkeys(t.doi for t in targets if t.doi and t.doi not in already))
    titles = [
        (t.title, t.year)
        for t in targets
        if not t.doi and normalise_title(t.title) and t.year and t.key not in already
    ]
    return dois, titles


def _found_dois(project: Project, wanted: set[tuple[str, int]]) -> dict[tuple[str, int], str]:
    """The DOI the sealed OpenAlex title searches returned, per wanted (title, year)."""
    found: dict[tuple[str, int], str] = {}
    for run_dir in _sealed(project.raw_dir / OPENALEX_DIRNAME):
        manifest = OpenAlexRunManifest.model_validate_json(
            (run_dir / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        for request in manifest.requests:
            for o in request.outcomes:
                if o.outcome != "value" or not isinstance(o.identifier, OpenAlexTitle):
                    continue
                key = _title_key(o.identifier.title, o.identifier.year)
                if key not in wanted or key in found:
                    continue
                try:
                    body = json.loads((run_dir / request.file).read_text(encoding="utf-8"))
                except ValueError:  # a blank stored body
                    continue
                results = body.get("results") if isinstance(body, dict) else None
                works = [parse_work(r) for r in results or [] if isinstance(r, dict)]
                work, _ = lookup_outcome(works, title=o.identifier.title, year=o.identifier.year)
                if work is not None and work.doi:
                    found[key] = work.doi
    return found


def _write_back_dois(
    log: CandidateLog, targets: list[_Target], found: dict[tuple[str, int], str]
) -> list[AddResult]:
    """Log the DOI OpenAlex found for each candidate; the log never replaces a stored DOI."""
    results: list[AddResult] = []
    for t in targets:
        doi = found.get(_title_key(t.title, t.year))
        if t.candidate_id and doi:
            row = CandidateRow(t.title, t.year, doi)
            event = CandidateEvent.from_row(row, reviewer=_REVIEWER, pasted_at=datetime.now(UTC))
            results.append(log.add(event))
    return results


def _write_back(project: Project, targets: list[_Target]) -> None:
    """Write back the OpenAlex-found DOIs of the DOI-less candidates among ``targets``."""
    from prismabib.cli import _echo

    bare = [t for t in targets if t.candidate_id and not t.doi]
    found = _found_dois(project, {_title_key(t.title, t.year) for t in bare})
    log = CandidateLog(project.root / "decisions" / "candidates.jsonl")
    counts = Counter(_write_back_dois(log, bare, found))
    if counts:
        _echo("candidate DOIs from OpenAlex: " + ", ".join(f"{k} {n}" for k, n in counts.items()))


def _field_report(project: Project) -> list[str]:
    """Per-field counts by level, read from the provenance columns of the rebuilt store."""
    try:
        con = duckdb.connect(str(project.db_path), read_only=True)
        try:
            lines = ["per field, Scopus records (from the store):"]
            for f in _FIELDS:
                rows = con.execute(
                    f"SELECT {f}_level, count(*) FROM records WHERE starts_with(record_id, ?) "
                    "GROUP BY 1",
                    [_SCOPUS_PREFIX],
                ).fetchall()
                n = dict(rows)
                levels = ", ".join(f"level {i} {n.get(i, 0)}" for i in range(1, 6))
                lines.append(f"{f}: {levels}, unavailable {n.get(0, 0)}")
        finally:
            con.close()
    except duckdb.Error as exc:
        raise StoreError(f"cannot read the store {project.db_path}: {exc}") from exc
    return lines


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

        if dry_run:
            dois, ids = _plan(project, targets) if opt_in else ([], [])
            oa_dois, oa_titles = _openalex_inputs(project, targets, refresh)
            _echo(
                f"level 2: {len(dois)} DOIs planned" if opt_in else "level 2: skipped (no --opt-in)"
            )
            _echo(f"level 3: up to {len(ids)} Scopus ids planned" if opt_in else "level 3: skipped")
            _echo(f"openalex: {len(oa_dois)} DOIs, {len(oa_titles)} titles planned")
            return

        # Decision 9: DOI-less targets go to OpenAlex first, so a DOI it finds reaches Scopus.
        _, oa_titles = _openalex_inputs(project, targets, refresh)
        capture_openalex(project, titles=oa_titles, fallback_opt_in=opt_in, refresh=refresh)
        _write_back(project, targets)
        targets = _targets(project)

        dois, ids = _plan(project, targets) if opt_in else ([], [])
        quoted = [d for d in dict.fromkeys(dois) if '"' in d]
        for d in quoted:  # capture_scopus_cascade rejects the whole batch for one such DOI
            _echo(f"level 2: skipped DOI {d!r} (contains a double quote)")
        dois = [d for d in dois if d not in quoted]
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
        oa_dois, _ = _openalex_inputs(project, targets, refresh)
        capture_openalex(project, dois=oa_dois, fallback_opt_in=opt_in, refresh=refresh)
        for line in _report(project, targets):
            _echo(line)
        build_store(project, rebuild=True)
        for line in _field_report(project):
            _echo(line)


def openalex(
    slug: Annotated[str, typer.Argument(help="Project slug.")],
    opt_in: Annotated[
        bool,
        typer.Option(
            "--opt-in", help="Record a 401/403 as `refused` and seal, instead of failing."
        ),
    ] = False,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Ask OpenAlex again for held identifiers.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print what would be asked. No HTTP call, no write.")
    ] = False,
    root: Annotated[Path | None, typer.Option("--root", "-r", show_default=False)] = None,
) -> None:
    """Capture OpenAlex works for records and candidates with a field still missing.

    Looks up by DOI, or by title and year where there is no DOI, and seals one Layer 0 run.
    Nothing is requested for identifiers a sealed run already holds, unless --refresh.
    Run `prismabib build --rebuild` afterwards to load the run.
    """
    from prismabib.cli import _echo, _reporting_errors

    with _reporting_errors():
        project = Project.open(slug, root=root)
        targets = _targets(project)
        dois, titles = _openalex_inputs(project, targets, refresh)
        if dry_run:
            _echo(f"openalex: {len(dois)} DOIs, {len(titles)} titles planned")
            return
        manifest = capture_openalex(
            project, dois=dois, titles=titles, fallback_opt_in=opt_in, refresh=refresh
        )
        if manifest is None:
            _echo("openalex: nothing to request; no run created")
            return
        _write_back(project, targets)
        outcomes = [o for r in manifest.requests for o in r.outcomes]
        counts = Counter(o.outcome for o in outcomes)
        _echo(
            "identifiers: "
            + ", ".join(f"{n} {counts[n]}" for n in ("value", "not_found", "refused"))
        )
        for f in _FIELDS:
            absent = sum(f in o.absent_fields for o in outcomes)
            _echo(
                f"{f}: value {counts['value'] - absent}, absent {absent}, "
                f"not_found {counts['not_found']}, refused {counts['refused']}"
            )
        _echo(f"run {manifest.run_id} sealed. Run `prismabib build {slug} --rebuild` to load it.")


__all__ = ["cascade", "openalex"]
