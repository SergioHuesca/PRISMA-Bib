# OpenAlex cassettes (synthetic)

These JSON files are **hand-written**, not recorded from api.openalex.org. They
follow the documented OpenAlex `works` list schema (`meta` + `results[]`, with
only the fields requested through `select`). DOIs, titles and people are
invented. Tests using them make no network calls.

`work_registration.json` (ADR 0029/0032 batch 3a) is the same kind of file: synthetic
and hand-built, in the OpenAlex Work shape for the registration select (`type`,
`primary_location`, `primary_topic`). It is **not** a recorded response; the ADR 0032
asked for one, and that needs a live capture.
