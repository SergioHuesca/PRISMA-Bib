"""OpenAlex classification map for registered records (ADR 0032, Decision 3).

A registered record (ADR 0029) has no Scopus payload, so its ``doc_type``,
``venue_type`` and subject area are derived from the stored OpenAlex work body
through three fixed tables, in the style of :mod:`prismabib.asjc`:

* (a) ``SUBFIELD_TO_ASJC``: OpenAlex subfield id to a four-digit ASJC code,
  252 rows (239 ``exact``, 13 ``qualifier``). ``primary_topic`` only.
* (b) ``TYPE_TO_DOC_TYPE``: OpenAlex work ``type`` to the engine's
  ``doc_type`` description form; 25 values, 8 mapped, 17 refused (``None``).
* (c) ``SOURCE_TYPE_TO_VENUE_TYPE``: OpenAlex source ``type`` to the closed
  ``venue_type`` set; 7 rows, all judgement calls, tightenable.

Anything a table does not cover is a refusal (:class:`ClassificationRefusal`),
never a guess. Language is deliberately not an input (ADR 0032, Decision 2).

The rows below were transcribed programmatically from the ADR tables, and
``tests/unit/test_openalex_map.py`` re-parses the ADR to prove they still agree.
Any change to any table bumps ``MAP_VERSION`` and re-pins ``MAP_SHA256``.

Canonical serialization of the digest (ADR 0032 leaves the shape of ``tables``
open; this module fixes it)::

    tables = {
        "SUBFIELD_TO_ASJC": {"<id>": {"asjc": "<code>", "asjc_name": <str|null>,
                                       "exact": <bool>, "name": "<OpenAlex name>"}},
        "TYPE_TO_DOC_TYPE": {"<type>": "<doc_type>" | null},
        "SOURCE_TYPE_TO_VENUE_TYPE": {"<type>": "<venue_type>"},
    }
    MAP_SHA256 = sha256(json.dumps(tables, sort_keys=True,
                        separators=(",", ":"), ensure_ascii=False)
                        .encode("utf-8")).hexdigest()

``asjc_name`` is the ASJC spelling for ``qualifier`` rows and null for ``exact``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Final, NamedTuple

from prismabib.errors import PrismabibError

MAP_VERSION: Final = "1"
MAP_SHA256: Final = "801149ebb3c2b6114a54034909f4719d6ab179ba0eceaa53b0f4fa89a03d20f1"


class ClassificationRefusal(PrismabibError):
    """An OpenAlex value is not covered by the classification map."""


class SubjectArea(NamedTuple):
    """One ASJC code, with whether the mapping is exact or approximate."""

    code: str
    exact: bool
    asjc_name: str | None  # the ASJC spelling, set only for ``qualifier`` rows


_SUBFIELD_ROWS: Final[tuple[tuple[str, str, str, bool, str | None], ...]] = (
    ("3106", "Nuclear and High Energy Physics", "3106", True, None),
    ("1110", "Plant Science", "1110", True, None),
    ("3312", "Sociology and Political Science", "3312", True, None),
    ("3320", "Political Science and International Relations", "3320", True, None),
    ("3304", "Education", "3304", True, None),
    ("1202", "History", "1202", True, None),
    ("1312", "Molecular Biology", "1312", True, None),
    ("1208", "Literature and Literary Theory", "1208", True, None),
    ("2208", "Electrical and Electronic Engineering", "2208", True, None),
    ("2202", "Aerospace Engineering", "2202", True, None),
    ("2746", "Surgery", "2746", True, None),
    ("2002", "Economics and Econometrics", "2002", True, None),
    ("2210", "Mechanical Engineering", "2210", True, None),
    ("1311", "Genetics", "1311", True, None),
    ("1203", "Language and Linguistics", "1203", True, None),
    ("3308", "Law", "3308", True, None),
    ("2739", "Public Health, Environmental and Occupational Health", "2739", True, None),
    ("1710", "Information Systems", "1710", True, None),
    ("1702", "Artificial Intelligence", "1702", True, None),
    ("2505", "Materials Chemistry", "2505", True, None),
    ("1105", "Ecology, Evolution, Behavior and Systematics", "1105", True, None),
    ("2303", "Ecology", "2303", True, None),
    ("2713", "Epidemiology", "2713", True, None),
    ("1213", "Visual Arts and Performing Arts", "1213", True, None),
    ("2740", "Pulmonary and Respiratory Medicine", "2740", True, None),
    ("2306", "Global and Planetary Change", "2306", True, None),
    ("1211", "Philosophy", "1211", True, None),
    ("3103", "Astronomy and Astrophysics", "3103", True, None),
    ("2204", "Biomedical Engineering", "2204", True, None),
    ("2205", "Civil and Structural Engineering", "2205", True, None),
    ("3316", "Cultural Studies", "3316", True, None),
    ("1408", "Strategy and Management", "1408", True, None),
    ("1204", "Archeology", "1204", False, "Archeology (arts and humanities)"),
    ("1605", "Organic Chemistry", "1605", True, None),
    ("3600", "General Health Professions", "3600", True, None),
    ("3314", "Anthropology", "3314", True, None),
    ("3107", "Atomic and Molecular Physics, and Optics", "3107", True, None),
    ("1402", "Accounting", "1402", True, None),
    ("3203", "Clinical Psychology", "3203", True, None),
    ("3207", "Social Psychology", "3207", True, None),
    ("1212", "Religious studies", "1212", False, "Religious Studies"),
    ("1210", "Music", "1210", True, None),
    ("2705", "Cardiology and Cardiovascular Medicine", "2705", True, None),
    ("1705", "Computer Networks and Communications", "1705", True, None),
    ("2737", "Physiology", "2737", False, "Physiology (medical)"),
    ("2207", "Control and Systems Engineering", "2207", True, None),
    ("1207", "History and Philosophy of Science", "1207", True, None),
    ("1106", "Food Science", "1106", True, None),
    ("1707", "Computer Vision and Pattern Recognition", "1707", True, None),
    ("1209", "Museology", "1209", True, None),
    ("2730", "Oncology", "2730", True, None),
    ("2741", "Radiology, Nuclear Medicine and Imaging", "2741", True, None),
    ("2725", "Infectious Diseases", "2725", True, None),
    ("2805", "Cognitive Neuroscience", "2805", True, None),
    ("3204", "Developmental and Educational Psychology", "3204", True, None),
    ("3205", "Experimental and Cognitive Psychology", "3205", True, None),
    ("1910", "Oceanography", "1910", True, None),
    ("1604", "Inorganic Chemistry", "1604", True, None),
    ("2211", "Mechanics of Materials", "2211", True, None),
    ("2206", "Computational Mechanics", "2206", True, None),
    ("1902", "Atmospheric Science", "1902", True, None),
    ("3311", "Safety Research", "3311", True, None),
    ("2215", "Building and Construction", "2215", True, None),
    ("2738", "Psychiatry and Mental health", "2738", False, "Psychiatry and Mental Health"),
    ("2003", "Finance", "2003", True, None),
    ("2308", "Management, Monitoring, Policy and Law", "2308", True, None),
    ("1908", "Geophysics", "1908", True, None),
    ("1407", "Organizational Behavior and Human Resource Management", "1407", True, None),
    ("1102", "Agronomy and Crop Science", "1102", True, None),
    ("1803", "Management Science and Operations Research", "1803", True, None),
    ("1111", "Soil Science", "1111", True, None),
    ("3305", "Geography, Planning and Development", "3305", True, None),
    ("2302", "Ecological Modeling", "2302", True, None),
    ("2712", "Endocrinology, Diabetes and Metabolism", "2712", True, None),
    ("2736", "Pharmacology", "2736", False, "Pharmacology (medical)"),
    ("3317", "Demography", "3317", True, None),
    ("2312", "Water Science and Technology", "2312", True, None),
    ("3315", "Communication", "3315", True, None),
    ("1406", "Marketing", "1406", True, None),
    ("2403", "Immunology", "2403", True, None),
    ("2734", "Pathology and Forensic Medicine", "2734", True, None),
    ("2000", "General Economics, Econometrics and Finance", "2000", True, None),
    ("3318", "Gender Studies", "3318", True, None),
    ("2105", "Renewable Energy, Sustainability and the Environment", "2105", True, None),
    ("1205", "Classics", "1205", True, None),
    ("1404", "Management Information Systems", "1404", True, None),
    ("1109", "Insect Science", "1109", True, None),
    ("1206", "Conservation", "1206", True, None),
    ("2604", "Applied Mathematics", "2604", True, None),
    ("1703", "Computational Theory and Mathematics", "1703", True, None),
    ("2212", "Ocean Engineering", "2212", True, None),
    ("2216", "Architecture", "2216", True, None),
    ("1911", "Paleontology", "1911", True, None),
    ("2728", "Neurology", "2728", False, "Neurology (clinical)"),
    ("2504", "Electronic, Optical and Magnetic Materials", "2504", True, None),
    ("1100", "General Agricultural and Biological Sciences", "1100", True, None),
    ("2307", "Health, Toxicology and Mutagenesis", "2307", True, None),
    ("2745", "Rheumatology", "2745", True, None),
    ("1802", "Information Systems and Management", "1802", True, None),
    ("2613", "Statistics and Probability", "2613", True, None),
    ("2209", "Industrial and Manufacturing Engineering", "2209", True, None),
    ("2720", "Hematology", "2720", True, None),
    ("3309", "Library and Information Sciences", "3309", True, None),
    ("2213", "Safety, Risk, Reliability and Quality", "2213", True, None),
    ("1307", "Cell Biology", "1307", True, None),
    ("2305", "Environmental Engineering", "2305", True, None),
    ("1607", "Spectroscopy", "1607", True, None),
    ("2916", "Nutrition and Dietetics", "2916", True, None),
    ("2735", "Pediatrics, Perinatology and Child Health", "2735", True, None),
    ("2309", "Nature and Landscape Conservation", "2309", True, None),
    ("2743", "Reproductive Medicine", "2743", True, None),
    ("2804", "Cellular and Molecular Neuroscience", "2804", True, None),
    ("3322", "Urban Studies", "3322", True, None),
    ("1306", "Cancer Research", "1306", True, None),
    ("2608", "Geometry and Topology", "2608", True, None),
    ("1405", "Management of Technology and Innovation", "1405", True, None),
    ("2310", "Pollution", "2310", True, None),
    ("2507", "Polymers and Plastics", "2507", True, None),
    (
        "2707",
        "Complementary and alternative medicine",
        "2707",
        False,
        "Complementary and Alternative Medicine",
    ),
    ("2729", "Obstetrics and Gynecology", "2729", True, None),
    ("1904", "Earth-Surface Processes", "1904", True, None),
    ("2731", "Ophthalmology", "2731", True, None),
    ("3109", "Statistical and Nonlinear Physics", "3109", True, None),
    ("2214", "Media Technology", "2214", True, None),
    ("3313", "Transportation", "3313", True, None),
    ("3104", "Condensed Matter Physics", "3104", True, None),
    ("3306", "Health", "3306", False, "Health (social science)"),
    ("3321", "Public Administration", "3321", True, None),
    ("2610", "Mathematical Physics", "2610", True, None),
    ("2727", "Nephrology", "2727", True, None),
    ("1602", "Analytical Chemistry", "1602", True, None),
    ("2203", "Automotive Engineering", "2203", True, None),
    ("2732", "Orthopedics and Sports Medicine", "2732", True, None),
    ("3310", "Linguistics and Language", "3310", True, None),
    ("3605", "Health Information Management", "3605", True, None),
    ("1103", "Animal Science and Zoology", "1103", True, None),
    ("2502", "Biomaterials", "2502", True, None),
    ("1104", "Aquatic Science", "1104", True, None),
    ("3004", "Pharmacology", "3004", True, None),
    ("3612", "Physical Therapy, Sports Therapy and Rehabilitation", "3612", True, None),
    ("1906", "Geochemistry and Petrology", "1906", True, None),
    ("3609", "Occupational Therapy", "3609", True, None),
    ("1907", "Geology", "1907", True, None),
    ("1804", "Statistics, Probability and Uncertainty", "1804", True, None),
    ("2721", "Hepatology", "2721", True, None),
    ("3404", "Small Animals", "3404", True, None),
    ("2708", "Dermatology", "2708", True, None),
    ("2711", "Emergency Medicine", "2711", True, None),
    ("2716", "Genetics", "2716", False, "Genetics (clinical)"),
    ("1711", "Signal Processing", "1711", True, None),
    ("2304", "Environmental Chemistry", "2304", True, None),
    ("2311", "Waste Management and Disposal", "2311", True, None),
    ("3108", "Radiation", "3108", True, None),
    ("3505", "Orthodontics", "3505", True, None),
    ("1708", "Hardware and Architecture", "1708", True, None),
    ("2405", "Parasitology", "2405", True, None),
    ("3506", "Periodontics", "3506", True, None),
    ("1606", "Physical and Theoretical Chemistry", "1606", True, None),
    ("1709", "Human-Computer Interaction", "1709", True, None),
    ("2748", "Urology", "2748", True, None),
    ("3604", "Emergency Medical Services", "3604", True, None),
    ("2715", "Gastroenterology", "2715", True, None),
    ("1706", "Computer Science Applications", "1706", True, None),
    ("3504", "Oral Surgery", "3504", True, None),
    ("1712", "Software", "1712", True, None),
    ("2703", "Anesthesiology and Pain Medicine", "2703", True, None),
    ("2808", "Neurology", "2808", True, None),
    ("2404", "Microbiology", "2404", True, None),
    ("1912", "Space and Planetary Science", "1912", True, None),
    ("1507", "Fluid Flow and Transfer Processes", "1507", True, None),
    ("2742", "Rehabilitation", "2742", True, None),
    ("3616", "Speech and Hearing", "3616", True, None),
    ("3307", "Human Factors and Ergonomics", "3307", True, None),
    ("2733", "Otorhinolaryngology", "2733", True, None),
    ("3005", "Toxicology", "3005", True, None),
    ("3303", "Development", "3303", True, None),
    ("1304", "Biophysics", "1304", True, None),
    ("3003", "Pharmaceutical Science", "3003", True, None),
    ("3202", "Applied Psychology", "3202", True, None),
    ("1704", "Computer Graphics and Computer-Aided Design", "1704", True, None),
    ("3300", "General Social Sciences", "3300", True, None),
    ("1305", "Biotechnology", "1305", True, None),
    ("1310", "Endocrinology", "1310", True, None),
    ("2611", "Modeling and Simulation", "2611", True, None),
    ("2503", "Ceramics and Composites", "2503", True, None),
    ("2704", "Biochemistry", "2704", False, "Biochemistry (medical)"),
    ("1313", "Molecular Medicine", "1313", True, None),
    ("1200", "General Arts and Humanities", "1200", True, None),
    ("2602", "Algebra and Number Theory", "2602", True, None),
    ("3105", "Instrumentation", "3105", True, None),
    ("2723", "Immunology and Allergy", "2723", True, None),
    ("2922", "Research and Theory", "2922", True, None),
    ("3611", "Pharmacy", "3611", True, None),
    ("2508", "Surfaces, Coatings and Films", "2508", True, None),
    ("2807", "Endocrine and Autonomic Systems", "2807", True, None),
    ("3614", "Radiological and Ultrasound Technology", "3614", True, None),
    ("2809", "Sensory Systems", "2809", True, None),
    ("2706", "Critical Care and Intensive Care Medicine", "2706", True, None),
    ("2607", "Discrete Mathematics and Combinatorics", "2607", True, None),
    ("2406", "Virology", "2406", True, None),
    ("1107", "Forestry", "1107", True, None),
    ("2614", "Theoretical Computer Science", "2614", True, None),
    ("1503", "Catalysis", "1503", True, None),
    ("1303", "Biochemistry", "1303", True, None),
    ("1308", "Clinical Biochemistry", "1308", True, None),
    ("2612", "Numerical Analysis", "2612", True, None),
    ("2717", "Geriatrics and Gerontology", "2717", True, None),
    ("1800", "General Decision Sciences", "1800", True, None),
    ("2724", "Internal Medicine", "2724", True, None),
    ("2910", "Issues, ethics and legal aspects", "2910", False, "Issues, Ethics and Legal Aspects"),
    ("1314", "Physiology", "1314", True, None),
    ("2747", "Transplantation", "2747", True, None),
    ("3603", "Complementary and Manual Therapy", "3603", True, None),
    ("2100", "General Energy", "2100", True, None),
    ("2702", "Anatomy", "2702", True, None),
    ("2102", "Energy Engineering and Power Technology", "2102", True, None),
    ("3200", "General Psychology", "3200", True, None),
    ("1409", "Tourism, Leisure and Hospitality Management", "1409", True, None),
    ("3206", "Neuropsychology and Physiological Psychology", "3206", True, None),
    ("3402", "Equine", "3402", True, None),
    ("2714", "Family Practice", "2714", True, None),
    ("3302", "Archeology", "3302", True, None),
    ("2718", "Health Informatics", "2718", True, None),
    ("2806", "Developmental Neuroscience", "2806", True, None),
    ("1603", "Electrochemistry", "1603", True, None),
    ("2402", "Applied Microbiology and Biotechnology", "2402", True, None),
    ("2500", "General Materials Science", "2500", True, None),
    ("1315", "Structural Biology", "1315", True, None),
    ("2506", "Metals and Alloys", "2506", True, None),
    ("2802", "Behavioral Neuroscience", "2802", True, None),
    ("1309", "Developmental Biology", "1309", True, None),
    ("1504", "Chemical Health and Safety", "1504", True, None),
    ("1410", "Industrial relations", "1410", False, "Industrial Relations"),
    ("3319", "Life-span and Life-course Studies", "3319", True, None),
    ("1502", "Bioengineering", "1502", True, None),
    ("1403", "Business and International Management", "1403", True, None),
    ("1508", "Process Chemistry and Technology", "1508", True, None),
    ("1302", "Aging", "1302", True, None),
    ("2103", "Fuel Technology", "2103", True, None),
    ("2726", "Microbiology", "2726", False, "Microbiology (medical)"),
    ("1108", "Horticulture", "1108", True, None),
    ("2911", "Leadership and Management", "2911", True, None),
    ("3607", "Medical Laboratory Technology", "3607", True, None),
    ("1506", "Filtration and Separation", "1506", True, None),
    ("2803", "Biological Psychiatry", "2803", True, None),
    ("3102", "Acoustics and Ultrasonics", "3102", True, None),
    ("2605", "Computational Mathematics", "2605", True, None),
    ("2200", "General Engineering", "2200", True, None),
    ("3500", "General Dentistry", "3500", True, None),
    ("3608", "Medical Terminology", "3608", True, None),
    ("2104", "Nuclear Energy and Engineering", "2104", True, None),
    ("3002", "Drug Discovery", "3002", True, None),
)

_TYPE_ROWS: Final[tuple[tuple[str, str | None], ...]] = (
    ("article", "article"),
    ("review", "review"),
    ("editorial", "editorial"),
    ("letter", "letter"),
    ("erratum", "erratum"),
    ("book", "book"),
    ("book-chapter", "book chapter"),
    ("conference-paper", "conference paper"),
    ("book-review", None),
    ("conference-abstract", None),
    ("data-paper", None),
    ("dataset", None),
    ("dissertation", None),
    ("libguides", None),
    ("other", None),
    ("paratext", None),
    ("peer-review", None),
    ("preprint", None),
    ("reference-entry", None),
    ("report", None),
    ("retraction", None),
    ("software", None),
    ("software-paper", None),
    ("standard", None),
    ("supplementary-materials", None),
)

_SOURCE_ROWS: Final[tuple[tuple[str, str], ...]] = (
    ("journal", "journal"),
    ("conference", "conference"),
    ("ebook platform", "book"),
    ("book series", "book"),
    ("repository", "other"),
    ("metadata", "other"),
    ("other", "other"),
)


SUBFIELD_TO_ASJC: Final[Mapping[str, SubjectArea]] = {
    sid: SubjectArea(code, exact, asjc_name)
    for sid, _name, code, exact, asjc_name in _SUBFIELD_ROWS
}
TYPE_TO_DOC_TYPE: Final[Mapping[str, str | None]] = dict(_TYPE_ROWS)
SOURCE_TYPE_TO_VENUE_TYPE: Final[Mapping[str, str]] = dict(_SOURCE_ROWS)


def canonical_tables() -> dict[str, Any]:
    """The three tables in the canonical shape that ``MAP_SHA256`` hashes."""
    return {
        "SUBFIELD_TO_ASJC": {
            sid: {"asjc": code, "asjc_name": an, "exact": ex, "name": name}
            for sid, name, code, ex, an in _SUBFIELD_ROWS
        },
        "TYPE_TO_DOC_TYPE": dict(_TYPE_ROWS),
        "SOURCE_TYPE_TO_VENUE_TYPE": dict(_SOURCE_ROWS),
    }


def compute_map_sha256() -> str:
    """Recompute the digest from the tables (``MAP_SHA256`` must equal it)."""
    blob = json.dumps(canonical_tables(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _refuse(what: str, value: object) -> ClassificationRefusal:
    return ClassificationRefusal(
        f"{what} {value!r} is not in classification map version {MAP_VERSION}"
    )


def subfield_to_asjc(subfield_id: str | int) -> SubjectArea:
    """Map an OpenAlex subfield id to its ASJC code.

    Accepts ``1204``, ``"1204"`` or the API's ``https://openalex.org/subfields/1204``.
    """
    key = str(subfield_id).rstrip("/").rsplit("/", 1)[-1]
    try:
        return SUBFIELD_TO_ASJC[key]
    except KeyError:
        raise _refuse("subfield", subfield_id) from None


def work_type_to_doc_type(work_type: str) -> str:
    """Map an OpenAlex work ``type`` to the engine's ``doc_type`` description."""
    doc_type = TYPE_TO_DOC_TYPE.get(work_type)
    if doc_type is None:  # unknown, or one of the 17 refused types
        raise _refuse("type", work_type)
    return doc_type


def source_type_to_venue_type(source_type: str | None) -> str:
    """Map ``primary_location.source.type`` to the closed ``venue_type`` set."""
    venue_type = SOURCE_TYPE_TO_VENUE_TYPE.get(source_type) if source_type else None
    if venue_type is None:
        raise _refuse("source type", source_type)
    return venue_type


def primary_topic_to_subject_area(primary_topic: Mapping[str, Any] | None) -> SubjectArea:
    """Subject area from ``primary_topic.subfield.id`` only (never ``topics[1:]``)."""
    subfield = (primary_topic or {}).get("subfield") or {}
    subfield_id = subfield.get("id")
    if subfield_id is None:
        raise ClassificationRefusal(
            f"primary_topic.subfield.id is missing; classification map version {MAP_VERSION}"
        )
    return subfield_to_asjc(subfield_id)
