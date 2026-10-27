"""Static invariants of .github/workflows/update-behind-prs.yml (ADR 0037 PR 4).

The workflow cannot run here, so this parses it and checks the properties that keep it safe:
least privilege, no PR code, no schedule trigger, the lists' fields, and where the mutating
commands may appear. ``violations`` returns every broken invariant; the real file must have
none and each mutation of it must have at least one.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_WF = Path(__file__).parent.parent.parent / ".github" / "workflows" / "update-behind-prs.yml"
_SHA_PIN = re.compile(r"[^@\s]+@[0-9a-f]{40}")


def _script_lines(run: str) -> list[str]:
    """The run script without full-line comments (a comment may name the forbidden commands)."""
    return [ln for ln in run.splitlines() if not ln.lstrip().startswith("#")]


def _joined(run: str) -> str:
    """Script text with comments dropped and backslash continuations folded to one line."""
    return re.sub(r"\\\n\s*", " ", "\n".join(_script_lines(run)))


def _calls(script: str, needle: str) -> list[str]:
    return [ln for ln in script.splitlines() if needle in ln]


def violations(text: str) -> list[str]:
    wf: dict[Any, Any] = yaml.safe_load(text)
    bad: list[str] = []
    on = wf.get("on", wf.get(True))
    if set(on) & {"pull_request_target", "schedule", "pull_request"}:
        bad.append("forbidden trigger")
    if wf.get("permissions") != {}:
        bad.append("top-level permissions must be {}")
    if wf.get("concurrency") != {"group": "update-behind-prs", "cancel-in-progress": False}:
        bad.append("concurrency changed")
    job = wf["jobs"]["update"]
    if job.get("permissions") != {"contents": "read"}:
        bad.append("job permissions must be contents: read only")
    steps = job["steps"]
    checkout = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")]
    if len(checkout) != 1:
        bad.append("expected exactly one checkout")
    else:
        with_ = checkout[0].get("with", {})
        if "ref" in with_ or "repository" in with_:
            bad.append("checkout must not set ref or repository")
        if with_.get("persist-credentials") is not False:
            bad.append("persist-credentials must be false")
    for s in steps:
        uses = str(s.get("uses", ""))
        if uses and not uses.startswith("actions/checkout@") and not _SHA_PIN.fullmatch(uses):
            bad.append(f"action not pinned by full SHA: {uses}")
    app = [s for s in steps if "create-github-app-token" in str(s.get("uses", ""))]
    if len(app) != 1:
        bad.append("expected exactly one App token step")
    else:
        perms = {k: v for k, v in app[0]["with"].items() if k.startswith("permission-")}
        if perms != {"permission-contents": "write", "permission-pull-requests": "write"}:
            bad.append("App token permissions changed")
    if text.count("steps.app.outputs.token") != 1:
        bad.append("the App token must reach exactly one step")
    runs = [s for s in steps if "run" in s]
    if len(runs) != 1:
        return [*bad, "expected exactly one run step"]
    step = runs[0]
    if "${{" in step["run"]:
        bad.append("expression interpolation inside a run script")
    if "MERGE_TRAIN_STACKS" not in step.get("env", {}):
        bad.append("MERGE_TRAIN_STACKS is not passed through env")
    script = _joined(step["run"])
    if re.search(r"\beval\b", script):
        bad.append("eval")
    if re.search(r"git (checkout|switch|worktree)|\bsource\b|\bpip\b|\bnpm\b|\bmake\b", script):
        bad.append("something that could run PR content")
    for call in re.findall(r"gh pr (?:list|view)[^\n]*", script):
        if "--base" in call:
            bad.append(f"--base filter: {call[:60]}")
        if "--json" in call and "baseRefName" not in call:
            bad.append(f"baseRefName missing: {call[:60]}")
    if len(re.findall(r"gh pr list", script)) != 2 or "list_open()" not in script:
        bad.append("expected the open list function and the closed list only")
    if "--state closed" not in script:
        bad.append("closed list missing")
    if "--limit 100" not in script:
        bad.append("--limit 100 missing")
    decides = _calls(script, "train_step.py decide")
    # the two-step: a decide without --head-check, then one with --head-check via "${verify[@]}"
    if len(decides) != 2 or not all(
        '--merge-train-stacks "${MERGE_TRAIN_STACKS:-off}"' in d for d in decides
    ):
        bad.append("both decide calls must pass --merge-train-stacks")
    if len(re.findall(r'"\$\{facts_args\[@\]\}"', script)) != 2:
        bad.append("facts must be passed to both decide calls")
    merges = [m.start() for m in re.finditer(r"gh pr merge", script)]
    client = [m.start() for m in re.finditer(r"train_merge_async\.py", script)]
    guard = script.find('if [ "$stacked" != true ]; then')
    if len(merges) != 1 or len(client) != 1 or guard < 0:
        bad.append("expected one gh pr merge and one merge client call")
    else:
        els = script.find("else", merges[0])
        if not guard < merges[0] < els < client[0]:
            bad.append("gh pr merge must sit only in the non-stacked arm")
        call = script[client[0] : script.find("\n", client[0])]
        if '--head "$sha"' not in call or "--audit-file" not in call or "--pr" not in call:
            bad.append("merge client needs --pr, --head and --audit-file")
    ub = [m.start() for m in re.finditer(r"gh pr update-branch", script)]
    gate = script.find("(.stack // null) != null")
    if len(ub) != 1 or gate < 0 or not gate < ub[0]:
        bad.append("update-branch must come after the stack check")
    else:
        between = script[gate : ub[0]]
        for need in (
            '[ "$stacked" = true ] && [ "$mode" = train ]',  # train mode halts a stacked PR
            'elif [ "$stacked" = true ]',  # update-only mode warns and skips
            'elif [ "$stacked" != false ]',  # an unknown stack check also skips
            "stacked PR is behind main",
        ):
            if need not in between:
                bad.append(f"update arm lacks: {need}")
    mcall = merges and script[merges[0] : script.find(chr(10), merges[0])]
    if mcall and not ("--squash" in mcall and '--match-head-commit "$sha"' in mcall):
        bad.append('gh pr merge must be --squash --match-head-commit "$sha"')
    if "--merge " in script or "--rebase" in script:
        bad.append("a merge method other than squash")
    if script.count("sha_re='^[0-9a-f]{40}$'") < 2:
        bad.append("shell sha-regex guards missing")
    if '"${#vargs[@]}" = 6' not in script:
        bad.append("verifier_args count check missing")
    alarm = script[script.find("audit_alarm() {") :][:600]
    if "audit_alarm() {" not in script or "exit 1" not in alarm.split("}")[0]:
        bad.append("audit_alarm must exit 1")
    if "--merge-train on" in script or script.count('--merge-train "${MERGE_TRAIN:-off}"') != 3:
        bad.append("--merge-train must come from MERGE_TRAIN, defaulting to off (3 calls)")
    if script.count("--limit 100") != 2:
        bad.append("--limit 100 must be on both lists")
    stops = [m.start() for m in re.finditer(r"train_step.py audit-stop", script)]
    failed = script.find('"$(jq -r .failed "$audit")" = true')
    if len(stops) != 1 or failed < 0 or not failed < stops[0]:
        bad.append("audit-stop must run only after a failed audit")
    elif script.find("list_open >", failed) not in range(failed, stops[0]):
        bad.append("the open PRs must be re-listed before audit-stop")
    return bad


@pytest.fixture(scope="module")
def text() -> str:
    raw = _WF.read_bytes()
    assert b"\r" not in raw
    return raw.decode("utf-8")


def test_real_workflow__has_no_violations(text):
    assert violations(text) == []


def test_real_workflow__bash_syntax_is_valid(text, tmp_path):
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    script = tmp_path / "run.sh"
    script.write_bytes(yaml.safe_load(text)["jobs"]["update"]["steps"][-1]["run"].encode("utf-8"))
    proc = subprocess.run([bash, "-n", str(script)], capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")


# ---- mutations: each must be caught -------------------------------------------------------


def _replace(text: str, old: str, new: str, count: int = 1) -> str:
    assert old in text, old
    return text.replace(old, new, count)


MUTATIONS = {
    "re-add --base main to the open list": lambda t: _replace(
        t, "gh pr list --state open", "gh pr list --base main --state open"
    ),
    "re-add --base main to the closed list": lambda t: _replace(
        t, "gh pr list --state closed", "gh pr list --base main --state closed"
    ),
    "drop baseRefName from the open list": lambda t: _replace(
        t, "statusCheckRollup,baseRefName\n", "statusCheckRollup\n"
    ),
    "drop baseRefName from the closed list": lambda t: _replace(
        t, "mergedAt,baseRefName", "mergedAt"
    ),
    "drop baseRefName from gh pr view": lambda t: _replace(
        t, "statusCheckRollup,baseRefName)", "statusCheckRollup)"
    ),
    "drop the stack guard of update-branch": lambda t: _replace(
        t, "(.stack // null) != null", "true"
    ),
    "run update-branch unconditionally": lambda t: _replace(
        t, 'if [ "$stacked" != false ]; then', "if false; then"
    ),
    "add a schedule trigger": lambda t: _replace(
        t,
        "  workflow_dispatch:\n",
        "  workflow_dispatch:\n  schedule:\n    - cron: '*/5 * * * *'\n",
    ),
    "add pull_request_target": lambda t: _replace(
        t, "  workflow_dispatch:\n", "  workflow_dispatch:\n  pull_request_target:\n"
    ),
    "add ref to the checkout": lambda t: _replace(
        t,
        "        with:\n          fetch-depth: 0\n",
        "        with:\n          ref: ${{ github.event.workflow_run.head_sha }}\n          fetch-depth: 0\n",
    ),
    "persist credentials": lambda t: _replace(
        t, "persist-credentials: false", "persist-credentials: true"
    ),
    "widen the token": lambda t: _replace(
        t,
        "permission-contents: write\n",
        "permission-contents: write\n          permission-workflows: write\n",
    ),
    "widen the job permissions": lambda t: _replace(
        t, "      contents: read\n", "      contents: write\n"
    ),
    "interpolate PR data into run": lambda t: _replace(
        t,
        'echo "$action (mode $mode): $reason"',
        'echo "${{ github.event.workflow_run.head_branch }}"',
    ),
    "gh pr merge in the stacked arm": lambda t: _replace(
        t,
        'python3 scripts/train_merge_async.py --repo "$GH_REPO"',
        'gh pr merge "$pr" --squash; python3 scripts/train_merge_async.py --repo "$GH_REPO"',
    ),
    "merge client without audit file": lambda t: _replace(t, ' --audit-file "$audit"', ""),
    "merge client without head": lambda t: _replace(
        t, '--head "$sha" --audit-file', "--audit-file"
    ),
    "decide without the stacks switch": lambda t: _replace(
        t,
        '--merge-train-stacks "${MERGE_TRAIN_STACKS:-off}" "${facts_args[@]}" "${verify[@]}"',
        '"${facts_args[@]}" "${verify[@]}"',
    ),
    "audit-stop on every merge": lambda t: _replace(
        t,
        'if [ -f "$audit" ] && [ "$(jq -r .failed "$audit")" = true ]; then',
        'if [ -f "$audit" ]; then',
    ),
    "change the concurrency": lambda t: _replace(
        t, "cancel-in-progress: false", "cancel-in-progress: true"
    ),
    "top-level permissions": lambda t: _replace(
        t, "permissions: {}", "permissions:\n  contents: write"
    ),
    "unpinned new action": lambda t: _replace(
        t,
        "      - name: Decide and act",
        "      - uses: some/action@v1\n      - name: Decide and act",
    ),
    "merge without match-head-commit": lambda t: _replace(
        t, ' --match-head-commit "$sha"; then', "; then"
    ),
    "merge method not squash": lambda t: _replace(
        t, 'gh pr merge "$pr" --squash', 'gh pr merge "$pr" --merge'
    ),
    "sha guard removed": lambda t: _replace(t, "sha_re='^[0-9a-f]{40}$'", "x=1", 2),
    "vargs count check removed": lambda t: _replace(t, '[ "${#vargs[@]}" = 6 ]', "true"),
    "audit alarm without exit": lambda t: _replace(
        t,
        'audit failed: $areason"\n                    exit 1',
        'audit failed: $areason"\n                    :',
    ),
    "merge train hardcoded on": lambda t: _replace(
        t, 'decide --merge-train "${MERGE_TRAIN:-off}"', "decide --merge-train on"
    ),
    "limit dropped": lambda t: _replace(
        t,
        'gh pr list --state closed --search "is:unmerged" --limit 100',
        'gh pr list --state closed --search "is:unmerged"',
    ),
    "train mode stacked update halt removed": lambda t: _replace(
        t, '[ "$stacked" = true ] && [ "$mode" = train ]', "false"
    ),
    "eval": lambda t: _replace(t, "out=$(python3", 'eval "$x"; out=$(python3'),
    "MERGE_TRAIN_STACKS not in env": lambda t: _replace(
        t, "MERGE_TRAIN_STACKS: ${{ vars.MERGE_TRAIN_STACKS }}", "OTHER: 1"
    ),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_mutation__is_caught(text, name):
    assert violations(MUTATIONS[name](text)) != [], name
