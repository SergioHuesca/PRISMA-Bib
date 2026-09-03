-- Layer 1 normalised store schema (BUILD_PLAN Stage 3, lines 847-879).
--
-- This file is executed verbatim by prismabib.store.load.build_store to
-- (re)create every table. It is intentionally NOT hand-duplicated as Python
-- CREATE TABLE calls elsewhere, so schema.sql and the live DuckDB catalog
-- can never drift out of sync (see test_schema__sql_file__matches_live_duckdb_introspection).
--
-- Every table down to citation_snapshots is reproduced character-for-character
-- from the BUILD_PLAN -- do not add, rename, or retype a column in one of them
-- without updating the frozen spec first. The two tables below that line
-- (malformed_entries, run_duplicates) are additions, recorded in ADR 0012 and
-- ADR 0013; adding a table there is subject to the same rule, so a further one
-- needs its own ADR.

CREATE TABLE runs (
  run_id TEXT PRIMARY KEY, started_at TIMESTAMP, query TEXT, view TEXT,
  total_results INTEGER, payload_sha256 TEXT, criteria_version TEXT
);

CREATE TABLE records (
  record_id TEXT PRIMARY KEY, run_id TEXT, doi TEXT, title TEXT, abstract TEXT,
  year INTEGER, cover_date DATE, doc_type TEXT, language TEXT,
  venue_id TEXT, open_access BOOLEAN, payload_file TEXT, payload_line INTEGER
);

CREATE TABLE venues (
  venue_id TEXT PRIMARY KEY, name TEXT, issn TEXT, eissn TEXT,
  venue_type TEXT, abbreviation TEXT
);

CREATE TABLE authors (author_id TEXT PRIMARY KEY, surname TEXT, given_name TEXT);
CREATE TABLE record_authors (record_id TEXT, author_id TEXT, position INTEGER);

CREATE TABLE affiliations (afid TEXT PRIMARY KEY, name TEXT, city TEXT, country_iso3 TEXT);
CREATE TABLE record_affiliations (record_id TEXT, afid TEXT);

CREATE TABLE keywords (keyword_id TEXT PRIMARY KEY, term_raw TEXT, term_norm TEXT);
CREATE TABLE record_keywords (record_id TEXT, keyword_id TEXT, kind TEXT);  -- author|index

CREATE TABLE subject_areas (record_id TEXT, area_code TEXT);

CREATE TABLE citation_snapshots (
  record_id TEXT, retrieved_at TIMESTAMP, cited_by_count INTEGER,
  PRIMARY KEY (record_id, retrieved_at)
);

-- Added by ADR 0012, not part of the frozen BUILD_PLAN schema. One row per
-- Layer 0 entry that could not be turned into a record, so "which entries were
-- skipped" is a query against Layer 1 rather than an in-memory tally that only
-- the rebuilding call ever sees (BUILD_PLAN 2.2: Layer 1 is derived from
-- Layer 0, and a skipped entry is a fact about Layer 0).
--
-- `reason` is a short, closed-vocabulary code, never the exception message:
-- those messages embed an absolute path, and an absolute path inside a
-- checksummed table would make S03-AC1's byte-stable checksums depend on where
-- the repository is checked out. The full message is logged instead.
CREATE TABLE malformed_entries (
  run_id TEXT, payload_file TEXT, payload_line INTEGER, record_id TEXT, reason TEXT,
  PRIMARY KEY (payload_file, payload_line)
);

-- Added by ADR 0013, not part of the frozen BUILD_PLAN schema. One row per
-- sealed run that re-found at least one paper an earlier search had already
-- contributed (runs with none get no row), counting the papers that run re-found which an *earlier run
-- under a different query* had already loaded -- PRISMA 2020's "duplicates
-- removed before screening". The different-query condition is load-bearing: a
-- refresh of the same search re-finding its own papers is not a duplicate,
-- because `identified` already counts each distinct query exactly once.
--
-- Recorded during the load because that is the only moment it is observable:
-- `records.run_id` keeps the first run that loaded a record, so afterwards
-- Layer 1 cannot say how many runs saw it. Deriving it instead as
-- `identified - |S_raw| - removed_other_reasons` would make the flow diagram's
-- first equation close by construction, absorbing a manifest that disagrees
-- with its own corpus -- the defect that equation exists to catch.
--
-- A table rather than a `runs` column, for the reason ADR 0012 gives: adding a
-- table is a smaller deviation from the frozen schema than altering one.
CREATE TABLE run_duplicates (
  run_id TEXT PRIMARY KEY, duplicates INTEGER
);

