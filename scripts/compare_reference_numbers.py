"""Assert that two machines exported the same ``numbers.json``.

Stage 11's reproducibility criterion (ADR 0027 Decision 3): a clean clone on
a different machine must reproduce every number. This is the "compare" half;
``export_reference_numbers.py`` is the "run" half, executed once per runner.

The comparison is over the parsed mappings rather than the raw bytes. Both
files are written by the same serialiser with ``sort_keys``, so byte equality
would hold -- but a byte diff on a 39-key JSON file reports "line 12 differs"
where a key diff reports *which number* changed, and a reproducibility
failure is read by someone trying to find out what is machine-dependent.

Usage::

    uv run --no-project python scripts/compare_reference_numbers.py exported/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: Keys whose value may legitimately differ between two runs, with the reason.
#:
#: BUILD_PLAN allows "an explicit, reviewer-justified timestamp allowlist".
#: Empty is the correct state today: `numbers.json` carries only counts,
#: shares and rates. `exported_at` lives in `manifest.json`, which this
#: comparison does not cover precisely because it is expected to differ.
#:
#: Adding a key here is a claim that a number is *allowed* to be
#: machine-dependent, which is the claim Stage 11 exists to make expensive.
VOLATILE_KEYS: frozenset[str] = frozenset()


def main() -> int:
    """Compare every ``numbers.json`` under ``argv[1]``.

    Returns:
        ``0`` when every machine agrees, ``1`` otherwise, with the
        disagreeing keys and their per-machine values printed.
    """
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} <directory-of-artifacts>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1])
    exports = sorted(root.rglob("numbers.json"))
    if len(exports) < 2:
        # Fewer than two machines proves nothing at all, and a comparison of
        # one file against itself would pass. That must be a failure, not a
        # quiet success: the criterion is about agreement *between* machines.
        print(
            f"::error::found {len(exports)} numbers.json under {root}; "
            "the reproducibility criterion needs at least two machines",
            file=sys.stderr,
        )
        return 1

    raw = {path.parent.name: path.read_bytes() for path in exports}
    loaded = {name: json.loads(payload.decode("utf-8")) for name, payload in raw.items()}
    machines = sorted(loaded)
    reference_name = machines[0]
    reference = loaded[reference_name]

    disagreements: list[str] = []
    for name in machines[1:]:
        other = loaded[name]
        keys = (set(reference) | set(other)) - VOLATILE_KEYS
        for key in sorted(keys):
            mine = reference.get(key, "<missing>")
            theirs = other.get(key, "<missing>")
            if mine != theirs:
                disagreements.append(f"{key}: {reference_name}={mine!r} vs {name}={theirs!r}")

    print(f"compared {len(machines)} machines: {', '.join(machines)}")
    print(f"{len(reference)} numbers, {len(VOLATILE_KEYS)} allowlisted as volatile")

    if disagreements:
        for line in disagreements:
            print(f"::error::numbers.json differs across machines -- {line}")
        return 1

    # Bytes, not only parsed values. `validation.md` claims byte-identity, and
    # the two are different assertions: Windows once produced a file whose
    # every number matched and whose bytes differed at byte 2, because `\n`
    # had been translated to `\r\n`.
    #
    # Checked *after* the key comparison, so a genuine numeric difference is
    # reported as a number rather than as an offset -- a reproducibility
    # failure is read by someone trying to find what is machine-dependent, and
    # "line 12 differs" does not tell them.
    reference_bytes = raw[reference_name]
    byte_differences = [name for name in machines[1:] if raw[name] != reference_bytes]
    if byte_differences:
        for name in byte_differences:
            print(
                f"::error::numbers.json is byte-different on {name} though every number "
                f"matches {reference_name} -- serialisation, not arithmetic (line endings, "
                "encoding, or key order)"
            )
        return 1

    print("every number is identical across every machine, and so is every byte")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
