# Data Model

The data model defines the core entities flowing through the system: records, authors, affiliations, venues, and identifiers. This page covers the **Stage 1 domain model**, frozen after Stage 1 and extended only additively in later stages.

## Overview

The pipeline transforms raw Scopus data into a normalised internal representation, then into versioned decision events (Layer 2), and finally into analysis views (Layer 3).

```
Raw Scopus JSON
       ↓
  [Pydantic validation]
       ↓
  Normalised Record (BUILD_PLAN §Stage 1, lines 660–697)
       ↓
  Layer 1 DuckDB store
       ↓
  Layer 2 Events (screening, taxonomy)
       ↓
  Layer 3 Analysis (PRISMA flow, figures, exports)
```

This page documents the **normalised Record** and its constituent entities, the deduplication key, identifier schemes, and country normalisation.

## Entities

All entities are Pydantic v2 models, defined in `src/prismabib/models.py`.

### Record

A `Record` is the primary unit of analysis: a bibliographic item retrieved from Scopus.

```python
class Record(BaseModel):
    record_id: str  # Canonical primary key: "scopus:2-s2.0-..."
    doi: str | None  # Secondary key (optional)
    title: str  # Required; never empty
    abstract: str | None  # Optional; Scopus omits for some doc types
    year: int  # Publication year (required)
    cover_date: date | None  # Precise publication date if available
    doc_type: str  # Scopus document type code: "ar", "cp", "re", ...
    language: str | None  # Language code as received (wire format; normalisation TBD Stage 2)
    venue: Venue  # Journal, conference, book, etc.
    authors: list[Author]  # List of contributing authors (may be empty)
    affiliations: list[Affiliation]  # List of institutional affiliations (may be empty)
    author_keywords: list[str]  # Keywords supplied by authors
    index_keywords: list[str]  # Index/controlled keywords (e.g. Scopus Subject Terms)
    subject_areas: list[
        str
    ]  # Scopus subject codes (wire format; e.g. "COMP", "ENGI" per criteria.yaml)
    open_access: bool | None  # Open access status (if known)
    source_payload_ref: PayloadRef  # Provenance: pointer to Layer 0 raw JSON line
```

### Author

An `Author` is a person who contributed to a record.

```python
class Author(BaseModel):
    author_id: str | None  # Scopus author ID (if assigned)
    surname: str  # Family name (required)
    given_name: str | None  # Given name(s) (optional; Scopus sometimes omits)
    initials: str | None  # Author initials (optional)
```

**Notes:**
- `author_id` may be `None` if Scopus did not assign an author ID (rare, but happens)
- Author name disambiguation is deferred (BUILD_PLAN §8); Stage 1 uses Scopus IDs as-is
- Non-ASCII names are preserved without transliteration

### Affiliation

An `Affiliation` is an institutional affiliation associated with one or more authors.

```python
class Affiliation(BaseModel):
    afid: str | None  # Scopus affiliation ID
    name: str  # Institution name (required)
    city: str | None  # City (optional)
    country: str | None  # ISO 3166-1 alpha-3 code (normalised; see below)
```

**Scopus quirks handled:**
- **Scalar vs. list `afid`**: Scopus emits `afid` as a bare string when there is one affiliation, and as an array otherwise. A Pydantic validator coerces this to a single scalar: if `afid` is a list, the first element is kept as canonical.
- **Country normalisation**: free-text country strings are normalised to ISO 3166-1 alpha-3 codes (see below).

### Venue

A `Venue` is the publication outlet (journal, conference, book, etc.).

```python
class Venue(BaseModel):
    name: str  # Venue name (required)
    issn: str | None  # ISSN (for journals; optional)
    eissn: str | None  # Electronic ISSN (for e-journals; optional)
    venue_type: Literal["journal", "conference", "book", "other"]
    abbreviation: str | None  # Short name (e.g., "CVPR", "Nature"; optional)
```

## Identifiers and Deduplication

### Primary key: Scopus record ID

Every `Record` has a canonical primary key:

```
scopus:2-s2.0-85123456789
```

