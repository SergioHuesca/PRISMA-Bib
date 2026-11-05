"""A record with no ``doc_type`` (an OpenAlex work with no ``type``) must not crash the filter."""

from __future__ import annotations

from prismabib.prisma.engine import _doc_type_matches


def test_doc_type_matches__null_doc_type__fails_a_non_empty_include() -> None:
    assert _doc_type_matches(None, ["ar"]) is False


def test_doc_type_matches__null_doc_type__empty_include_is_no_restriction() -> None:
    assert _doc_type_matches(None, []) is True
