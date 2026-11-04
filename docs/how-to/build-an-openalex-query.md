# Build an OpenAlex Query

This page is for a project that has **no Scopus access**. Such a project is in sources mode
`openalex`: its records come from searching OpenAlex, and the `openalex-search` command does the
searching. If you have Scopus, use [Build a Scopus Query](build-a-query.md) instead. The two
searches are not interchangeable: a Scopus query does not carry over to OpenAlex, and the same
topic gives a different set of records in each. The methods text of your review records which
source you used and the exact query.

## What you need

1. **A project in mode `openalex`.** Create it with
   `prismabib init my-review --sources openalex --actor "Your Name"`. The mode is a
   methodological choice that is recorded; it cannot be changed after screening starts.
   The new `project.toml` already holds an empty `[openalex_query]` section with a short
   reminder of the rules. Fill in `search`; while it is empty the command refuses to run and says
   "edit the search placeholder", so nothing is sent to OpenAlex. (A project that was moved to
   mode `openalex` later gets no such section; add it as shown below.)
2. **A free OpenAlex key.** Create a free account at openalex.org, copy the key, then run
   `prismabib credentials set openalex` and paste it at the hidden prompt. The key is kept in
   your `.env` file, which git ignores. It is never printed and never stored in the captured data.
   Without a key a real run refuses to start, before any request. A `--dry-run` still works and
   says the key is not set.

A free key allows about 1,000 pages (100,000 works) per day. Each page is one request of up to
100 works and costs at most $0.001 at list price. OpenAlex resets the daily allowance at midnight
UTC.

## Write the query

Put one line in `projects/my-review/project.toml`:

```toml
[openalex_query]
search = '"carbon capture" AND policy'
```

OpenAlex searches the title, abstract, full text and keywords together, so there are no field
codes. What you can write:

| You write | Meaning |
|---|---|
| `solar wind` | Both words (words next to each other mean AND) |
| `"carbon capture"` | The exact phrase |
| `AND`, `OR`, `NOT` | The operators. They must be in **capitals** |
| `solar NOT nuclear` | `solar`, but not `nuclear` |

What is refused, with a message that says why and how to fix it:

- Parentheses, `*` and `?` wildcards, and `~`. They are not supported in this version, and they
  are refused **even inside a quoted phrase**, so a phrase such as `"what is AI?"` must be
  rephrased without the character.
- A word that starts with `-` (for example `-nuclear`). A minus sign does not exclude in
  OpenAlex. Write `NOT nuclear`.
- Scopus syntax such as `TITLE-ABS-KEY(...)`. Write the phrase itself.
- An empty query, a query that starts with `NOT`, or an operator with nothing after it.
- A query longer than 2,000 characters.
- Curly quotes (like “ ”), full-width punctuation and invisible characters, which Word and
  web pages often paste in. OpenAlex would read them as ordinary letters and silently change the
  search, so prismabib refuses them and names the character. Retype the quotes as straight `"`.
- Any other key in the `[openalex_query]` table (`filter`, `sort`, `select`, and so on).

A TOML string in single quotes (`'...'`) cannot contain an apostrophe. For a query such as
`women's health` use a double-quoted string and put a backslash before each inner double quote:

```toml
[openalex_query]
search = "women's health AND \"primary care\""
```

Lower-case `and`, `or` and `not` are ordinary words to OpenAlex, not operators, and prismabib
never rewrites them. Write them in capitals when you mean the operator.

There is no year or document-type filter in the query. Those restrictions go in `criteria.yaml`
and are applied after the search, so that the PRISMA flow diagram can count what they exclude.

## Step 1: check the cost with a dry run

```bash
prismabib openalex-search my-review --budget 30 --dry-run
```

```text
dry run: no request made, nothing written
query: "carbon capture" AND policy
query key: "carbon capture" AND policy
select: id,doi,title,publication_year,abstract_inverted_index,authorships,topics,type,primary_location,primary_topic,cited_by_count
page budget: 30 pages of 100 works
cost bound: at most $0.030
OpenAlex key: set
```

Nothing is requested and nothing is written. The cost bound is the most 30 pages can cost.

## Step 2: run it

```bash
prismabib openalex-search my-review --budget 30
```

`--budget` is required: it is the most pages this call may request. There is no `--opt-in`.
Suppose OpenAlex reports 2,350 matching works, which is 24 pages:

```text
run 20261104T140321Z-57caa7e8 sealed: 2350 works captured of 2350 reported by OpenAlex (24 page(s) this call, spent $0.024).
Next: prismabib build my-review --rebuild
```

A run is **sealed** only when every page was received and the works captured equal the count
OpenAlex reported. Run the `build` command to load it. The search command does not build the
store itself.

## The other outcomes

| The summary says | What happened | What to do |
|---|---|---|
| `sealed` | The search is complete and saved. | Run `prismabib build my-review --rebuild`. |
| `a sealed run of this query already exists` | You already captured this query. No request was made. | Nothing, or add `--refresh` to capture it again. |
| `stopped: the page budget of N is used` | Your `--budget` ran out before the last page. The run is saved but not sealed. | Run the same command again. It continues from the saved place and does not request a page twice. A bigger `--budget` finishes sooner. |
| `stopped: OpenAlex's daily budget is spent` | OpenAlex refused more requests today. The line says how long until it resets. | Run the same command again after the reset. Nothing is lost. |
| `discarded ... discard and restart` | The run can never be sealed, for example because the number of matching works changed while you waited, or OpenAlex no longer accepts the saved place. The command exits with an error status. | Run the same command again. It starts a new run. The discarded run stays on disk as a record and is never used, and the new run asks for all the pages again, so it spends the quota again. |

Because OpenAlex keeps adding works, a very large search that you spread over several days may be
discarded if the count moves. Capture a large search in as few days as the daily limit allows.

## Capture it again with `--refresh`

```bash
prismabib openalex-search my-review --budget 30 --refresh
```

This makes a new run of the same query, for example to pick up corrected metadata. It adds no new
records to the review and does not count as a second search.

## A different query

Each different query is a separate search with its own count. Change the `search` line and run the
command again. The PRISMA flow adds the counts of your distinct queries, and a work found by more
than one query is counted once and reported as a duplicate.

## Other modes

In sources mode `scopus` or `scopus+openalex` the command refuses before it reads your key or
makes any request, and says how to proceed.
