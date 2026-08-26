# ADR 0010: Cross-Platform Locking for the Decision Log

## Status

Accepted — Phase 0b, 2026-08-26. Not a deviation from a frozen BUILD_PLAN contract: §2.4
names a technology stack and no operating system, and the decision-log rules of §Stage 4
(lines 970–974) are about durability and detectability, not about which system call
provides them. What this ADR records is a decision that changes observable behaviour on
one platform, and the one place where the two platforms deliberately differ.

## Context

`src/prismabib/prisma/log.py` opened with a module-level `import fcntl`. That module does
not exist on Windows, so on Windows the *import* failed — not the lock, the import. Every
consequence follows from that one line:

```python
>>> import prismabib.prisma.log
ModuleNotFoundError: No module named 'fcntl'
```

A researcher on Windows could install prismabib, configure a query, capture a corpus from
Scopus, and build the Layer 1 store. They found out at the first screening decision, after
the part of the pipeline that costs API quota and cannot be repeated against an index that
has since drifted. Windows is a large share of academic desktops; "works on the author's
machine" is precisely the failure mode this project exists to argue against.

Three properties of `decisions.jsonl` constrain any fix. It is **append-only** and holds
irreplaceable human screening labour — nothing else can reconstruct it. It is
**checksum-guarded** by a `decisions.jsonl.sha256` sidecar that is byte-identical to
`sha256sum decisions.jsonl` output, so that a reviewer can verify the log with a tool that
is not ours. And it is **gated at 100% line and 100% branch coverage** (§3.7.6), with
mutation testing over `prisma/` weekly, because every published number originates from a
fold over it.

There is a second, higher-severity defect in the same file, which the locking question
obscures. Both `os.open` calls omitted `O_BINARY`:

```python
fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)  # the log
fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)  # the sidecar
```

Without that flag the Windows C runtime opens the descriptor in text mode: every `\n`
written becomes `\r\n` on disk, and every `\r\n` read becomes `\n` again. In-process the
two translations cancel, so the existing tests would all still have passed — while the
bytes on disk were CRLF and an external `sha256sum` would have disagreed with the sidecar
prismabib itself had written. A tamper-detection sidecar that no outside tool can confirm
is worse than none: it looks like independent verification and is not, and the recovery
instructions in the truncated-line error tell the user to run exactly that command.

## Decision

**Support Windows properly: two lock backends behind one internal protocol, selected on
`sys.platform`, with the platform module resolved by function-local import. Open both
files binary on every platform.**

### The abstraction

```python
LockKind = Literal["shared", "exclusive"]


class _LockBackend(Protocol):
    def acquire(self, fd: int, kind: LockKind, path: Path) -> None: ...
    def release(self, fd: int) -> None: ...
```

`_locked(kind)` replaces `_locked(lock_operation: int)`. An `fcntl` constant in the
signature names one platform's API in the signature of code that has to run on two — and
there is no `fcntl` at all on the platform this abstraction exists for.

`_PosixLockBackend` calls `fcntl.flock` exactly as before: blocking, whole-file,
`LOCK_SH`/`LOCK_EX`, no timeout, no retry. **The working platform's code path is
unchanged**, which is the reason the backend split is a refactor on Linux and macOS rather
than a rewrite.

`_WindowsLockBackend` drives `msvcrt.locking`, and four differences from `flock` are
answered explicitly rather than left to surprise a Windows user:

1. **It locks a byte range at the descriptor's current position, and moves that
   position.** The backend locks one byte at offset `0x7FFF_FFFF` and restores the
   caller's position afterwards, including when the attempt fails. A range far past EOF
   never moves as the file grows, and — because Windows byte-range locks are *mandatory*,
   not advisory — it covers no data, so an ordinary reader is not shut out of bytes it is
   entitled to read. Locking byte 0 instead would break outside readers, including this
   project's own byte-level test helpers.
2. **There is no shared mode.** This is the named deviation; see below.
3. **`LK_LOCK` is not usable as a blocking mode.** It retries ten times at one-second
   intervals and then raises — an unconfigurable ten-second ceiling reported as a bare
   `OSError`. The backend uses the non-blocking mode with its own jittered exponential
   backoff to an explicit deadline, and raises a `LogError` that names the file, says how
   long it waited, names the likely cause, and states that no decision was written.
4. **Re-locking a region from the same handle fails**, where POSIX quietly succeeds. Both
   backends now refuse it, so the mistake reads identically on both platforms instead of
   being a Windows-only discovery. `DecisionLog._locked` refuses to nest for the same
   reason: each call opens a *new* descriptor, so nesting asks for a second conflicting
   lock from one thread — which blocks forever on POSIX. Nothing nests today; the guard
   keeps that true and turns a hang into an error.

### The named deviation: `"shared"` is exclusive on Windows

`msvcrt` documents `LK_RLCK` as identical to `LK_LOCK`. There is no read lock to take, so
`DecisionLog.load()`'s shared lock is satisfied with an exclusive one on Windows. This is
**never weaker than what was asked for, only less concurrent**: two simultaneous readers
serialise. Correctness is untouched; the cost is a wait, on an operation that reads a file
of a few hundred kilobytes. It is pinned by a test, not merely written down here.

