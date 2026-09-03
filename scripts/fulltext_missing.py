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
from collections import defaultdict
from pathlib import Path

from prismabib.fulltext.resolve import manual_drop_path
from prismabib.prisma.engine import manual_abstract_set
from prismabib.project import Project
from prismabib.store.db import connect


def main() -> None:
    """Print the checklist."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument("--root", type=Path, default=Path("projects"))
    args = parser.parse_args()

    project = Project.open(args.slug, root=args.root)
    sought = sorted(manual_abstract_set(project))

    connection = connect(project, read_only=True)
    try:
        resolved = {
            record_id
            for (record_id,) in connection.execute(
                "SELECT DISTINCT record_id FROM fulltext_assets WHERE media_type IS NOT NULL"
            ).fetchall()
        }
        meta = {
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

    drop_dir = manual_drop_path(project.fulltext_dir, "x").parent
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