This is Scopus's internal identifier, prefixed with `scopus:` to allow future multi-source deduplication (OpenAlex, Web of Science, etc.). The `record_id` field is required and unique within a corpus.

**Format**: `scopus:2-s2.0-<numeric-id>`

### Secondary keys: DOI and dedup fallback

Deduplication is within a single source in v1.0. Multi-source deduplication is deferred (BUILD_PLAN §8 line 1559, out of v1.0). The dedup key supports future multi-source work by namespacing record IDs and using three candidate keys:

1. **DOI (highest confidence)**: when present, the normalised DOI is the deduplication key
   - Normalised form: lowercase, URL wrappers stripped (see `normalise_doi()` below)
   - Example: `10.1109/cvpr.2023.01234`

2. **Fallback key (when no DOI)**: `(normalised_title, first_author_surname, year)`
   - Used for records without a DOI
   - Normalised title: casefolded, whitespace runs collapsed to single space
   - First author surname: casefolded, normalised the same way
   - Year: integer, as-is

### Deduplication key computation

The `dedup_key()` function in `models.py` computes the deduplication key:

```python
def dedup_key(record: Record) -> DedupKey:
    """
    Returns:
        ("doi", normalised_doi) when the record has a DOI
        ("fallback", normalised_title, first_author_surname, year) otherwise

    Raises:
        ValueError: if the record has neither a DOI nor any authors
    """
```

**Example:**

```python
from prismabib.models import dedup_key, Record

# Record with DOI
record1 = Record(..., doi="https://doi.org/10.1234/example", ...)
assert dedup_key(record1) == ("doi", "10.1234/example")

# Record without DOI, with authors
record2 = Record(
    title="Deep Learning for Video Analysis",
    authors=[Author(surname="Smith", ...)],
    year=2023,
    doi=None,
    ...
)
assert dedup_key(record2) == ("fallback", "deep learning for video analysis", "smith", 2023)
```

The dedup key is used to identify duplicates when acquiring from multiple sources. Records with the same key are treated as the same bibliographic item (even if they have different Scopus IDs).

### DOI normalisation

The `normalise_doi()` function removes common URL wrappers and normalises case:

```python
def normalise_doi(doi: str) -> str:
    """
    Normalises a DOI to lowercase, bare form (10.xxxx/...).

    Handles:
    - https://doi.org/10.1234/example → 10.1234/example
    - http://dx.doi.org/10.1234/example → 10.1234/example
    - doi:10.1234/example → 10.1234/example
    - 10.1234/Example (mixed case) → 10.1234/example

    Args:
        doi: A DOI as received from a source (with or without URL/scheme)

    Returns:
        The bare, lowercase DOI (e.g., "10.1234/example")
    """
```

## Country Normalisation

Affiliation country strings are free-text in Scopus and need normalisation to enable geographic analysis. All country names are normalised to **ISO 3166-1 alpha-3 codes** (three-letter codes: "USA", "GBR", "CHN", etc.).