### `O_BINARY`, and how it is checked

Both `os.open` calls now pass `getattr(os, "O_BINARY", 0)`. The flag is `0` on POSIX,
which is the identity for `|`, so the POSIX call is bit-for-bit what it always was.

Because that means the fix is *literally the same call* on Linux, no Linux test can
detect its removal — confirmed by mutation: deleting `| _O_BINARY` from either call site
leaves the whole suite green. What the suite adds instead are byte-level assertions that
run on every platform: `b"\r\n" not in decisions.jsonl`, the same for the sidecar, and the
file's `hashlib.sha256` digest equal to the one the sidecar records. On Linux these are
trivially true. On Windows they are the detector, and without them the corruption is
silent. The `full-windows` CI job is what makes them run there.

## Alternatives rejected

### 1. Declare the project POSIX-only

Document that prismabib requires Linux or macOS, raise a clear error on import elsewhere,
and change no locking code. This is honest, it is one line of work, and the module
docstring already claimed it ("matching this project's Linux/macOS development and CI
targets").

Rejected by the owner in favour of real support. The reasoning: the tool's purpose is to
let *other* researchers reproduce and check a systematic review, a large fraction of them
work on Windows, and a reproducibility tool that a reader cannot run is not doing the job
it exists for. A clear error at import is a real improvement on `ModuleNotFoundError`, but
it improves the message on a door that stays shut.

### 2. `ctypes` → `LockFileEx`

The Win32 API has what `msvcrt` lacks: `LockFileEx` takes `LOCKFILE_EXCLUSIVE_LOCK` as a
*flag*, so a genuine shared lock is available and the deviation above would disappear. It
also has `LOCKFILE_FAIL_IMMEDIATELY`, so the retry loop stays under our control.

Rejected. `LockFileEx` requires marshalling an `OVERLAPPED` structure through `ctypes`,
converting a CRT file descriptor to an OS handle with `msvcrt.get_osfhandle`, and reading
`GetLastError` to distinguish contention from failure. That is pointer-level code whose
mistakes — a wrong structure layout, a handle of the wrong width, a missing
`byref` — produce silent misbehaviour rather than exceptions, and it is *precisely the
code that can only fail on the machine we cannot test on*. `ctypes` is also effectively
untyped: `windll.kernel32.LockFileEx` is `Any` to mypy, so `--strict` compliance would be
bought with casts that assert exactly the things most likely to be wrong.

The trade is a real shared lock, whose only benefit is concurrency between two
simultaneous *readers* of a small file, against a class of defect that cannot be found by
this project's tests. Taken on the most safety-critical file in the system, that is the
wrong trade.

### 3. `portalocker` or `filelock`

Either would supply a maintained cross-platform lock in one line. `filelock` is widely
used and well tested on both platforms.

Rejected on two independent grounds. §2.4's dependency list is closed: every entry in
`pyproject.toml` traces to a named row in the plan, and adding one is scope widening that
this project rejects on principle. And both libraries default to *lock files* beside the
target — see alternative 4 for why that is a different, weaker guarantee than the one this
module currently makes. `filelock` documents that it provides no shared mode either, so
even the deviation would not go away.

### 4. A separate lock file beside `decisions.jsonl`

Lock `decisions.jsonl.lock` instead of the log, sidestepping both the byte-range problem
and the mandatory-locking problem, on both platforms, with `O_CREAT | O_EXCL` semantics
that need no platform module at all.

Rejected. It moves the guarantee off the artefact it is supposed to protect. A lock on a
different file is only honoured by processes that agree to check that file: a second copy
of prismabib, an editor, a script, or a user who deletes a stale `.lock` after a crash
would all proceed straight into the real one. It also introduces a state that can outlive
its holder — the stale-lock problem, whose usual answers (PID files, timeouts, "delete
this file to continue") are exactly the kind of thing that gets deleted casually the first
time it is in someone's way. And it adds a third file to a directory whose contract is
currently "the log, and a checksum of the log". Locking the file itself is what makes the
lock die with the process that held it.

## Consequences

### 1. `prismabib.prisma.log` imports with no platform lock module at all

Both `fcntl` and `msvcrt` are imported inside functions. Import-time work is a
`sys.platform` comparison; the platform module is loaded when a backend is constructed.
This is pinned by a test that runs the import in a subprocess with both modules blocked in
`sys.modules` — the reported bug, stated exactly, and it fails on the pre-fix code at
`log.py:83`.

### 2. Windows raises where POSIX waits

`flock` blocks indefinitely; the Windows backend gives up after 10 seconds and raises
`LogError`. A Windows user who leaves a second notebook kernel holding the log gets an
error naming that cause after ten seconds, where a Linux user waits. Neither loses a
decision — the message says so explicitly, because the append has not started — but the
behaviours differ and this is where that is written down.

### 3. The lock is not re-entrant, on either platform

`DecisionLog._locked` refuses to nest, and each backend refuses a second lock on a
descriptor it already holds. Any future code that wants to read inside a write critical
section must reuse the descriptor it already has (as `append_event` does, calling
`_verify_and_load_locked` rather than `load`), not take a second lock.

### 4. The Windows backend's behaviour is asserted against a fake, and that is not enough

Everything except `full-windows` exercises `_WindowsLockBackend` against an injected
stand-in for `msvcrt.locking` that models per-handle range ownership and `EACCES` on
conflict. A fake that agrees with itself proves the backend's *logic* is self-consistent
and proves nothing whatever about `msvcrt`. `full-windows` is the only check of the model
against reality, and it is not a required check yet, so for now the Windows claim rests on
a job that can go red without blocking a merge. `docs/testing.md` says this in the same
words.

### 5. `.gitattributes` now exists, and is load-bearing

Git for Windows defaults to `core.autocrlf=true`. Without an explicit policy, a Windows
clone would rewrite the reference fixture's captured Scopus pages to CRLF and break
`manifest.json`'s `payload_sha256` — a checkout that fails to verify on a machine where
nothing is wrong. `* text=auto eol=lf`, with explicit `-text` for the checksummed
fixtures and for `decisions.jsonl` and its sidecar. A meta-test asserts the attribute git
actually resolves for those paths, since no POSIX CI job can otherwise see it.

### 6. `build_store(rebuild=True)` explains an undeletable store

The rebuild path unlinks the existing DuckDB file. On POSIX that succeeds even with the
file open; on Windows it raises `PermissionError` if any connection is open, and the most
likely holder is the caller's own `Corpus` or an earlier notebook kernel. It is now a
`StoreError` naming that cause.

## Constraints

- **The decision log's contract is unchanged.** Append-only, one `write(2)` per line,
  `fsync` per append, sidecar rewritten via write-temp-then-`os.replace`, shared lock for
  reads and exclusive for writes, held across the caller's whole critical section, and
  both crash signatures (truncated final line; sidecar covering a prefix) still
  distinguished from hand-editing.
- **The sidecar stays `sha256sum`-compatible.** That is what `O_BINARY` protects and what
  the truncated-line recovery instructions depend on.
- **The locking code stays inside `log.py`.** A new `prisma/locking.py` would fall under
  the *global* 85% gate — the weakest gate in the project, applied to its most
  safety-critical primitive — or else force an edit to the §3.7.6 gate table, which
  `tests/unit/test_harness.py` asserts equals a literal transcription of the plan.
- **No `pragma: no cover` on reachable logic.** Both arms of `_select_lock_backend`, and
  every branch of the retry loop, are covered by tests on Linux; `log.py` remains at 100%
  line and 100% branch.
- **`full-windows` must not be added to the required checks until it has passed on
  `main`**, on the same reasoning the `e2e` job's header records.

## Related decisions

- **ADR 0002** (Append-Only Decision Log): the contract this ADR had to preserve while
  changing how the file is locked — the fold key, the event schema, and why the log is
  events rather than mutations
- **ADR 0006** (Public Repository and Single-Owner Review): why `full-windows` being
  non-required matters — CI, not human review, is the only gate on `main`
- **ADR 0009** (Mutation Scope Is Configured, Not Passed on the Command Line): the weekly
  mutation run over `prisma/`, which now includes both lock backends

## References

- BUILD_PLAN §2.4 (technology stack; the closed dependency list)
- BUILD_PLAN §Stage 4 lines 970–974 (the decision log's durability rules) and §3.7.6
  (100% line and branch on `prisma/log.py`)
- BUILD_PLAN §3.7.3 rule 1 (mock at the boundary; the `msvcrt` stand-in is injected, not
  monkeypatched) and rule 3 (determinism: the retry loop's clock, sleep and jitter are all
  injected)
- `src/prismabib/prisma/log.py` — `_LockBackend`, `_PosixLockBackend`,
  `_WindowsLockBackend`, `_ByteRangeLocking`, `_select_lock_backend`, `_O_BINARY`
- `tests/integration/prisma/test_log.py` — the backend conformance suite and the
  byte-level assertions; `tests/prisma_helpers.py` — `FakeWindowsLocking`
- `.github/workflows/ci.yml` — the `full-windows` job and why it is not required
- [Microsoft C runtime, `_locking`](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking)
  — `_LK_RLCK` "identical to `_LK_LOCK`"; `_LK_LOCK` "attempts ... 10 times at
  one-second intervals"
- [Python `msvcrt` documentation](https://docs.python.org/3/library/msvcrt.html) — "The
  locked region of the file extends from the current file position for `nbytes` bytes, and
  may continue beyond the end of the file"; "Multiple regions in a file may be locked at
  the same time, but may not overlap"
- [Git `gitattributes`](https://git-scm.com/docs/gitattributes) — `text`, `eol`, and
  `core.autocrlf` interaction

---

This ADR records how `decisions.jsonl` is locked and how its bytes are written. Reverting
to a single POSIX-only backend, changing the sentinel lock range, weakening the
non-reentrancy rule, or making `"shared"` mean something other than "an exclusive lock on
Windows" requires a new ADR that supersedes this one (§2.6).
