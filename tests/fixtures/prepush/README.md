# Pre-push gate fixtures

Real output of the tools the pre-push gate drives (#449), captured once with the versions
below so the parsers are tested against the real format, not a sketch. They are inputs, not
code: nothing here is imported, and the `.txt` suffix on the sample source keeps it out of
ruff, mypy and test collection.

Captured with radon 6.0.1, pylint 4.1.4, coverage 7.15.4, pytest-cov 7.1.0 on Windows
(so paths in the coverage and pylint files use backslashes: normalise before comparing).
Line endings were normalised to LF.

| File | How it was made |
| --- | --- |
| `radon_sample_source.py.txt` | Input for the radon files: a plain function, a decorated function, a class with a method and a decorated property getter/setter pair, a nested function and an async function. |
| `radon_cc.json` | `radon cc -s -j sample_module.py`, run on the sample source saved as `sample_module.py`. |
| `radon_cc.txt` | `radon cc -s sample_module.py` (the text form of the same run). |
| `coverage_report.json` | `coverage json --include='*/prismabib/ids.py,*/prismabib/progress.py'` after a unit and contract run with coverage on. Two real files: executed, missing and excluded lines are all present. |
| `pylint_duplicate_code_6.txt` | `pylint --disable=all --enable=duplicate-code --min-similarity-lines=6 src/prismabib` on main at 308bb02 (nine duplicated blocks). |
| `pylint_duplicate_code_root_init.txt` | Same command, run on a scratch package named `prismabib` (`src/prismabib/__init__.py` and `src/prismabib/other.py`, each holding the same seven-line function body at lines 4-10). It shows pylint naming the root package's own `__init__` module by the bare package name (`==prismabib:[4:11]`), not `prismabib.__init__`. The separate nine-pair capture is unchanged. |
