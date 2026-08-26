#!/usr/bin/env python3
"""A complete PRISMA review, start to finish, with no Scopus key required.

Run this before pointing prismabib at your own topic. It shows what "done"
looks like -- a search corpus, automated eligibility filtering, human screening
decisions with reason codes, and the PRISMA 2020 flow counts that come out the
other end -- so that when you run your own review you already know the shape of
the thing.

    uv run python examples/worked_example.py

It costs nothing and touches no network. The Layer 0 archive it reads is the
synthetic 120-record corpus committed for prismabib's own test suite: real
Scopus payloads are licensed and can never be redistributed, so a runnable
example has to use manufactured data. The records are invented; the pipeline
that processes them is exactly the one your review will use.

What this example deliberately does NOT show: `prismabib search`, because that
needs your own Scopus credentials and spends your quota. Everything after the
search is identical either way.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from prismabib.prisma.flow import compute_flow_counts
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.load import build_store

#: The committed synthetic Layer 0 archive. Not a public API -- it lives under
#: `tests/` because its first job is pinning prismabib's own golden numbers.
_SYNTHETIC_CORPUS = Path(__file__).resolve().parent.parent / "tests/fixtures/projects/reference"

#: A deliberately generic protocol. Compare it with the one `prismabib init`
#: writes for you: same shape, filled in. The reason codes are the ones this
#: imaginary review distinguishes -- yours should be the ones yours does.
_CRITERIA = """\
version: 1.0.0
temporal:
  year_start: 2020
  year_end: 2026
subject_areas: []
doc_types:
  include: [ar]
  conference_whitelist: []
languages: [English]
manual_abstract:
  exclude_reason_codes:
    - OFF_TOPIC
    - REVIEW_OR_SURVEY
    - NOT_PRIMARY_RESEARCH
manual_fulltext:
  exclude_reason_codes:
    - NO_FULL_TEXT
    - WRONG_STUDY_DESIGN
"""


def _rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="prismabib-example-") as scratch:
        root = Path(scratch) / "example-review"
        shutil.copytree(_SYNTHETIC_CORPUS, root)
        (root / "criteria.yaml").write_text(_CRITERIA, encoding="utf-8")
        project = Project(slug="example-review", root=root)

        _rule("1. Build Layer 1 from the sealed Layer 0 archive")
        stats = build_store(project, rebuild=True)
        print(f"   {stats.records_loaded} records loaded into DuckDB.")
        print("   Layer 1 is derived, never authored: delete it and rebuild, and you")
        print("   get the same store back. That is why the raw archive is immutable.")

        _rule("2. Automated eligibility -- no human judgement involved")
        # These come from criteria.yaml alone. No decision you log can widen them,
        # which is what makes them reproducible from the archive by anyone.
        from prismabib.prisma import engine

        raw = engine.raw_set(project)
        automated = engine.automated_set(project)
        language = engine.language_set(project)
        print(f"   identified            {len(raw):4d}")
        print(f"   after year/doc-type   {len(automated):4d}   (-{len(raw) - len(automated)})")
        print(f"   after language        {len(language):4d}   (-{len(automated) - len(language)})")

        _rule("3. Human screening -- every decision is an event, appended and checksummed")
        log = DecisionLog(project)
        queue = sorted(language)
        plan: list[tuple[str, str, str | None]] = []
        for position, record_id in enumerate(queue[:40]):
            if position % 4 == 0:
                plan.append((record_id, "include", None))
            elif position % 4 == 1:
                plan.append((record_id, "exclude", "OFF_TOPIC"))
            elif position % 4 == 2:
                plan.append((record_id, "exclude", "REVIEW_OR_SURVEY"))
            else:
                plan.append((record_id, "unsure", None))
        for record_id, decision, reason in plan:
            log.append(
                stage=PrismaStage.TITLE_ABSTRACT,
                record_id=record_id,
                reviewer="demo-reviewer",
                decision=decision,
                reason_code=reason,
            )
        print(f"   {len(plan)} title/abstract decisions logged by 'demo-reviewer'.")
        print("   'unsure' never resolves to inclusion -- it keeps the record in the")
        print("   queue and is reported separately, so nothing is quietly dropped.")

        advanced = sorted(engine.manual_abstract_set(project))
        for position, record_id in enumerate(advanced):
            log.append(
                stage=PrismaStage.FULLTEXT,
                record_id=record_id,
                reviewer="demo-reviewer",
                decision="include" if position % 3 else "exclude",
                reason_code=None if position % 3 else "NO_FULL_TEXT",
            )
        print(f"   {len(advanced)} full-text decisions logged.")

        _rule("4. The PRISMA 2020 flow counts")
        counts = compute_flow_counts(project)
        print(f"   identified                     {counts.identified:4d}")
        print(f"   excluded, automated            {counts.excluded_automated:4d}")
        print(f"   excluded, language             {counts.excluded_language:4d}")
        print(f"   excluded at title/abstract     {counts.excluded_title_abstract:4d}")
        print(f"   unsure at title/abstract       {counts.unsure_title_abstract:4d}")
        print(f"   sought for full text           {counts.retrieved_fulltext:4d}")
        for reason, n in sorted(counts.excluded_fulltext.items()):
            print(f"   excluded, full text: {reason:<10} {n:4d}")
        print(f"   unsure at full text            {counts.unsure_fulltext:4d}")
        print(f"   INCLUDED                       {counts.included:4d}")

        counts.assert_consistent()
        print("\n   assert_consistent() passed: every stage's arithmetic closes.")
        print("   This is the guard against the failure this project exists to")
        print("   prevent -- a plausible wrong number in a published paper.")

        _rule("5. Where the numbers came from")
        print(f"   decision log   {len(log.load())} events, append-only, sha256 sidecar")
        print("   Nothing above was typed by hand. Every count is a query against the")
        print("   store, so refreshing the corpus updates the diagram instead of")
        print("   silently disagreeing with it.")
        print('\nNext: `prismabib init my-review --title "..."` and edit the two files')
        print("it names. See docs/getting-started.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
