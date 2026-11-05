# Enter Credentials

prismabib reads its API keys from a file named `.env`. The `prismabib credentials` commands write that
file for you, so a key does not have to be typed into a command line, and they show what is stored
without showing the values.

Run every command on this page from the repository root, the folder that holds `.gitignore`. The
`.env` file is read from, and written to, the folder you run the command from.

## Which key is for what

| Name | Set with | Used for |
|---|---|---|
| `SCOPUS_API_KEY` | `credentials set scopus` | `search`, `enrich`, the Scopus steps of `cascade`, and `credentials test scopus`. A project in source mode `openalex` never reads it. |
| `SCOPUS_INSTTOKEN` | `credentials set scopus` (optional) | The institutional token that your library issued for that key, if it issued one. |
| `OPENALEX_API_KEY` | `credentials set openalex` | `openalex-search` requires it for a real run. Its `--dry-run` does not, and prints `OpenAlex key: NOT SET (a real run is refused)`. The OpenAlex lookups in `cascade` and `openalex` use it when it is set. |
| `UNPAYWALL_EMAIL` | `credentials set unpaywall` | The Unpaywall lookup in `fulltext`. It is a contact address, not a secret. |

`credentials` manages only these four names. ScienceDirect's key, `ELSEVIER_SD_API_KEY`, is not
managed by these commands. Add it to `.env` by hand if you use full-text retrieval.

## Store a key

```bash
uv run prismabib credentials set scopus
```

The command prompts for each value. Typed secrets are hidden. The first value is required, and the
second, `SCOPUS_INSTTOKEN`, may be left blank. `set openalex` and `set unpaywall` ask for one value.

Three other ways to give the values:

- `--from-env` takes the values from variables that are already set in your environment:

  ```bash
  uv run prismabib credentials set scopus --from-env
  ```

- `--stdin` reads `NAME=VALUE` lines from standard input. Keep the file outside the repository:

  ```bash
  uv run prismabib credentials set scopus --stdin < ../keys-outside-repo.txt
  ```

  In PowerShell, pipe the file instead: `Get-Content ..\keys-outside-repo.txt | uv run prismabib credentials set scopus --stdin`

  The file holds one line per name, with placeholders such as these:

  ```text
  SCOPUS_API_KEY=<your Scopus key>
  SCOPUS_INSTTOKEN=<your institutional token, or leave this line out>
  ```

  Blank lines and lines that start with `#` are skipped. A name that the service does not use stops
  the command, and the line is not shown.

No option takes a key value, so a key never appears in your shell history. `--stdin` and
`--from-env` cannot be combined. A value may not contain a line break or the text `${`.

After a successful write, the command says which names it stored and in which file. It names the
values only, never the values themselves.

## Check what is stored

```bash
uv run prismabib credentials status
uv run prismabib credentials status --project my-review
```

The output looks like this:

```text
file: /path/to/repository/.env (exists)
git-ignored by a tracked rule: yes
SCOPUS_API_KEY: set (.env)
SCOPUS_INSTTOKEN: not set
OPENALEX_API_KEY: not set
UNPAYWALL_EMAIL: not set
```

- `set (.env)` means the value is in the file. `set (environment)` means the variable is set in your
  environment. The environment wins over the file, so `status` reports that case.
- `--project` adds the source mode of that project. In mode `openalex`, a stored Scopus key is reported
  as unused in that mode. `--project` reads the project from the projects root, and `--root` names
  that root when it is not the default.
- The line `git-ignored by a tracked rule` reads `no (<reason>)` when the file is not safely ignored.
  The reason is the one the writer would give, so `set` would refuse for the same reason.

`status` never prints a value.

## Test the Scopus key

```bash
uv run prismabib credentials test scopus
```

This makes one request to Scopus with the stored key. It reports one of these results:

- `scopus: ok`
- `scopus: 401: the key was rejected`
- `scopus: 403: the key is not entitled here (check the institutional token)`
- `scopus: network error (<error type>)`
- `scopus: http <status>` for any other status

If no Scopus key is stored, the command stops with ``SCOPUS_API_KEY is not set; run `prismabib
credentials set scopus`.`` and makes no request. If the key or the institutional token contains a
non-ASCII character, it reports `the key or token contains non-ASCII characters; no request made`.

The command exits with status 1 unless the result is `ok`. With `--project`, the command refuses in a
project whose mode is `openalex`, before any request.

## Remove a service's keys

```bash
uv run prismabib credentials clear openalex
```

`clear` removes the keys of that service from `.env`. `clear scopus` removes both Scopus names. A key
that is set in your environment is not removed, and the command tells you so.

## The Connect Scopus panel, from Python

`prismabib.credentials_ui.ConnectScopusPanel` is a password field over the same `.env` writer that
`credentials set scopus` uses. It shows only whether the Scopus key is set and which file it checked.
It never shows the value. After every attempt, successful or refused, it clears the field, so the
value is not kept in the widget state that a notebook can save.

The panel is not wired into any notebook or dashboard in this release. You create it yourself in a
notebook cell:

```python
from pathlib import Path

from prismabib.credentials_ui import ConnectScopusPanel
from prismabib.project import Project

repo = Path("/path/to/repository")  # the repository root, which holds .gitignore
project = Project.open("my-review")
panel = ConnectScopusPanel(project, env_file=repo / ".env")
panel.view()  # in a notebook, this displays the panel
```

The panel's title says "(unused in this mode)" when the project's sources mode is `openalex`. The
panel is enabled in every mode, so you can store a Scopus key before you change a project's mode.

Its arguments:

- `project` (optional) names the project whose mode sets that title.
- `env_file` (optional) is the `.env` to read and write. It defaults to `.env` in the working
  directory of the Python process.

Set `env_file` explicitly when you use the panel from a notebook. A notebook runs from the folder
that holds the notebook, so with the default `.env`, a Jupyter server started in `notebooks/` would
write a second `.env` there, which `prismabib` does not read when you run it from the repository
root. Either pass `env_file` as above, or start Jupyter from the repository root.

The panel uses the same checks as `credentials set`. If the file is not git-ignored by a tracked
rule, the panel's alert says `Refused:` with the reason, and it writes nothing.

## Where keys live, and what the commands refuse

- A key is written only to a file that git confirms is ignored by a rule in the repository's tracked
  `.gitignore`, the rule `.env` or `*.env`. Before writing, the command checks that:
  - the folder is inside a git repository;
  - the `.env` file is not tracked;
  - the temporary file the writer creates is also ignored.

  If one of these checks fails, the command writes nothing and says which check failed.
- A rule from another source does not count. A rule from your global git excludes file, or from
  `.git/info/exclude`, is absent from a fresh clone, so a key ignored only by such a rule could be
  committed by a colleague.
- A write changes only the managed names, keeps every other line and comment, and replaces the file in
  one step, so an interrupted write leaves the old file in place. On Linux and macOS the file is made
  readable by you only (mode `0600`). Windows has no such permission bits.
- Nothing is printed except names and results. The Scopus and OpenAlex clients register their keys with
  the logger, so a key is removed from log lines.