-- Added by ADR 0018, not part of the frozen BUILD_PLAN schema. `subject_areas`
-- alone cannot distinguish "Scopus assigned no subject areas" from "this record
-- was never enriched" -- both are zero rows there -- and a filter that *removes*
-- records on subject-area data must be able to tell the two apart. `abstract_runs`
-- mirrors `runs` for provenance (never a row for identification, per ADR 0011:
-- an abstract run identifies no record); `record_subject_area_coverage` records,
-- per record per run, which of Layer 0's three unavailability reasons applied, or
-- that Scopus assigned areas -- a record with no row at all is the fourth state,
-- "never asked". See ADR 0018 for the full rationale.
CREATE TABLE abstract_runs (
  run_id TEXT PRIMARY KEY,
  started_at TIMESTAMP, finished_at TIMESTAMP,
  endpoint TEXT, view TEXT,
  records_requested INTEGER, records_fetched INTEGER,
  payload_sha256 TEXT, client_version TEXT, criteria_version TEXT
);

CREATE TABLE record_subject_area_coverage (
  record_id TEXT, run_id TEXT, status TEXT,
  PRIMARY KEY (record_id, run_id)
);

-- Added by ADR 0019, not part of the frozen BUILD_PLAN schema. `fulltext_assets`
-- holds one row per resolver *attempt*, not per asset: a ScienceDirect 403 yields
-- no asset yet must still be recorded, because `entitled = false` (an entitlement
-- gap) is exactly what the Stage 6 coverage table needs to tell apart from
-- `entitled IS NULL` (no open-access copy, no manual drop, HTTP 404) -- conflating
-- the two is the corpus bias this stage exists to prevent. `path`/`media_type` are
-- NULL when an attempt yielded no asset; a resolver is never re-attempted for a
-- record that already has a *resolved* attempt in a sealed Layer 0 run --
-- resumption reads `fulltext/runs/<run_id>/attempts.jsonl`, not this table,
-- because this table is rebuilt from those runs (ADR 0019 Decision 0). A
-- record whose only rows are refusals is deliberately re-attempted: a fresh
-- token or a newly dropped PDF can change the answer.
-- `path` is **run-relative** (`<run_id>/assets/<digest>.pdf`), never absolute:
-- this table is checksummed, and an absolute path would make the digest a
-- function of where the project sits on disk -- the same reason
-- `malformed_entries.payload_file` is relative. Resolve it against
-- `project.fulltext_dir / 'runs'`.
CREATE TABLE fulltext_assets (
  record_id TEXT, resolver_name TEXT, media_type TEXT, path TEXT,
  retrieved_at TIMESTAMP, entitled BOOLEAN,
  PRIMARY KEY (record_id, resolver_name)
);

-- Added by ADR 0019, not part of the frozen BUILD_PLAN schema. `position` preserves
-- document order -- section names alone cannot say "methods" came before "results"
-- -- and `low_confidence` is per section rather than per document because
-- confidence varies *within* one document: a PDF whose body carries a text
-- layer and whose scanned appendix does not should flag the appendix alone.
-- (Not because one record can carry sections from two source documents -- it
-- cannot: first-hit-wins gives a record one resolved asset, and the primary key
-- would collide anyway. ADR 0019 retracted that reason.) It is set when
-- `pdfplumber` finds no text layer for a PDF section. No OCR is attempted: the flag
-- exists so a human reads that section instead.
CREATE TABLE fulltext_sections (
  record_id TEXT, position INTEGER, section_name TEXT, text TEXT,
  low_confidence BOOLEAN,
  PRIMARY KEY (record_id, position)
);
