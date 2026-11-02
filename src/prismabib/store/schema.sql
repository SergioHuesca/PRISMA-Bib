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

-- ADR 0041 Decision 3 adds the 16 provenance columns after payload_line: per field (abstract,
-- authors, affiliations, keywords) the source, the level (0 when unavailable, else 1-5), the final
-- outcome (`value` or `unavailable`) and the payload reference. The CHECKs are the closed sets of
-- that Decision, tightened so an `unavailable` outcome also needs the `unavailable` source. The
-- DEFAULTs are the honest "nothing known" triple, so an INSERT that names only the original
-- columns still works; the loader always writes all 16 explicitly. The 13 original columns are
-- unchanged. This is a deviation from the frozen BUILD_PLAN clause, recorded in ADR 0041.
CREATE TABLE records (
  record_id TEXT PRIMARY KEY, run_id TEXT, doi TEXT, title TEXT, abstract TEXT,
  year INTEGER, cover_date DATE, doc_type TEXT, language TEXT,
  venue_id TEXT, open_access BOOLEAN, payload_file TEXT, payload_line INTEGER,
  abstract_source TEXT NOT NULL DEFAULT 'unavailable', abstract_level INTEGER NOT NULL DEFAULT 0,
  abstract_outcome TEXT NOT NULL DEFAULT 'unavailable', abstract_payload_ref TEXT,
  authors_source TEXT NOT NULL DEFAULT 'unavailable', authors_level INTEGER NOT NULL DEFAULT 0,
  authors_outcome TEXT NOT NULL DEFAULT 'unavailable', authors_payload_ref TEXT,
  affiliations_source TEXT NOT NULL DEFAULT 'unavailable', affiliations_level INTEGER NOT NULL DEFAULT 0,
  affiliations_outcome TEXT NOT NULL DEFAULT 'unavailable', affiliations_payload_ref TEXT,
  keywords_source TEXT NOT NULL DEFAULT 'unavailable', keywords_level INTEGER NOT NULL DEFAULT 0,
  keywords_outcome TEXT NOT NULL DEFAULT 'unavailable', keywords_payload_ref TEXT,
  CHECK ((abstract_source, abstract_level) IN (('scopus-complete', 1), ('openalex', 4), ('manual', 5), ('unavailable', 0))
    AND ((abstract_outcome = 'unavailable' AND abstract_source = 'unavailable'
           AND abstract_payload_ref IS NULL)
      OR (abstract_outcome = 'value' AND abstract_payload_ref IS NOT NULL
          AND abstract_source <> 'unavailable'))),
  CHECK ((authors_source, authors_level) IN (('scopus-complete', 1), ('openalex', 4), ('manual', 5), ('unavailable', 0))
    AND ((authors_outcome = 'unavailable' AND authors_source = 'unavailable'
           AND authors_payload_ref IS NULL)
      OR (authors_outcome = 'value' AND authors_payload_ref IS NOT NULL
          AND authors_source <> 'unavailable'))),
  CHECK ((affiliations_source, affiliations_level) IN (('scopus-complete', 1), ('scopus-standard', 2), ('scopus-meta', 3), ('openalex', 4), ('manual', 5), ('unavailable', 0))
    AND ((affiliations_outcome = 'unavailable' AND affiliations_source = 'unavailable'
           AND affiliations_payload_ref IS NULL)
      OR (affiliations_outcome = 'value' AND affiliations_payload_ref IS NOT NULL
          AND affiliations_source <> 'unavailable'))),
  CHECK ((keywords_source, keywords_level) IN (('scopus-complete', 1), ('openalex-topic', 4), ('unavailable', 0))
    AND ((keywords_outcome = 'unavailable' AND keywords_source = 'unavailable'
           AND keywords_payload_ref IS NULL)
      OR (keywords_outcome = 'value' AND keywords_payload_ref IS NOT NULL
          AND keywords_source <> 'unavailable')))
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
-- ADR 0030 Decision 3 amends BUILD_PLAN line 871 (author|index): the CHECK adds 'openalex-topic'.
-- Must match RECORD_KEYWORD_KINDS in store/load.py (a test asserts it).
CREATE TABLE record_keywords (
  record_id TEXT, keyword_id TEXT,
  kind TEXT CHECK (kind IN ('author', 'index', 'openalex-topic'))
);

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
-- the two is the corpus bias this stage exists to prevent.
--
-- `entitled` here does NOT mean the same thing as `entitled` in Layer 0's
-- `attempts.jsonl` (ADR 0021 Decision 1b). Layer 0 records the raw fact: this
-- resolver was refused, `false`, always. This column records the *attribution*:
-- `false` only when the refusing resolver could have served the record's own
-- publisher, and `NULL` when it could not (a ScienceDirect 403 on an IEEE paper
-- -- ScienceDirect never held it and IEEE was never asked) or when the publisher
-- cannot be identified at all. The derivation happens in `store/load.py`, which
-- is what lets `build --rebuild` correct runs sealed under the older,
-- unconditional rule without re-fetching anything. `path`/`media_type` are
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

-- Added by ADR 0032, not part of the frozen BUILD_PLAN schema. Active reviewer
-- registrations (ADR 0029), folded from Layer 2 by the loader; every build creates
-- it, empty until the registration fold fills it. A registration has no run and no
-- `venues` row, so its venue type and name live here. `language` is always NULL in
-- this version (ADR 0032 Decision 2).
CREATE TABLE registered_records (
  record_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, doi TEXT NOT NULL,
  title TEXT NOT NULL, abstract TEXT, year INTEGER NOT NULL,
  doc_type TEXT NOT NULL, language TEXT, venue_type TEXT NOT NULL,
  venue_name TEXT NOT NULL, openalex_id TEXT NOT NULL, payload_file TEXT NOT NULL,
  payload_line INTEGER NOT NULL, match_rule TEXT NOT NULL, reviewer TEXT NOT NULL,
  registered_ts TIMESTAMP NOT NULL,
  -- ADR 0041 Decision 3: JSON arrays of text, or NULL when the body lacks the field.
  authors_json TEXT, affiliations_json TEXT, keywords_json TEXT
);

-- Added by ADR 0041 Decision 2, not part of the frozen BUILD_PLAN schema. One row per
-- (record, field) where a Scopus level and OpenAlex both hold a value and the values differ
-- under the fixed comparison rule. Every value column is the canonical JSON text of that side.
CREATE TABLE provenance_conflicts (
  record_id TEXT, field TEXT,
  scopus_level INTEGER CHECK (scopus_level IN (1, 2, 3)),
  scopus_source TEXT,
  scopus_payload_ref TEXT, scopus_value TEXT,
  openalex_payload_ref TEXT, openalex_value TEXT,
  status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
  resolved_side TEXT CHECK (resolved_side IN ('scopus', 'openalex', 'manual')),
  resolution_event_id TEXT,
  PRIMARY KEY (record_id, field),
  CHECK ((scopus_level, scopus_source) IN
    ((1, 'scopus-complete'), (2, 'scopus-standard'), (3, 'scopus-meta'))),
  CHECK ((status = 'open' AND resolved_side IS NULL AND resolution_event_id IS NULL)
    OR (status = 'resolved' AND resolved_side IS NOT NULL AND resolution_event_id IS NOT NULL))
);

-- The one reader of S_raw (ADR 0032 Decision 1): Scopus rows plus registrations.
-- Deliberately no COALESCE on the Scopus half, so a record with no venue keeps NULL
-- `venue_type`/`venue_name` exactly as screening/ui.py read them from the LEFT JOIN.
-- A view is not checksummed.
CREATE VIEW records_all AS
  SELECT r.record_id, 'scopus' AS origin, r.run_id, r.doi, r.title, r.abstract, r.year,
         r.cover_date, r.doc_type, r.language, r.venue_id, r.open_access,
         r.payload_file, r.payload_line,
         v.venue_type, v.name AS venue_name,
         r.abstract_source, r.abstract_level, r.abstract_outcome, r.abstract_payload_ref,
         r.authors_source, r.authors_level, r.authors_outcome, r.authors_payload_ref,
         r.affiliations_source, r.affiliations_level, r.affiliations_outcome,
         r.affiliations_payload_ref,
         r.keywords_source, r.keywords_level, r.keywords_outcome, r.keywords_payload_ref
    FROM records r LEFT JOIN venues v ON v.venue_id = r.venue_id
  UNION ALL
  -- A registered record's provenance is OpenAlex, level 4, from its own body (ADR 0041 Decision 3).
  SELECT record_id, 'registered', NULL, doi, title, abstract, year,
         NULL, doc_type, language, NULL, NULL,
         payload_file, payload_line, venue_type, venue_name,
         CASE WHEN abstract IS NOT NULL THEN 'openalex' ELSE 'unavailable' END,
         CASE WHEN abstract IS NOT NULL THEN 4 ELSE 0 END,
         CASE WHEN abstract IS NOT NULL THEN 'value' ELSE 'unavailable' END,
         CASE WHEN abstract IS NOT NULL THEN payload_file || '#' || openalex_id END,
         CASE WHEN authors_json IS NOT NULL THEN 'openalex' ELSE 'unavailable' END,
         CASE WHEN authors_json IS NOT NULL THEN 4 ELSE 0 END,
         CASE WHEN authors_json IS NOT NULL THEN 'value' ELSE 'unavailable' END,
         CASE WHEN authors_json IS NOT NULL THEN payload_file || '#' || openalex_id END,
         CASE WHEN affiliations_json IS NOT NULL THEN 'openalex' ELSE 'unavailable' END,
         CASE WHEN affiliations_json IS NOT NULL THEN 4 ELSE 0 END,
         CASE WHEN affiliations_json IS NOT NULL THEN 'value' ELSE 'unavailable' END,
         CASE WHEN affiliations_json IS NOT NULL THEN payload_file || '#' || openalex_id END,
         CASE WHEN keywords_json IS NOT NULL THEN 'openalex-topic' ELSE 'unavailable' END,
         CASE WHEN keywords_json IS NOT NULL THEN 4 ELSE 0 END,
         CASE WHEN keywords_json IS NOT NULL THEN 'value' ELSE 'unavailable' END,
         CASE WHEN keywords_json IS NOT NULL THEN payload_file || '#' || openalex_id END
    FROM registered_records;
