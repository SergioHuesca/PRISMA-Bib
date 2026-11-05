---
name: Evaluation Finding
about: File a finding from a peer evaluation session (no participant identifiers, no date)
title: "[EVAL] Task [T0-T7] brief description"
labels: evaluation
assignees: SergioHuesca

---

<!--
Do not include any participant name, session code, email address, date, file path that contains a user
name, API key, or the content of a .env file. Describe the situation in neutral words. Refer to the task
by its number only. Give the OS family and the Python minor version only.
-->

## Category

One of: bug / usability / docs / out-of-scope

## Severity

One of: blocker / major / minor

- **blocker**: the participant cannot continue without facilitator help beyond hint level 2, or the tool's
  own wording caused a wrong belief, or the participant sees a wrong or misleading number.
- **major**: a significant detour, or a wrong belief about the tool, where the participant ignored wording
  that was visible on screen.
- **minor**: friction only.

## Task

[T0 to T7, and the step inside the task, if known]

## What happened

[What the user did, in neutral words, and what the tool or guide showed. No names.]

## Expected behavior

[What the guide says, or what the tool's own message says it does]

## Evidence

[Exact text of the message, the command typed, the exit code, and the line in the guide or the file.
Do not paste a key, a `.env` line, or a path with a user name in it.]

```
[Paste the exact output, with any user name replaced by <user>]
```

## Environment

- **OS family**: [e.g., Windows 11]
- **Python minor version**: [e.g., 3.11]
- **prismabib version or commit**: [output of `uv run prismabib --version`, or the commit]
- **Sources mode**: [openalex or scopus+openalex]
- **Platform**: [Windows, or macOS or Linux if different]

## Hint level needed

[No hint, 1, 2, or 3. Level 3 means the facilitator gave the command or value.]

## Suggested fix (optional)

[Only if the cause is clear]

## Checklist

- [ ] No participant name, session code, email address, or date appears in this issue.
- [ ] No API key, no part of a key, and no `.env` content appears in this issue.
- [ ] Every path with a Windows user name in it has been shortened to `<projects root>\...`.
- [ ] No screenshot is attached, or any attached screenshot has been checked for keys and user names.
- [ ] Only the finding text is carried over, not the sheet.
