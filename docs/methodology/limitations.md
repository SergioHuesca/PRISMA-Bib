# Limitations

This page documents known limitations, biases, and gaps in prismabib's coverage and methodology.

## Delivered in Stages 3 and 6

**Limitations first discussed in Stage 3** (when Layer 1 store and full-text acquisition are designed):

- **Single-database coverage** — Scopus only, no Web of Science, OpenAlex, or Crossref (deliberate, §1.3 non-goals; multi-source is deferred work per §8)
- **Author disambiguation** — Scopus author IDs only; no disambiguation across author name variations (deferred to v2.0)
- **Full-text coverage bias** — ScienceDirect API access is entitlement-gated; coverage is incomplete and publisher-skewed
- **Index drift** — Scopus updates records retroactively; re-running the same query months later may return different results (mitigated by Layer 0 manifests)
- **Language filtering bias** — Scopus language field is author-supplied and unreliable (documented and measured in validation data)
- **Citation lag** — Scopus citation counts are incomplete for recent publications (less than 1 year old)

**Additional limitations in Stage 6** (when full-text extraction is complete):

- **Section extraction** — full-text analysis is limited to designated sections (abstract, methods, results, discussion); other sections not extracted
- **Methodology scope** — no backward citation snowballing or forward citation tracking (deferred; specified in §8)
- **LLM exclusion** — no automated coding beyond the versioned taxonomy rules (deliberate per ADR 0003; LLM support deferred and must preserve human-decision provenance)

Stage 11 delivers `methodology/validation.md`, which will record how these limitations are measured in a reference run. That page does not exist yet, so it is named here rather than linked.

## Known issues and measurement

Each limitation is measured in the reference dataset and reported with:

- **Magnitude** — how many records or what percentage are affected
- **Direction** — whether the bias increases or decreases reported counts
- **Mitigation** — what the system does to minimize impact (e.g., Layer 0 manifests for index drift)
- **Documentation** — where in the exported report this limitation is called out

This ensures that anyone citing prismabib results knows exactly what is and is not included, and can judge whether the limitations affect their conclusions.

See [Architecture Overview](../architecture/overview.md) for the core design, BUILD_PLAN.md §1.3 for non-goals, and §8 for deferred work.
