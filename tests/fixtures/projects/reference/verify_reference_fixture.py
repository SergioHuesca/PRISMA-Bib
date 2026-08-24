#!/usr/bin/env python3
"""Verification script for the reference fixture project.

Not a pytest test module (this directory's contents are fixtures, not test
code the default suite collects) -- a standalone script run by hand, and by
the test-automation agent that built this fixture, to print the evidence
README.md's edge-case table is built from: record count, per-page line
counts, the computed ``payload_sha256`` against ``manifest.json``, the exact
(file, entry index) of each of the eight required edge cases, and a
cassette-derivation check (no real title/ORCID/authid/DOI from
``tests/fixtures/cassettes/`` appears in this fixture).

Usage::

    uv run python tests/fixtures/projects/reference/verify_reference_fixture.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_ROOT.parents[3]
CASSETTES_DIR = REPO_ROOT / "tests" / "fixtures" / "cassettes"


def _run_dir() -> Path:
    raw_dir = FIXTURE_ROOT / "raw"
    candidates = [p for p in raw_dir.iterdir() if p.is_dir()]
    if len(candidates) != 1:
        raise SystemExit(f"expected exactly one run directory under {raw_dir}, found {candidates}")
    return candidates[0]


def _load_pages(run_dir: Path, manifest: dict) -> list[dict]:
    pages = []
    for filename in manifest["payload_files"]:
        path = run_dir / filename
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1, (
            f"{path} has {len(lines)} lines, expected exactly 1 (one page per file)"
        )
        pages.append(json.loads(lines[0]))
    return pages


def main() -> None:
    run_dir = _run_dir()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    pages = _load_pages(run_dir, manifest)

    all_entries: list[tuple[str, int, dict]] = []
    for filename, page in zip(manifest["payload_files"], pages, strict=True):
        entries = page["search-results"]["entry"]
        for index, entry in enumerate(entries):
            all_entries.append((filename, index, entry))

    print(f"record count: {len(all_entries)}")
    print(f"manifest.total_results: {manifest['total_results']}")
    assert len(all_entries) == manifest["total_results"] == 120

    print("\nper-page entry counts:")
    for filename, page in zip(manifest["payload_files"], pages, strict=True):
        print(f"  {filename}: {len(page['search-results']['entry'])} entries")

    # payload_sha256 -----------------------------------------------------
    digest = hashlib.sha256()
    for filename in manifest["payload_files"]:
        digest.update((run_dir / filename).read_bytes())
    computed = digest.hexdigest()
    print(f"\ncomputed payload_sha256: {computed}")
    print(f"manifest payload_sha256: {manifest['payload_sha256']}")
    assert computed == manifest["payload_sha256"]

    # distinct dc:identifier ---------------------------------------------
    identifiers = [entry["dc:identifier"] for _, _, entry in all_entries]
    assert len(identifiers) == len(set(identifiers)), "dc:identifier values are not all distinct"
    assert all(i.startswith("SCOPUS_ID:") for i in identifiers)
    print("\nall dc:identifier values distinct and SCOPUS_ID:-prefixed: OK")

    # eight edge cases -----------------------------------------------------
    def find(predicate, label):
        matches = [(f, i, e) for f, i, e in all_entries if predicate(e)]
        assert matches, f"no record found for edge case: {label}"
        return matches

    print("\nedge cases:")

    no_abstract = find(lambda e: "dc:description" not in e, "no abstract")
    print(f"  no abstract: {[(f, i, e['dc:identifier']) for f, i, e in no_abstract]}")

    scalar_afid = find(
        lambda e: any(isinstance(a["afid"], str) for a in e["affiliation"]), "scalar afid"
    )
    print(f"  scalar afid: {[(f, i, e['dc:identifier']) for f, i, e in scalar_afid]}")

    non_english = find(lambda e: e.get("language") != "English", "non-English")
    print(
        f"  non-English: {[(f, i, e['dc:identifier'], e['language']) for f, i, e in non_english]}"
    )

    many_authors = find(lambda e: len(e.get("author", [])) >= 40, "40+ authors")
    print(
        f"  40+ authors: {[(f, i, e['dc:identifier'], len(e['author'])) for f, i, e in many_authors]}"
    )

    zero_keywords = find(lambda e: "authkeywords" not in e, "zero keywords")
    print(f"  zero keywords: {[(f, i, e['dc:identifier']) for f, i, e in zero_keywords]}")

    dupe_doi_map: dict[str, list] = {}
    for f, i, e in all_entries:
        doi = e.get("prism:doi")
        if doi:
            dupe_doi_map.setdefault(doi, []).append((f, i, e["dc:identifier"]))
    duplicates = {doi: locs for doi, locs in dupe_doi_map.items() if len(locs) > 1}
    assert duplicates, "no duplicate DOI found"
    print(f"  duplicate DOI: {duplicates}")

    partial_year = find(lambda e: e["prism:coverDate"].startswith("2026"), "2026 partial-year")
    print(
        f"  2026 partial-year: {[(f, i, e['dc:identifier'], e['prism:coverDate']) for f, i, e in partial_year]}"
    )

    known_countries = {inst[3] for inst in _known_countries()}
    unmapped_country = find(
        lambda e: any(a["affiliation-country"] not in known_countries for a in e["affiliation"]),
        "unmapped country string",
    )
    print(
        "  unmapped country: "
        f"{[(f, i, e['dc:identifier'], [a['affiliation-country'] for a in e['affiliation']]) for f, i, e in unmapped_country]}"
    )

    # cassette-derivation check --------------------------------------------
    print("\ncassette-derivation check:")
    _check_no_cassette_leakage(all_entries)


def _known_countries():
    import sys

    sys.path.insert(0, str(FIXTURE_ROOT))
    from generate_reference_fixture import _INSTITUTIONS

    return _INSTITUTIONS


def _check_no_cassette_leakage(all_entries: list[tuple[str, int, dict]]) -> None:
    cassette_titles: set[str] = set()
    cassette_dois: set[str] = set()
    cassette_orcids: set[str] = set()
    cassette_authids: set[str] = set()

    for cassette_path in CASSETTES_DIR.glob("*.json"):
        try:
            data = json.loads(cassette_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        entries = data.get("search-results", {}).get("entry", [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                if "dc:title" in entry:
                    cassette_titles.add(entry["dc:title"])
                if "prism:doi" in entry:
                    cassette_dois.add(entry["prism:doi"])
                for author in entry.get("author") or []:
                    if isinstance(author, dict):
                        if "orcid" in author:
                            cassette_orcids.add(author["orcid"])
                        if "authid" in author:
                            cassette_authids.add(author["authid"])

    fixture_titles = {e["dc:title"] for _, _, e in all_entries}
    fixture_dois = {e["prism:doi"] for _, _, e in all_entries if "prism:doi" in e}
    fixture_orcids = {a["orcid"] for _, _, e in all_entries for a in e.get("author", [])}
    fixture_authids = {a["authid"] for _, _, e in all_entries for a in e.get("author", [])}

    title_overlap = fixture_titles & cassette_titles
    doi_overlap = fixture_dois & cassette_dois
    orcid_overlap = fixture_orcids & cassette_orcids
    authid_overlap = fixture_authids & cassette_authids

    assert not title_overlap, f"titles copied from cassette: {title_overlap}"
    assert not doi_overlap, f"DOIs copied from cassette: {doi_overlap}"
    assert not orcid_overlap, f"ORCIDs copied from cassette: {orcid_overlap}"
    assert not authid_overlap, f"authids copied from cassette: {authid_overlap}"

    print(f"  cassette titles checked: {len(cassette_titles)}, overlap: {len(title_overlap)}")
    print(f"  cassette DOIs checked: {len(cassette_dois)}, overlap: {len(doi_overlap)}")
    print(f"  cassette ORCIDs checked: {len(cassette_orcids)}, overlap: {len(orcid_overlap)}")
    print(f"  cassette authids checked: {len(cassette_authids)}, overlap: {len(authid_overlap)}")
    print("  no cassette-derived content found: OK")


if __name__ == "__main__":
    main()
