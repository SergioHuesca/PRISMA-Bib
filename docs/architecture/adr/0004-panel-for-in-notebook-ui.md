# ADR 0004: Panel for In-Notebook UI

## Status

Accepted — Stage 1, 2026-08-18.

## Context

The screening interface must satisfy two deployment contexts simultaneously:

1. **In-notebook** (Stage 5 requirement): reviewers work in Jupyter notebooks where all analysis lives
2. **Standalone web app** (BUILD_PLAN §1.2 line 57, consequence): the same widget code must serve via `panel serve` with no code rewrite

This is unusual: most web frameworks are built *either* as standalone apps (Streamlit, Django) *or* as notebook widgets (ipywidgets). A dual-mode deployment requires a framework that spans both.

## Decision

**Panel (HoloViz) is the screening UI framework** (BUILD_PLAN §1.2 line 57 and §2.4 line 271).

Panel's architecture allows the same widget code to run in three contexts without modification:

1. Jupyter notebook cell (interactive in the live kernel)
2. JupyterLab with `panel preview notebook.ipynb`
3. Standalone web app via `panel serve notebook.ipynb`

This is the only framework where UI code does not fork based on deployment context.

## Rationale

### Screening order determines labour cost

Screening is the project's critical path. If the UI is slow or cumbersome, the review may never finish. Stage 5 requires ≥4 records/minute throughput (line 1092) and ≤100 ms persistence per decision (line 1090). This means:

- Pre-loading the next record while the human reads the current one (async, non-blocking)
- Minimal keystroke latency  
- No page reloads or kernel restarts

Panel's async support and in-notebook execution make this feasible.

### Standalone deployment is a v1.0 requirement

BUILD_PLAN §1.2 line 57 states: "Same code can later be served via `panel serve` with no rewrite." This is not deferred; it is a v1.0 acceptance criterion (Stage 5 line 1088): "Rendering `screener(...)` in a notebook cell produces a working interface; the same object serves via `panel serve` with no code change."

Choosing a notebook-only framework would license Stage 5 to skip this criterion.

## Contracts

**Stage 5 defines the frozen UI API (BUILD_PLAN Stage 5, lines 1081–1084):**

```python
def screening_queue(project: Project, stage: PrismaStage, reviewer: str) -> ScreeningQueue:
    """Fetch records for manual screening at a given stage by a specific reviewer.

    Args:
        project: The project handle.
        stage: One of 'title_abstract' or 'fulltext'.
        reviewer: Person code/ID.

    Returns:
        ScreeningQueue yielding Record objects in deterministic order,
        skipping already-decided records for this reviewer+stage.
    """


def screener(
    project: Project, *, stage: str, reviewer: str, blind: bool = True
) -> pn.viewable.Viewable:
    """Return a Panel view for screening.

    Args:
        project: The project handle.
        stage: 'title_abstract' or 'fulltext'.
        reviewer: Person code/ID.
        blind: If True (default), hide author names and citation counts
               to reduce prestige bias (Stage 5 line 1074).

    Returns:
        A Panel Viewable. Display in a notebook cell or `panel serve` it.
    """
```

**Key parameters:**
- `reviewer`: Required for per-reviewer folding; missing it prevents second-reviewer support (ADR 0002 consequence 3)
- `blind`: Prestige-bias control (Stage 5 requirement 6, line 1074: "Citation count and author names are hidden by default")
- `stage`: Literal string, one of `{title_abstract, fulltext}` (Stage 4 line 960); this value is recorded in `decisions.jsonl`

## Consequences

### 1. Widget code is unmodified across deployments

```python
# In notebook:
from prismabib.screening import screener

ui = screener(project, stage="title_abstract", reviewer="alice", blind=True)
display(ui)  # Renders in the notebook cell

# Via panel serve (same file, no code change):
# $ panel serve my_notebook.ipynb
# → opens http://localhost:5006/my_notebook
# Same widget, same event-append logic
```

### 2. Per-reviewer state is mandatory

The `reviewer` parameter enables:
- Queue skips records already decided by this reviewer at this stage (Stage 5 requirement 4, line 1072)
- Event folding groups by `(stage, record_id, reviewer)` (ADR 0002 consequence 3)
- Future multi-reviewer support without schema changes (deferred per §8 line 1558)

### 3. Blind mode reduces prestige bias

When `blind=True`, the view model omits authors and citation counts (Stage 5 line 1074). This is testable (Stage 5 line 1106: `test_screener__blind_mode__omits_authors_and_citations_from_view_model`).

### 4. Throughput is measured on the append path

Stage 5 line 1090 specifies ≤100 ms persistence per decision. The benchmark (line 1109: `test_screener__decision_persisted_within_100ms`) measures the append time, not UI rendering. This is achievable because:

- Panel widgets are lightweight (no full-page reload)
- Appending to JSONL is fast (fsync per write)
- No blocking DB queries between keystroke and persistence

### 5. Stage name is literal "title_abstract", not "abstract"

The string passed to `screener(..., stage=...)` is recorded directly in `decisions.jsonl` (BUILD_PLAN Stage 4 line 960: `"stage": "title_abstract"`). Inventing a synonym creates a second source of truth. The frozen set is `{title_abstract, fulltext}`.

## Constraints

- **No server required in notebook mode.** Panel widgets run in the live kernel; no separate process.
- **Async handling is required.** Pre-loading the next record must not block the UI response to the keystroke.
- **Keyboard-first is required.** Stage 5 line 1070 specifies bindings: `i` (include), `e` (exclude + reason digit), `u` (unsure), `n`/`p` (next/prev), `z` (undo), `?` (help).

## Related decisions

- **ADR 0003** (Human-Only Screening): UI must enforce stable, unbiased order
- **ADR 0002** (Append-Only Decision Log): events are appended, never mutated
- BUILD_PLAN §1.2 line 57 (Panel decision)
- BUILD_PLAN §2.4 line 271 (technology stack rationale)

## References

- [Panel by HoloViz](https://panel.holoviz.org/)
- [Panel Deployment](https://panel.holoviz.org/how_to/deployment/index.html)
- BUILD_PLAN §1.2 line 57 (Panel consequence: same code serves in-notebook and standalone)
- BUILD_PLAN §Stage 5 lines 1081–1084 (API contracts)
- BUILD_PLAN §Stage 5 line 1088 (AC: same object serves both ways)
- BUILD_PLAN §Stage 5 line 1090 (AC: ≤100 ms persistence per decision)
- BUILD_PLAN §Stage 5 line 1092 (AC: ≥4 records/minute throughput)

---

This records BUILD_PLAN §1.2 line 57, which is not open for renegotiation. Changing it requires a new ADR that supersedes this one (§2.6).
