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

## Stage 3 extensions (deferred)

The following will be added in Stage 3 (not Stage 1):

- **DuckDB schema**: table structure, column types, primary keys, indexes
- **ER diagram**: relationships between tables (records, authors, affiliations, venues, keywords)
- **View definitions**: derived tables for common queries (records by year, by affiliation, etc.)
- **Citation schema**: if Stage 2's full-text acquisition includes citation metadata

For now, Stage 1 freezes the `Record` and related models. Later stages add tables and views, but do not change the model definitions.

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
