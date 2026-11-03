"""Synthetic ``raw/openalex-search/<run_id>/`` fixtures for the Layer 1 loader tests (ADR 0042).

Hand-written on purpose, not produced by the PR 3 writer: the loader tests must not depend on the
capture code, and they need runs the writer would refuse to seal (a work with no title, a manifest
that disagrees with its bytes). Content is synthetic; shapes follow the live probe of 2026-11-03
(``id``, ``doi`` as a ``https://doi.org/`` URL, ``type``, ``cited_by_count``, ``authorships[]`` with
``author``, ``institutions[]``, ``primary_topic``, ``topics``, ``primary_location.source``).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from prismabib.capture.layout import OPENALEX_SEARCH_DIRNAME, RUN_MANIFEST_FILENAME
from prismabib.capture.manifest import OpenAlexSearchRequest, OpenAlexSearchRunManifest
from prismabib.project import Project
from prismabib.sources.openalex import SEARCH_SELECT

#: ASJC 1702 "Artificial Intelligence": a subfield id that the ADR 0032 map holds.
MAPPED_SUBFIELD = "https://openalex.org/subfields/1702"
T0 = datetime(2026, 11, 3, 10, 0, 0, tzinfo=UTC)


def work(n: int, **overrides: Any) -> dict[str, Any]:
    """One synthetic work; a keyword replaces a key, and ``None`` removes it."""
    body: dict[str, Any] = {
        "id": f"https://openalex.org/W{n}",
        "doi": f"https://doi.org/10.1/Item-{n}",
        "title": f"Synthetic title {n}",
        "publication_year": 2020 + n % 3,
        "type": "article",
        "cited_by_count": 10 * n,
        "abstract_inverted_index": {"Synthetic": [0], f"abstract{n}": [1]},
        "authorships": [
            {
                "author": {"id": f"https://openalex.org/A{n}", "display_name": f"Ada Author{n}"},
                "author_position": "first",
                "institutions": [
                    {
                        "id": f"https://openalex.org/I{n}",
                        "display_name": f"Institute {n}",
                        "country_code": "ES",
                    }
                ],
            }
        ],
        "primary_topic": {"id": "https://openalex.org/T1", "subfield": {"id": MAPPED_SUBFIELD}},
        "topics": [{"display_name": f"Topic {n}", "score": 0.9}],
        "primary_location": {"source": {"display_name": f"Journal {n}", "type": "journal"}},
    }
    for key, value in overrides.items():
        if value is None:
            body.pop(key, None)
        else:
            body[key] = value
    return body


def write_search_run(
    project: Project,
    run_id: str,
    query: str,
    pages: list[list[Any]],
    *,
    total: int | None = None,
    sealed: bool = True,
    discarded: bool = False,
    offset_minutes: int = 0,
) -> Path:
    """Write one run under ``raw/openalex-search/``; ``manifest.json`` only when ``sealed``."""
    run_dir = project.raw_dir / OPENALEX_SEARCH_DIRNAME / run_id
    run_dir.mkdir(parents=True)
    count = sum(len(p) for p in pages) if total is None else total
    started = T0 + timedelta(minutes=offset_minutes)
    requests: list[OpenAlexSearchRequest] = []
    for index, results in enumerate(pages):
        name = f"response-{index:04d}.jsonl"
        last = index + 1 == len(pages)
        next_cursor = None if last else f"c{index + 1}"
        line = json.dumps(
            {
                "meta": {"count": count, "per_page": 100, "next_cursor": next_cursor},
                "results": results,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        (run_dir / name).write_bytes(line + b"\n")
        requests.append(
            OpenAlexSearchRequest(
                index=index,
                file=name,
                cursor_sent="*" if index == 0 else f"c{index}",
                sha256=hashlib.sha256(line + b"\n").hexdigest(),
                encoding="verbatim",
                http_status=200,
                retrieved_at=started + timedelta(seconds=index),
                result_count=len(results),
                meta_count=count,
                next_cursor=next_cursor,
            )
        )
    if sealed:
        manifest = OpenAlexSearchRunManifest(
            run_id=run_id,
            started_at=started,
            finished_at=started + timedelta(seconds=len(pages)),
            endpoint="https://api.openalex.org/works",
            client_version="test",
            criteria_version="test",
            query=query,
            query_key=" ".join(query.split()),
            select=SEARCH_SELECT,
            per_page=100,
            total_count=count,
            works_captured=sum(len(p) for p in pages),
            complete=True,
            api_key_used=True,
            requests=requests,
        )
        (run_dir / RUN_MANIFEST_FILENAME).write_text(
            manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    else:
        state = {"query": query, "discarded": discarded}
        (run_dir / "progress.json").write_text(json.dumps(state), encoding="utf-8")
    return run_dir


def openalex_project(tmp_path: Path, mode: str = "openalex") -> Project:
    """A new project in ``mode`` (default ``openalex``), under ``tmp_path / "p"``."""
    return Project.init(
        "demo",
        title="Demo",
        root=tmp_path / "p",
        source_mode=mode,  # type: ignore[arg-type]
        actor="t",
    )
