# ADR 0043: A local GUI that calls library code and never alters results

## Status

Accepted. The values marked "proposed" in spec #312 are listed once under "Values for owner confirmation" and stand as written unless the owner overrules them before the merge. ADRs 0001 to 0042 are not edited.

## Context

Researchers who run systematic reviews but do not work in a terminal, TOML or YAML cannot use the command line, the notebooks or the two hand-edited files (the Project's settings and its Criteria). Spec #312 adds a local app, started with `prismabib gui`. The decision is hard to reverse, because the guarantees below are what an outside reader relies on to trust a result made in it.

## Decision

### What the GUI is

- The GUI is a local Panel app. It calls library functions directly, never a subprocess and never terminal text. It adds no result of its own and never alters one. The Project's settings file and Criteria file stay the source of truth and stay editable by hand, and a Project made on the command line opens in the app and the reverse.
- It is local-only: the server binds to the loopback address, on a free port it picks itself. The command has no option to bind elsewhere and refuses a non-loopback host with a message. The server rejects any request whose Host or origin is not the local app on the chosen port, which blocks DNS rebinding. If no port is free, the app shows an error. There is no remote, LAN, multi-user or authenticated mode.
- Install is one line, with uv as the only prerequisite: `uv tool install "prismabib[gui] @ git+https://github.com/SergioHuesca/PRISMA-Bib"`. The `gui` extra carries Panel and param and leaves out the plotting library, because no v1 screen plots. The command imports Panel only when it runs, so its help works on the base install. The clean-install CI job installs the extra and runs a launch smoke.

### Definitions

- **Size check**: before any Record is captured, the number of Records the search would return and its cost: the Scopus quota cost and remaining weekly allowance, or the OpenAlex page budget, remaining daily allowance and reset time. No capture starts without one.
- **Citable bundle**: what the library export function produces from the Sealed runs, the store-derived Flow counts and numbers, the Decision log and the manifests.
- **Store**: Layer 1, the normalised Records rebuilt entirely from Layer 0 and disposable by design.

### Credentials

API keys live in one per-user settings file in the operating system's user-config folder, outside every Project folder. The Settings screen also reads the Project's environment file when one exists. Where both define a key, the Project's value wins. The rule that refuses to write a key unless git confirms the file is ignored applies only to an environment file inside a repository, not to the per-user file. The GUI never relies on the working directory to find credentials. Keys are masked after entry and never appear in logs, errors, exports or fixtures. Scopus credentials are not requested in OpenAlex-only mode.

### Lock and step rules

- Single writer: the first window that opens a Project holds a lock in the Project folder. A second tab or process is read-only and says why. A lock left by a crash is reported and can be taken over after a confirmation.
- Source mode is fixed once any Run or Decision exists. The query is editable until the first Run starts. Criteria are editable until the first Decision is logged. The GUI enforces these locks even where the library does not, and a locked step shows its reason.
- A Project the library cannot read is refused, naming the version found, and its folder is not modified.

### Run statuses

Each Run has exactly one status, and these words are used only in these senses.

- **Sealed run**: finished completely, manifest written. Never modified, deleted or overwritten by the app.
- **Unsealed run**: cancelled or interrupted before it finished. Captured data is kept. It can be resumed or discarded. Closing the browser does not cancel a capture; if the app stops, the run is an interrupted unsealed run.
- **Discarded run**: an unsealed run the researcher marked as discarded after a confirmation. It deletes nothing, can never seal, and a discarded OpenAlex run still blocks a change of Source mode.

Cancel is not discard. Cancelling a capture leaves an unsealed run that can be resumed. Discarding is a separate, confirmed act.

Cancel mechanisms: capture checks a cancel flag between pages; build is cancelled by an exception raised in the worker at a safe point the library's build function declares, and the build is atomic, so the store is left as it was; export checks a cancel flag between files and leaves no partial bundle.

### Parity

Parity means the GUI path and the CLI path, given the same inputs, are identical after normalising run and event ids and timestamps, with a fixed clock and an id factory injected into both. Exact equality is required for `numbers.json` and the sealed payload hash. Git fields are excluded. Screening has no CLI path, so screening parity is checked against the Screener's decide API. Each parity case must be shown to fail when one byte of the layer's output is altered.

### Reviewer and actor

The Reviewer is the person named on a Decision: every Decision's reviewer field records the name given at Project creation. An actor is whoever or whatever ran a capture, build or export. The GUI does not record an actor in v1. The actor prompt stays in the CLI.

### Deletion

The app deletes only the built store, after a confirmation, and marks an unsealed run discarded, after a confirmation. It never deletes Layer 0, a Sealed run, the Decision log, or the Project's settings and Criteria files. Those leave the disk only when the researcher deletes the Project folder deliberately, outside the app. The Project stays listed and its store can be rebuilt. The confirmation says (wording proposed): "This deletes the Project's store, the normalised records that are rebuilt from the captured data. Your captured data (Layer 0), every sealed run, your decision log, and the Project's settings and Criteria files are kept. To remove them as well, delete the Project folder yourself."

### Errors

One function translates every library error into a sentence in everyday words plus the next step, with no traceback on screen. The classes: input the researcher can fix; prerequisite missing; credentials missing or rejected; quota reached (Scopus weekly, OpenAlex daily); network interrupted or offline; Project locked by another window; step locked, with the reason; Project from an older version or unreadable; cancelled; unexpected (a sentence and where the log is; the log never holds a key).

### Values for owner confirmation

Proposed in spec #312 and confirmed here in one place:

| Value | Setting |
|---|---|
| Full path limit | 240 characters, refused before any capture |
| Status request while a task runs | answered within 1 second |
| First Record at 10,000 Records | shown within 2 seconds of the Screening step opening, on the project's CI runner |
| Size thresholds | warn and ask confirmation above 2,000 Records; stop above 10,000 Records |
| Parity CI environments | UTC with en-US; Asia/Kathmandu with de-DE; both must give the same numbers |
| Key precedence | the Project's environment file beats the per-user file |
| Locks | query at the first Run; Criteria at the first Decision |
| Deletion wording | store only, as quoted under "Deletion" |

An explicit confirmation before a capture is required at every size.

### Deferred

Filling a manuscript; amending Criteria after screening starts; Scopus enrichment and cascade controls, including subject-area restriction in Scopus modes; full-text handling; bibliometrics and visualisation exploration; any deletion of Layer 0, Sealed runs, the Decision log or the Project folder from the app; languages other than English; hosted, LAN or multi-user use; a one-click installer; publishing to PyPI; a browser-automation test dependency; a separate reviewer role.

## Consequences

The GUI is a thin layer, so its guarantees are only as strong as the parity test that checks it. The locks make a Project's inputs immutable once they matter, at the cost of a new Project for a different search. A key defined in a Project's environment file silently overrides the per-user one; the Settings screen shows which source applies.
