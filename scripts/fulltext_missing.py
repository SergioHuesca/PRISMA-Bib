#!/usr/bin/env -S uv run python
"""List the records still missing full text, as a checklist to fetch by hand.

BUILD_PLAN §Stage 6's resolver chain gets what is legally and technically
reachable without credentials. What it cannot get -- paywalled content, and
open-access hosts that refuse automated clients -- is a fetching exercise for a
reviewer with institutional access, not an engineering problem. This turns the
remainder into a worklist.

Grouped by venue, because a library session works one publisher at a time: one
proxied tab for SPIE, one for SpringerLink, one for IEEE Xplore.

Read-only. Opens Layer 1 read-only, writes nothing, spends no API quota.

Usage:
    uv run scripts/fulltext_missing.py <slug> [--root projects]
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from prismabib.errors import PrismabibError
from prismabib.fulltext.capture import already_resolved_record_ids
from prismabib.fulltext.resolve import MANUAL_DROP_DIRNAME, manual_drop_path
from prismabib.prisma.engine import manual_abstract_set
from prismabib.project import Project
from prismabib.store.db import connect


def main() -> None:
    """Print the checklist."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument("--root", type=Path, default=Path("projects"))
    args = parser.parse_args()

    try:
        project = Project.open(args.slug, root=args.root)
        sought = sorted(manual_abstract_set(project))
    except PrismabibError as error:
        # Same contract the CLI holds itself to
        # (`test_cli__known_error__exits_nonzero_without_a_traceback`): an
        # unknown slug, a missing store and a store predating v0.16 are all
        # ordinary operator situations, and a Python traceback tells a reviewer
        # nothing they can act on.
        print(f"prismabib: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    connection = connect(project, read_only=True)
    try:
        # Layer 0, not Layer 1: `fulltext_assets` only reflects what
        # `build --rebuild` has folded in, so reading it straight after
        # `prismabib fulltext` would list every record just resolved as still
        # missing.
        #
        # `include_unsealed=True` because this answers "what must the reviewer
        # still fetch?", not "what may resumption skip?" -- a different
        # question from the one `run.py` asks of the same function. A
        # budget-bounded run leaves its assets on disk unsealed, and listing
        # those papers would send someone to a library for files they already
        # have, contradicting the command that just reported resolving them.
        #
        # The cost of that choice, stated: an *orphaned* unsealed run (one
        # whose target set no longer matches, so it will never resume or seal)
        # holds assets `build_store` will never fold into Layer 1, and this
        # will report those records as not needed. The opposite default sends
        # a reviewer to a library for files they already hold, which is the
        # commoner harm.
        resolved = already_resolved_record_ids(project.fulltext_dir, include_unsealed=True)
        meta: dict[str, tuple[str, int | None, str, str | None]] = {
            record_id: (title, year, venue, doi)
            for record_id, title, year, venue, doi in connection.execute(
                "SELECT r.record_id, r.title, r.year, COALESCE(v.name, '(no venue)'), r.doi "
                "FROM records r LEFT JOIN venues v ON v.venue_id = r.venue_id"
            ).fetchall()
        }
    finally:
        connection.close()

    missing = [record_id for record_id in sought if record_id not in resolved]
    by_venue: dict[str, list[str]] = defaultdict(list)
    for record_id in missing:
        by_venue[meta[record_id][2]].append(record_id)

    drop_dir = project.fulltext_dir / MANUAL_DROP_DIRNAME
    print(f"# Full text still needed — {project.slug}")
    print()
    print(f"{len(missing)} of {len(sought)} records sought for retrieval have no full text.")
    print(f"Save each PDF into {drop_dir}/ under the exact filename given,")
    print("then run `prismabib fulltext` and `prismabib build --rebuild` again.")
    print()

    for venue in sorted(by_venue, key=lambda name: (-len(by_venue[name]), name)):
        print(f"## {venue}  ({len(by_venue[venue])})")
        print()
        for record_id in by_venue[venue]:
            title, year, _venue, doi = meta[record_id]
            print(f"- [ ] **{title}** ({year})")
            print(f"      {'https://doi.org/' + doi if doi else '(no DOI recorded)'}")
            print(f"      -> {manual_drop_path(project.fulltext_dir, record_id).name}")
        print()


if __name__ == "__main__":
    main()
