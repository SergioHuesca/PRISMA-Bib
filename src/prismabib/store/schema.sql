-- Layer 1 normalised store schema (BUILD_PLAN Stage 3, lines 847-879).
--
-- This file is executed verbatim by prismabib.store.load.build_store to
-- (re)create every table. It is intentionally NOT hand-duplicated as Python
-- CREATE TABLE calls elsewhere, so schema.sql and the live DuckDB catalog
-- can never drift out of sync (see test_schema__sql_file__matches_live_duckdb_introspection).
--
-- Reproduced character-for-character from the BUILD_PLAN -- do not add,
-- rename, or retype a column here without updating the frozen spec first.

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
