"""Polyfactory factories for every Pydantic model (BUILD_PLAN §746, §3.7.4).

§3.7.4: "``polyfactory`` -- typed factories generated from the Pydantic
models, factories never drift from the schema." Each factory below is a bare
``ModelFactory[...]`` subclass with only ``__model__`` set: polyfactory
introspects the model's own fields to generate values, so a field added to
(or removed from) a model in ``src/prismabib/models.py`` is automatically
picked up here without anyone hand-editing a field list. Nothing here
hand-rolls a value for a specific field -- that would be exactly the drift
this deliverable exists to prevent.

Every later stage depends on these (BUILD_PLAN line 746), so they are kept
deliberately minimal: one factory per model, no stage-specific overrides.
Stage-specific data shaping belongs in ``tests/builders.py`` (the
``SyntheticCorpus`` builder), which composes these factories rather than
duplicating their field coverage.
"""

from __future__ import annotations

from polyfactory.factories.pydantic_factory import ModelFactory

from prismabib.models import Affiliation, Author, PayloadRef, Record, Venue


class AuthorFactory(ModelFactory[Author]):
    """Factory for :class:`prismabib.models.Author`."""

    __model__ = Author


class AffiliationFactory(ModelFactory[Affiliation]):
    """Factory for :class:`prismabib.models.Affiliation`."""

    __model__ = Affiliation


class VenueFactory(ModelFactory[Venue]):
    """Factory for :class:`prismabib.models.Venue`."""

    __model__ = Venue


class PayloadRefFactory(ModelFactory[PayloadRef]):
    """Factory for :class:`prismabib.models.PayloadRef`."""

    __model__ = PayloadRef


class RecordFactory(ModelFactory[Record]):
    """Factory for :class:`prismabib.models.Record`."""

    __model__ = Record