The normalisation is performed by `normalise_country()` in `src/prismabib/countries.py`. This function uses a **checked-in mapping table** (no third-party dependency, per BUILD_PLAN §2.4's closed stack).

### Mapping table

The table in `countries.py` includes:

- **ISO short names** (official English names): "United States" → "USA", "Korea, Republic of" → "KOR"
- **Common aliases** as seen in Scopus data: "South Korea" → "KOR", "U.S.A." → "USA", "Viet Nam" → "VNM"
- **Identity mapping** for alpha-3 codes: "USA" → "USA" (re-normalising an already-normalised country is a no-op, never flagged as unmapped)

### Unmapped countries are preserved

A core principle (BUILD_PLAN §5 risk 8): unmapped strings are **never dropped or blanked**. Instead:

- The original string is returned unchanged
- A structured log warning is issued: `warning("affiliation.country.unmapped", raw="Unmapped Country")`

**Example:**

```python
from prismabib.countries import normalise_country

# Mapped: returns the ISO code
code, matched = normalise_country("South Korea")
assert code == "KOR"
assert matched is True

# Unmapped: returns the original string
code, matched = normalise_country("Wakanda")
assert code == "Wakanda"  # Original string preserved
assert matched is False  # Caller can log the miss

# Already normalised: re-normalising is a no-op, not flagged
code, matched = normalise_country("USA")
assert code == "USA"
assert matched is True  # Not re-flagged as "unmapped"
```

The unmapped-miss logging happens in `Affiliation._normalise_country()` (the Pydantic field validator) and is surfaced via structlog. This allows downstream analysis to detect coverage gaps: if many affiliations map to unmapped countries, geography analysis is understating coverage.

### Common Scopus country variants

The mapping table explicitly handles variants found in Scopus data:

| Input | Mapped to |
| --- | --- |
| `"Korea, Republic of"` | `"KOR"` |
| `"South Korea"` | `"KOR"` |
| `"Korea, Democratic People's Republic of"` | `"PRK"` |
| `"North Korea"` | `"PRK"` |
| `"Viet Nam"` | `"VNM"` |
| `"Vietnam"` | `"VNM"` |
| `"United States"` | `"USA"` |
| `"United States of America"` | `"USA"` |
| `"U.S.A."` | `"USA"` |
| `"U.S."` | `"USA"` |

## Provenance: PayloadRef

Every `Record` includes a `PayloadRef`, a pointer back to the originating raw JSON line in Layer 0:

```python
class PayloadRef(BaseModel):
    path: Path  # Path to the Layer 0 JSONL file
    line: (
        int  # 0-based line index (same as enumerate() convention; Stage 3 loader must not re-index)
    )

    def resolve(self) -> str:
        """Read the originating raw JSON line from the file.

        Returns:
            The raw text of the line, with trailing newline stripped.

        Raises:
            FileNotFoundError: If the file does not exist
            IndexError: If the line index exceeds the file's line count
        """
```

**Why this matters:**

- **Auditability**: any number derived from a record traces back to the exact raw Scopus JSON that produced it
- **Reproducibility**: future versions of the system can reload Layer 1 from Layer 0 and verify that the same records are produced
- **Error diagnosis**: if a record looks suspicious, the raw JSON can be inspected directly

**Example:**

```python
from prismabib.models import Record, PayloadRef
from pathlib import Path

record = Record(
    record_id="scopus:2-s2.0-85123456789",
    title="Example Paper",
    ...
    source_payload_ref=PayloadRef(
        path=Path("projects/example/raw/2026-01-15_page_01.jsonl"),
        line=42  # 0-based; this is the 43rd line in the file
    )
)

# Recover the originating raw JSON
raw_json_line = record.source_payload_ref.resolve()
# Returns: '{"dc:identifier":"2-s2.0-85123456789","dc:title":"Example Paper",...}'
```

## Scopus API quirks handled

### Affiliation scalar vs. list

Scopus's HTTP response emits `affiliation` (singular) as a bare object when a record has one affiliation:

```json
{
  "affiliation": {"afid": "60123456", "name": "University of Example"}
}
```

But as an array when there are multiple:

```json
{
  "affiliation": [
    {"afid": "60123456", "name": "University of Example"},
    {"afid": "60234567", "name": "Research Institute"}
  ]
}
```

The `Record._coerce_affiliations_scalar()` validator handles this: a single affiliation is automatically wrapped in a list, so `record.affiliations` is always `list[Affiliation]`.

### Affiliation afid scalar vs. list

Similarly, within an affiliation, the `afid` field is sometimes a string and sometimes a list. The `Affiliation._coerce_afid_scalar()` validator keeps the first ID as canonical.

### Missing authkeywords

Some Scopus views (e.g., STANDARD) omit the `authkeywords` field. The Stage 2 acquisition code must use the COMPLETE view to include author keywords. If `authkeywords` is missing from a raw payload, the record's `author_keywords` will be an empty list (validated as optional).

## Round-trip serialisation

A `Record` must survive a complete serialisation round-trip:

```python
import json
from prismabib.models import Record

# Original record with optional fields omitted
record1 = Record(
    record_id="scopus:1",
    title="Paper",
    year=2023,
    doi=None,
    abstract=None,
    language=None,
    authors=[],
    affiliations=[],
    ...
    source_payload_ref=PayloadRef(path=Path("raw/data.jsonl"), line=0)
)

# Serialise to JSON and back
json_str = record1.model_dump_json()
record2 = Record.model_validate_json(json_str)

# Must be identical
assert record1 == record2
```

This is tested in Stage 1's acceptance criteria (BUILD_PLAN §Stage 1 line 726): "Round-trip: Record → model_dump_json() → model_validate_json() is lossless for a synthetic fixture with missing optional fields, list-vs-scalar affiliations, and non-ASCII author names."

## Stage 3 — The Normalised Store (Layer 1)

Layer 1 is a single DuckDB file (`project.db_path`, typically `store/corpus.duckdb`) derived from Layer 0's sealed run directories. It contains 11 tables, frozen in the BUILD_PLAN schema (lines 847–879) and never hand-edited. This section describes the ER model, the rationale for key design choices, and the frozen read-facing API.

### Layer 1 is reconstructible and disposable

**Key invariant (BUILD_PLAN §2.2, line 105):** Layer 1 must be reconstructible from Layer 0 by running one function—`build_store(project, rebuild=True)`. If it is not:

- Layer 1 remains **fully disposable**: delete `corpus.duckdb` at any time, run `build_store` again, lose nothing.
- The store is **byte-stable**: rebuilding identical Layer 0 input produces byte-identical table checksums, so reproducibility can be verified independently.
- The schema is **future-proof**: if Stage 2 or later needs to modify Layer 1, the only safe path is to drop Layer 0 and rebuild from upstream sources—never hand-edit the database.

### Entity-Relationship description

The 11 tables are:

#### Core entities

**`runs`** — one row per sealed Layer 0 acquisition run.
- **Primary key:** `run_id` (a timestamp + hash, sortable, oldest first)
- **Columns:**
  - `started_at`: TIMESTAMP when the Scopus API query executed
  - `query`: TEXT the exact Boolean query string sent to Scopus
  - `view`: TEXT the entitlement level ("COMPLETE"; see BUILD_PLAN §5 risk 1)
  - `total_results`: INTEGER the Scopus API's own `opensearch:totalResults` from the first page
  - `payload_sha256`: TEXT SHA-256 of the concatenated page files, for Layer 0 integrity
  - `criteria_version`: TEXT version of the screening rules this run was captured under
- **Role:** Seals the identity and timestamp of every query result, linking every record back to one specific moment in time. Used to compute `retrieved_at` for citation snapshots (see below).

**`records`** — one row per distinct bibliographic item (by Scopus `record_id`).
- **Primary key:** `record_id` (e.g. `"scopus:2-s2.0-85123456789"`)
- **Columns:**
  - `run_id`: TEXT foreign key to `runs` (the first run this record appeared in)
  - `doi`: TEXT | NULL normalised DOI when the record carries one (see Domain Model, DOI normalisation)
  - `title`, `abstract`: TEXT | NULL
  - `year`, `cover_date`: the publication year and exact date (from Scopus's `prism:coverDate`)
  - `doc_type`: TEXT e.g. "Journal Article", "Conference Paper" (from `subtypeDescription`, or fallback to `subtype`)
  - `language`: TEXT | NULL author-supplied language code (wire format; normalisation deferred)
  - `venue_id`: TEXT foreign key to `venues`
  - `open_access`: BOOLEAN | NULL
  - `payload_file`: TEXT relative path within `project.raw_dir` to the Layer 0 JSONL file (e.g. `"<run_id>/page-0000.jsonl"`)
  - `payload_line`: INTEGER 0-based line index into that file (Stage 3's correction to the old scheme that always stored 0 here)
- **Role:** The central fact table. Every analysis begins here. `payload_file`/`payload_line` together form a provenance pointer back to the exact raw JSON line.
- **Deduplication:** When the same Scopus paper is captured by more than one run, **only the first run's row is retained**. Later runs contribute citation snapshots (see below) but no new record rows. This is the "first-seen wins" principle, deterministic per the fixed traversal order of runs by `run_id`.
- **That collapse is counted, not silent.** Two search strings over one register overlap, and every entry that resolves to an already-loaded `record_id` is a record identified twice and stored once. `FlowCounts.duplicates_across_searches` reports it on PRISMA's "duplicate records removed" line, which is what lets the flow diagram's first consistency equation close on a multi-search project ([ADR 0013](adr/0013-identified-sums-across-searches.md)). It is *not* the normalised-DOI report: two **distinct** records sharing a DOI are both kept and both screened.

**`venues`** — one row per distinct publication outlet.
- **Primary key:** `venue_id` (either `"scopus-source:<source-id>"` if Scopus provided one, or `"venue-hash:<sha1>"` for fallback matching)
- **Columns:** `name`, `issn`, `eissn` (from `prism:issn` and `prism:eIssn`), `venue_type` ("journal", "conference", "book", or "other"), `abbreviation` (NULL from Search API)
- **Role:** Metadata about journals, conferences, etc. Search API pages never supply venue abbreviations; that would come from a full-text or Abstract Retrieval API integration in a later stage.

#### Author-related entities

**`authors`** — one row per distinct Scopus author ID.
- **Primary key:** `author_id` (Scopus's internal identifier, e.g. `"7101234567"`)
- **Columns:** `surname`, `given_name` (both required; Scopus sometimes omits given names, stored as NULL)
- **Role:** Author name register. **Author disambiguation is out of scope** (BUILD_PLAN modelling note 4, line 886): Scopus author IDs are used as-is. A person who published under different names or an author ID collision are not resolved. This is a Stage 2+ decision, deferred to v2.0 (see [limitations](../methodology/limitations.md)).
- **Notes:** Records with no author list (rare but possible) still appear in `records`, just with no rows in `record_authors`.

**`record_authors`** — junction table linking records to authors, preserving position.
- **Primary key:** implicit (no PK defined; deduplication is implicit via `record_id`/`author_id`/`position` uniqueness in the Stage 3 loader)
- **Columns:** `record_id`, `author_id`, `position` (1-indexed, first author is position 1)
- **Role:** Preserves author order and allows queries like "papers by this author" or "author co-authorship networks".

#### Affiliation-related entities

**`affiliations`** — one row per distinct Scopus affiliation ID.
- **Primary key:** `afid` (Scopus's internal affiliation ID, e.g. `"60123456"`)
- **Columns:**
  - `name`: TEXT institution name (e.g. "University of Cambridge")
  - `city`: TEXT | NULL
  - `country_iso3`: TEXT | NULL ISO-3166-1 alpha-3 code, normalised from free-text
- **Role:** Institutional directory. Enables geographic analysis.
- **Country normalisation (BUILD_PLAN modelling note 2):** Free-text country strings from Scopus are normalised to ISO-3166 alpha-3 codes via a checked-in mapping table (`prismabib.countries`). Unmapped strings (e.g. "Wakanda") are **never discarded**—they are stored in `country_iso3` unchanged, a warning is logged, and the geography total still equals the record count (risk 8). This allows downstream analysis to detect coverage gaps ("how many affiliations were unmapped?") rather than silently undercounting.

**`record_affiliations`** — junction table linking records to affiliations.
- **Primary key:** implicit
- **Columns:** `record_id`, `afid`
- **Role:** M:N relationship; a record can have multiple affiliations, an affiliation multiple records.

#### Keyword-related entities

**`keywords`** — one row per distinct normalised keyword term.
- **Primary key:** `keyword_id` (deterministic hash of the normalised term, e.g. `"kw:abc123def456"`; see `_keyword_id()` in `store/load.py`)
- **Columns:**
  - `term_raw`: TEXT the first-seen raw (pre-normalisation) form, e.g. `"Convolutional Neural Networks"`
  - `term_norm`: TEXT normalised form (casefolded, punctuation→spaces, whitespace collapsed, singularised), e.g. `"convolutional neural network"`
- **Role:** Keyword dictionary. Normalisation reduces duplicates (e.g. "CNN" and "convolutional neural networks" map to the same underlying concept if both normalise to the same term), but **the raw form is never discarded** (BUILD_PLAN modelling note 3, line 885). A researcher can always trace back to how keywords appeared in the original records.
- **Singularisation:** A small, closed list (currently `convolutional neural networks` → `convolutional neural network`, etc.); no general stemming, which would mangle non-plural words ending in "s".
- **Index keywords always empty in this stage:** The Scopus Search API `view=COMPLETE` does not return indexed keywords (Scopus "Subject Terms")—those come only from the Abstract Retrieval API. The `keywords` table and `record_keywords` rows with `kind="index"` are schema-present but data-empty today; both are real tables, not modelling gaps (see the module docstring of `store/load.py`).

**`record_keywords`** — junction table linking records to keywords, tagged by kind.
- **Primary key:** implicit
- **Columns:** `record_id`, `keyword_id`, `kind` ("author" or "index", per the capture source)
- **Role:** Tracks which keywords appear in which records and whether they came from the authors or the index. Enables keyword-based analysis and filtering.

#### Subject areas and citations

**`subject_areas`** — Scopus subject classification codes for records.
- **Primary key:** implicit
- **Columns:** `record_id`, `area_code` (e.g. "COMP" for computer science, per `criteria.yaml`)
- **Role:** Supports subject-based filtering. Always empty for Search API `view=COMPLETE` captures (Abstract Retrieval API required); schema-present for forward compatibility.

**`malformed_entries`** — one row per Layer 0 entry that could not be turned into a record. **Not part of the frozen BUILD_PLAN schema**; added by [ADR 0012](adr/0012-persisting-skipped-layer0-entries.md).
- **Primary key:** `(payload_file, payload_line)` composite key
- **Columns:**
  - `run_id`: TEXT the sealed run the entry came from
  - `payload_file`, `payload_line`: TEXT / INTEGER the entry's location, in the same run-relative form as `records.payload_file`
  - `record_id`: TEXT | NULL the record id, or NULL when the entry had no usable `eid`
  - `reason`: TEXT a closed-vocabulary code — `"missing_eid"` or `"invalid_field"`
- **Role:** Makes "which entries were skipped?" a query against Layer 1 rather than a value only the call that rebuilt the store ever saw. The default `prismabib build` path reuses an existing store and does no loading, so an in-memory tally there is empty — which reads as "nothing was skipped".
- **Entries, not records:** A row here does not imply a lost record. A re-capture of a paper an earlier run already loaded can be skipped while the record stays in `records` (and its citation snapshot is kept, since the count does not depend on the field that failed).
- **Why `reason` is a code, not the error message:** The message embeds an absolute path. A checksummed table containing one would make S03-AC1's byte-stable checksums depend on where the repository is checked out. The message is logged instead.

**`run_duplicates`** — one row per sealed run, counting the entries in it that were papers a run under a *different* query had already loaded. **Not part of the frozen BUILD_PLAN schema**; added by [ADR 0013](adr/0013-identified-sums-across-searches.md).
- **Primary key:** `run_id`
- **Columns:**
  - `run_id`: TEXT the sealed run whose entries were counted
  - `duplicates`: INTEGER how many of that run's entries resolved to a `record_id` an earlier, *differently queried* run had already loaded
- **Role:** PRISMA 2020's "duplicate records removed" line. `FlowCounts.duplicates_across_searches` is `SUM(duplicates)` over this table, which is what lets the flow diagram's first consistency equation close on a review that ran more than one search string.
- **Why it must be written during the load:** it cannot be recomputed afterwards. `records.run_id` keeps only the *first* run that loaded a record, so once the load is over Layer 1 no longer knows how many runs a record appeared in. Only the loader is ever in a position to count this.
- **Why not derive it as a remainder** (`identified - |S_raw| - removed_other_reasons`): that would make equation 1 an identity that cannot fail, silently absorbing a run manifest that disagrees with the corpus it produced — the one defect the consistency guard exists to catch. Measured independently, the equation can still disagree, and a disagreement then means something real.
- **A refresh is not a duplicate.** An entry is counted only when the record was first loaded under a different `runs.query`. Re-running the *same* query to refresh citation counts contributes one `total_results` term to `identified` in total (see ADR 0013), so counting its re-found records would subtract them a second time and break equation 1 in the other direction.
- **Rows are inserted in sorted `run_id` order**, so the table's checksum does not depend on which run happened to be walked first.

**`citation_snapshots`** — point-in-time citation counts, one row per (record, retrieval timestamp) pair.
- **Primary key:** `(record_id, retrieved_at)` composite key
- **Columns:** `cited_by_count` INTEGER the Scopus citation count at that moment
- **Why a snapshot table, never a mutable column on `records` (BUILD_PLAN modelling note 1, line 883):**
  - Citation counts change over time; a single `cited_by_count` column on `records` would capture only the count from the first run, forever.
  - Different records may be retrieved at different times (if runs are re-done).
  - Every citation figure must carry the timestamp it was true as of—the only way to achieve reproducibility is to store every (record, timestamp, count) triple.
  - This table is populated only when a run's entries carry a parseable `citedby-count`; a record with no snapshots simply has no rows here.
- **Retrieval timestamp (BUILD_PLAN loader docstring, lines 68–81):** `retrieved_at` is set to the run's `RunManifest.started_at` (when the Scopus API query began), **never `datetime.now()` at load time**. This is the only choice consistent with byte-stable rebuilds and idempotence: if the load-time wall clock were used, every `build_store(rebuild=True)` call would write a different key and duplicate rows. The snapshot date must describe when the data was retrieved, not when someone rebuilt a derived store.

### The read-facing API

All queries from downstream analysis stages go through the `Corpus` class (BUILD_PLAN line 893) in `prismabib.store.load`, never raw SQL.

```python
def build_store(project: Project, *, rebuild: bool = False) -> StoreStats:
    """(Re)build project's Layer 1 store from Layer 0.

    Args:
        project: The project to build.
        rebuild: When False (default) and store exists, reuse it (idempotent).
                When True, delete and rebuild from Layer 0 (byte-stable).

    Returns:
        StoreStats with counts: runs_loaded, records_loaded, authors_loaded,
        affiliations_loaded, venues_loaded, keywords_loaded,
        record_keyword_links_loaded, subject_area_links_loaded,
        citation_snapshots_loaded, unmapped_country_values (the country
        strings currently stored that did not map to ISO codes), and
        malformed_entries_skipped (one reference per Layer 0 *entry* that
        could not be turned into a record). Every field is read back from
        the store, on the rebuild and the reuse path alike.

    Raises:
        StoreError: If store exists, rebuild=False, and store is corrupted;
            or if so many Layer 0 entries could not be parsed that the
            capture itself is unusable, in which case no store is written.
    """
```

A Layer 0 entry that cannot be turned into a record no longer aborts the load. It is skipped, written to `malformed_entries`, and reported — see [ADR 0012](adr/0012-persisting-skipped-layer0-entries.md) for why one bad entry must not make the other 1,944 unloadable, and why the report is a table rather than a tally the rebuilding call keeps to itself.

```python
def connect(project: Project, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """Open project's Layer 1 store as a raw DuckDB connection.

    Callers should not use this directly; use Corpus instead.
    """
```

```python
class Corpus:
    """Read-facing handle onto a Layer 1 store."""

    @classmethod
    def open(cls, project: Project, *, read_only: bool = True) -> Corpus:
        """Open project's Layer 1 store as a Corpus."""

    def records(self, stage: PrismaStage = PrismaStage.INCLUDED) -> pl.DataFrame:
        """Return records for one named PRISMA set.

        Only PrismaStage.RAW (every captured record, unfiltered) is answerable
        from Layer 1 alone. Every other stage raises NotImplementedError,
        naming the missing Stage 4 PRISMA engine.

        Returns:
            DataFrame with every column of records table, one row per record,
            ordered by record_id (when stage is RAW).
        """

    def keywords(
        self, kind: str = "author", stage: PrismaStage = PrismaStage.INCLUDED
    ) -> pl.DataFrame:
        """Return keyword occurrences of one kind, for one named PRISMA set.

        Args:
            kind: "author" or "index".
            stage: (See records() -- same restriction applies.)

        Returns:
            DataFrame: record_id, keyword_id, term_raw, term_norm, kind;
            one row per (record, keyword) occurrence; ordered by
            record_id, then term_norm.
        """

    def citations(self, at: datetime | None = None) -> pl.DataFrame:
        """Return one citation-count row per record, as of a point in time.

        Args:
            at: When None (default), latest snapshot for each record is used.
                When given, most recent snapshot with retrieved_at <= at.

        Returns:
            DataFrame: record_id, retrieved_at, cited_by_count; one row per
            record with a qualifying snapshot; ordered by record_id.
        """
```

### PrismaStage before Stage 4 exists

`Corpus.records()`, `Corpus.keywords()` and the Stage 5 `screening_queue()` contract all take a `PrismaStage` parameter. This enum lives in its own top-level module `src/prismabib/stage.py` (see the "Additions to BUILD_PLAN §2.3" section below) because:

- **Conceptually:** `PrismaStage` is a Stage 4 concept (the PRISMA engine is its sole producer).
- **Mechanically:** Stage 3's frozen `Corpus` contract already needs it as a parameter type, and BUILD_PLAN §0 rule 1 forbids Stage 3 from importing `prisma/` (not yet built).
- **Solution:** A leaf module with zero dependencies that both `prismabib.store` (Stage 3) and `prismabib.prisma` (Stage 4) can import downward.

The enum members are:

| Member | Value | Meaning |
| --- | --- | --- |
| `RAW` | `"raw"` | Every record captured from the query (unfiltered) |
| `AUTOMATED` | `"automated"` | RAW filtered by year, subject area, and document type per `criteria.yaml` (deterministic) |
| `LANGUAGE` | `"language"` | AUTOMATED further filtered by language (deterministic) |
| `TITLE_ABSTRACT` | `"title_abstract"` | LANGUAGE folded through the decision log at title/abstract screening |
| `FULLTEXT` | `"fulltext"` | TITLE_ABSTRACT folded through the decision log at full-text screening |
| `INCLUDED` | `"included"` | Final corpus = FULLTEXT |

**Critical caveat:** Only `PrismaStage.RAW` is answerable from Layer 1 alone. Every other member raises `NotImplementedError` naming the missing Stage 4 engine. This is intentional and honest—calling `.records(stage=PrismaStage.INCLUDED)` will fail loudly rather than silently returning wrong counts. This is tested with `xfail(strict=True)` until Stage 4 lands, then flipped in the same PR.

## Testing and validation

The `tests/factories.py` module exports polyfactory `ModelFactory` subclasses (BUILD_PLAN §Stage 1 line 746): `AuthorFactory`, `AffiliationFactory`, `VenueFactory`, `PayloadRefFactory`, `RecordFactory`. Each is a bare `ModelFactory[Model]` subclass; polyfactory introspects the model's fields and generates realistic values.

Hypothesis property tests use these factories' `.as_strategy()` method to generate test data:

```python
from hypothesis import given
from tests.factories import RecordFactory


@given(RecordFactory.as_strategy())
def test_dedup_key_is_deterministic(record):
    key1 = dedup_key(record)
    key2 = dedup_key(record)
    assert key1 == key2
```

The round-trip serialisation property test (BUILD_PLAN line 726) generates records covering: `abstract=None`, empty keyword lists, non-ASCII author names, 40+ authors, year bounds.

## References

- BUILD_PLAN §Stage 1 (lines 660–697): model specification
- BUILD_PLAN §3.2 (lines 370–374): identifier scheme and dedup key
- BUILD_PLAN §5 (risk 8): unmapped country preservation (§3.3 is the error taxonomy, not country handling)
- `src/prismabib/models.py`: Pydantic model definitions
- `src/prismabib/countries.py`: country normalisation table and `normalise_country()` function
- `tests/factories.py`: synthetic record factories

## See also

- **ADR 0001** (DuckDB as Analytical Store): how records flow into Layer 1
- **Provenance** (architecture/provenance.md): how any number traces back to raw JSON
- **Getting Started** (docs/getting-started.md): tutorial on querying the store
