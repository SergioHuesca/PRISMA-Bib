"""``SyntheticCorpus`` -- the builder-over-fixtures data-shaping tool of §3.7.3 rule 7.

BUILD_PLAN §3.7.3 rule 7: "Deeply parameterised ``@pytest.fixture`` chains
become unreadable. Use ``tests/builders.py``: ``SyntheticCorpus().with_records(50)
.in_years(2016, 2026).with_languages({"English": 45, "Chinese": 5}).build()``.
The test then reads as a statement of its own premise." This module ships
exactly that builder.

It is deliberately built on top of :mod:`tests.factories` (never hand-rolling
a :class:`~prismabib.models.Record` field list itself), so a schema change
that ``tests/factories.py`` already tracks propagates here for free.

Every later stage depends on this builder (BUILD_PLAN line 746: "Every later
stage depends on these, so they ship here."). It is designed to grow rather
than be replaced: Stage 3 needs keyword/country/venue distributions, Stage 7
needs citation counts. Each dimension is its own ``with_*``/``in_*`` method
backed by its own optional field, resolved independently inside
:meth:`SyntheticCorpus.build`. Adding a new dimension means adding one more
field plus one more private ``_resolve_*`` method; it never requires
changing an existing ``with_*`` method or breaking a caller who does not use
the new one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from prismabib.models import Record
from tests.factories import RecordFactory

_DEFAULT_LANGUAGE = "English"
_DEFAULT_YEAR = 2020


@dataclass
class SyntheticCorpus:
    """Fluent builder for a synthetic corpus of :class:`~prismabib.models.Record`.

    Deliberately *mutating*, not immutable: every ``with_*``/``in_*``/
    ``seeded`` call mutates ``self`` in place and returns it, so a chain such
    as::

        SyntheticCorpus().with_records(50).in_years(2016, 2026)
                          .with_languages({"English": 45, "Chinese": 5})
                          .build()

    configures one instance rather than allocating a fresh builder at every
    step. ``build()`` is the only method that performs the actual (seeded,
    deterministic) generation; it does not mutate ``self``, so calling it
    twice on the same builder yields two separately-generated but *value-equal*
    corpora -- same configuration, same seed, same records.

    Unconfigured dimensions fall back to a fixed default (a single default
    year, a single default language, seed ``0``) rather than raising, so a
    test that only cares about record *count* can write
    ``SyntheticCorpus().with_records(10).build()`` and ignore the rest.
    """

    _record_count: int = 0
    _year_start: int | None = None
    _year_end: int | None = None
    _languages: dict[str, int] | None = None
    _seed: int = 0

    def with_records(self, count: int) -> Self:
        """Set how many records :meth:`build` will produce.

        Args:
            count: The corpus size. Must be non-negative.

        Returns:
            ``self``, for chaining.

        Raises:
            ValueError: If ``count`` is negative.
        """
        if count < 0:
            raise ValueError(f"record count must be >= 0, got {count}")
        self._record_count = count
        return self

    def in_years(self, start: int, end: int) -> Self:
        """Constrain publication years to the inclusive ``[start, end]`` range.

        Years are cycled across the built records in ascending order (record
        0 gets ``start``, record 1 gets ``start + 1``, ..., wrapping back to
        ``start`` after ``end``), so every year in the range appears as
        evenly as the configured record count allows.

        Args:
            start: The first (inclusive) publication year.
            end: The last (inclusive) publication year.

        Returns:
            ``self``, for chaining.

        Raises:
            ValueError: If ``start`` is greater than ``end``.
        """
        if start > end:
            raise ValueError(f"in_years start ({start}) must be <= end ({end})")
        self._year_start = start
        self._year_end = end
        return self

    def with_languages(self, languages: dict[str, int]) -> Self:
        """Set exact per-language record counts.

        Args:
            languages: Mapping from language name to how many records of the
                built corpus should carry it, e.g.
                ``{"English": 45, "Chinese": 5}``. The values must sum to the
                count set by :meth:`with_records` -- this builder never
                silently truncates or pads a distribution the test declared
                explicitly.

        Returns:
            ``self``, for chaining.
        """
        self._languages = dict(languages)
        return self

    def seeded(self, seed: int) -> Self:
        """Override the seed driving the underlying factory's randomness (default ``0``).

        Args:
            seed: The seed passed to the factory before generation, per
                BUILD_PLAN §3.7.3 rule 3 ("every test that touches randomness
                passes an explicit seed").

        Returns:
            ``self``, for chaining.
        """
        self._seed = seed
        return self

    def build(self) -> list[Record]:
        """Materialise the configured corpus.

        Returns:
            A list of ``with_records(...)`` :class:`~prismabib.models.Record`
            instances, each with a unique, deterministic
            ``scopus:2-s2.0-XXXXXXXXXXX`` id (BUILD_PLAN §3.2), a year drawn
            from :meth:`in_years`, and a language drawn from
            :meth:`with_languages`; every other field is filled in by the
            seeded :class:`~tests.factories.RecordFactory`.

        Raises:
            ValueError: If :meth:`with_languages` was called with counts that
                do not sum to :meth:`with_records`'s count.
        """
        count = self._record_count
        years = self._resolve_years(count)
        languages = self._resolve_languages(count)

        RecordFactory.seed_random(self._seed)

        return [
            RecordFactory.build(
                record_id=f"scopus:2-s2.0-{index:011d}",
                year=years[index],
                language=languages[index],
            )
            for index in range(count)
        ]

    def _resolve_years(self, count: int) -> list[int]:
        """Resolve the per-record year assignment for :meth:`build`."""
        if self._year_start is None or self._year_end is None:
            return [_DEFAULT_YEAR] * count
        span = list(range(self._year_start, self._year_end + 1))
        return [span[index % len(span)] for index in range(count)]

    def _resolve_languages(self, count: int) -> list[str | None]:
        """Resolve the per-record language assignment for :meth:`build`."""
        if self._languages is None:
            return [_DEFAULT_LANGUAGE] * count
        total = sum(self._languages.values())
        if total != count:
            raise ValueError(
                f"with_languages counts sum to {total}, which does not match with_records({count})"
            )
        assigned: list[str | None] = []
        for language, language_count in self._languages.items():
            assigned.extend([language] * language_count)
        return assigned
