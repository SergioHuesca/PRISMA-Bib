#!/usr/bin/env bash
#
# BUILD_PLAN §2.5 data guard / risk 10.
#
# Rejects any staged path that would put licensed or derivable data into git:
#
#   projects/<slug>/raw/        Layer 0 Scopus payloads — redistribution is a licence
#                               violation, and on a hosted repo a push publishes them
#   projects/<slug>/store/      corpus.duckdb and friends — fully derivable from Layer 0
#   projects/<slug>/fulltext/   publisher PDFs/XML — licence
#   *.duckdb                    a store by any other path
#   .env                        secrets. `.env.example` is explicitly allowed: it is a
#                               tracked deliverable and carries variable names, no values.
#
# Exits non-zero and names every offending path. pre-commit passes staged files as "$@".

set -euo pipefail

pattern='(^|/)projects/[^/]+/(raw|store|fulltext)/|\.duckdb$|(^|/)\.env$'

offenders=()
for path in "$@"; do
    if [[ "$path" =~ $pattern ]]; then
        offenders+=("$path")
    fi
done

if [[ ${#offenders[@]} -gt 0 ]]; then
    echo "ERROR: refusing to commit licensed or derivable data (BUILD_PLAN §2.5)." >&2
    echo >&2
    for path in "${offenders[@]}"; do
        echo "    $path" >&2
    done
    echo >&2
    echo "These paths must never enter git. A push to GitHub cannot be retracted:" >&2
    echo "history rewriting does not reach forks or clones already taken." >&2
    echo >&2
    echo "If this is a false positive, fix the pattern in this script — do not" >&2
    echo "bypass the hook with --no-verify." >&2
    exit 1
fi
