# Worked example

A complete PRISMA review, start to finish, that needs **no Scopus key and spends no
quota**:

```bash
uv run python examples/worked_example.py
```

Run it before pointing prismabib at your own topic. It shows what "done" looks like — an
automated eligibility funnel, human screening decisions with reason codes, and the PRISMA
2020 flow counts that come out the other end — so that when you start your own review you
already recognise the shape of it.

## What it is not

The Layer 0 archive it reads is **synthetic**: 120 manufactured records committed for
prismabib's own test suite. Real Scopus payloads are licensed and can never be
redistributed, so a runnable example has to invent its data. The records are fictional;
the pipeline that processes them is exactly the one your review will use.

It also skips `prismabib search`, because that needs your credentials and spends your
weekly quota. Everything after the search is identical either way.

## What to look at

The output is not the interesting part — the **provenance** is. Every number the example
prints is a query against the store, not a literal. Nothing is typed by hand at any point,
which is the property the whole architecture exists to protect: refresh the corpus and the
diagram updates, instead of silently disagreeing with the prose around it.

Two things worth noticing as you read the script:

- **`unsure` is reported separately and never resolves to inclusion.** A record a reviewer
  could not decide stays visible in the counts rather than being quietly folded into
  "excluded", which would overstate your exclusions in a published diagram.
- **`assert_consistent()` is called at the end.** It checks that every stage's arithmetic
  closes. That guard exists because the failure this project is built to prevent is not a
  crash — it is a plausible wrong number in a published paper.

Then run `prismabib init my-review --title "..."` and edit the two files it names. See
[docs/getting-started.md](../docs/getting-started.md).
