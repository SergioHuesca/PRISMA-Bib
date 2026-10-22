"""Contract test: the on-disk shape of an OpenAlex Layer 0 run (ADR 0031 Decisions 1-4).

The loader (another module) reads these runs back, so the names and fields below
are an interface: this test pins them literally rather than through the models.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from prismabib.capture.openalex import capture_openalex
from prismabib.config import Settings
from prismabib.project import Project
from prismabib.sources.ratelimit import RateLimiter

_BODY = (
    b'{"results":[{"id":"https://openalex.org/W1","doi":"https://doi.org/10.1/a",'
    b'"title":"T","publication_year":2020,"abstract_inverted_index":null,'
    b'"authorships":[],"topics":[]}]}'
)


@pytest.mark.contract
def test_openalex_run__layout__matches_adr_0031(tmp_path: Path) -> None:
    project = Project.init("demo", title="Demo", root=tmp_path)
    manifest = capture_openalex(
        project,
        dois=["10.1/a"],
        settings=Settings(scopus_api_key="x"),
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=_BODY)),
        rate_limiter=RateLimiter(rate=1_000_000.0),
    )
    assert manifest is not None
    run_dir = project.raw_dir / "openalex" / manifest.run_id
    assert sorted(p.name for p in run_dir.iterdir()) == ["manifest.json", "response-0000.jsonl"]
    assert (run_dir / "response-0000.jsonl").read_bytes() == _BODY + b"\n"

    raw = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert set(raw) == {
        "run_id", "started_at", "finished_at", "endpoint", "fallback_opt_in",
        "api_key_used", "refresh", "requests", "payload_files", "payload_sha256",
        "client_version", "criteria_version",
    }  # fmt: skip
    assert "total_results" not in raw
    assert raw["endpoint"] == "https://api.openalex.org/works"  # no query string
    assert raw["payload_files"] == ["response-0000.jsonl"]
    [request] = raw["requests"]
    assert set(request) == {
        "index", "file", "sha256", "encoding", "raw_sha256", "lookup", "identifiers",
        "params", "api_view", "entitlement_context", "http_status", "retrieved_at",
        "result_count", "outcomes",
    }  # fmt: skip
    assert set(request["entitlement_context"]) == {
        "api_key_used", "fallback_opt_in", "refusal_status",
    }  # fmt: skip
    assert request["outcomes"] == [
        {
            "identifier": "10.1/a",
            "outcome": "value",
            "http_status": 200,
            "openalex_id": "https://openalex.org/W1",
            "absent_fields": ["abstract", "authors", "affiliations", "keywords"],
        }
    ]
