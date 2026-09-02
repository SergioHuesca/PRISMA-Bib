"""The Stage 6 full-text resolver chain (BUILD_PLAN lines 1121-1179, ADR 0019).

**The hazard, restated.** The ScienceDirect API serves Elsevier content
only. If ``Accessible(p)`` were implemented as "the ScienceDirect API
returned text", a corpus spanning IEEE, Springer, MDPI, and CVF/AAAI
proceedings alongside a handful of Elsevier venues would end up
Elsevier-weighted, and every downstream venue and geography statistic would
be wrong -- not merely incomplete, wrong, because the paper would look
under-represented rather than un-fetched.

**The chain, first hit wins.**

1. :class:`ScienceDirectResolver` -- entitled Elsevier content, XML via
   Article Retrieval.
2. :class:`OpenAccessResolver` -- DOI -> OA location (Unpaywall), PDF fetch.
3. :class:`ManualDropResolver` -- ``projects/<slug>/fulltext/manual/<record_id>.pdf``.
4. None of the above -> :func:`resolve_fulltext` returns ``(None, attempts)``.
   That is a candidate for a human to mark ``INACCESSIBLE`` during
   full-text screening (BUILD_PLAN, ADR 0019 hard rule 2) -- **never**
   something this module, or anything it calls, writes on its own. See
   :mod:`prismabib.screening` for where ``INACCESSIBLE`` may actually be
   constructed, and ``tests/unit/fulltext/test_inaccessible_ast.py`` for the
   architectural test that enforces it.

**Hard rule 1, and how this module keeps it.** BUILD_PLAN: "A 403 from
ScienceDirect records ``entitled = false`` and moves to the next resolver;
it does not mark the record inaccessible." :class:`FullTextResolver.resolve`
signals a refusal by *raising*
:class:`~prismabib.errors.EntitlementError` -- exactly the exception
:class:`~prismabib.sources.sciencedirect.ScienceDirectClient` already raises
on HTTP 403 -- rather than folding it into the ``FullTextAsset | None``
return type. :func:`resolve_fulltext` is the **one place** in this module
that is allowed to catch it, and it always does: an
:class:`~prismabib.errors.EntitlementError` from resolver *N* is recorded
as one :class:`FullTextAttempt` with ``entitled=False`` and the loop moves
to resolver *N+1* unconditionally. A resolver returning plain ``None``
(no exception) records ``entitled=None`` instead -- "not an entitlement
question" (HTTP 404, no OA location, no manual file present) -- which is
the three-valued distinction ADR 0019 requires the coverage table to be
able to draw.

**Why ``FullTextAttempt`` exists alongside ``FullTextAsset``.**
:class:`FullTextAsset` is BUILD_PLAN's own shape (record_id, resolver_name,
media_type, path, retrieved_at) for a *successful* resolution.
:class:`FullTextAttempt` is the row :mod:`prismabib.cli`'s ``fulltext``
command actually persists to ``fulltext_assets`` -- one per resolver
*invoked*, hit or miss, per ADR 0019's "one row per attempt" reading. A
resolver whose earlier sibling in the chain already produced an asset is
never called at all and gets no row -- BUILD_PLAN's "first hit wins" is
enforced by :func:`resolve_fulltext` returning as soon as one resolver
succeeds, not by a resolver checking whether it should bother.

**Why ``FullTextResolver.resolve`` takes ``record_id``/``doi`` rather than a
full ``prismabib.models.Record``.** BUILD_PLAN's own Stage 6 sketch shows
``def resolve(self, record: Record) -> FullTextAsset | None``. Layer 1's
``Corpus.records()`` returns a Polars ``DataFrame`` (Stage 3's own,
already-shipped design), not a stream of
:class:`~prismabib.models.Record` objects, and a resolver only ever reads
two of that domain object's fields -- the record id (for filenames and the
``fulltext_assets`` key) and the DOI (the only lookup key ScienceDirect and
Unpaywall both take). Threading a full domain object through three
resolvers to use two of its fields would either force a second
Layer-1-to-``Record`` assembly step this stage does not otherwise need, or
leave most of a constructed ``Record`` unused. This is a deliberate,
reported adaptation of BUILD_PLAN's sketch to this codebase's actual Layer 1
shape, not a silent narrowing of it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import structlog

from prismabib.config import Settings
from prismabib.errors import ConfigError, EntitlementError
from prismabib.sources.cache import HttpCache
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.sciencedirect import ArticleNotFoundError, ScienceDirectClient
from prismabib.sources.unpaywall import UnpaywallClient, best_oa_pdf_url

if TYPE_CHECKING:
    from prismabib.project import Project

logger = structlog.get_logger(__name__)

#: The three resolver names, in chain order. Also the closed vocabulary of
#: ``fulltext_assets.resolver_name`` and of :mod:`prismabib.fulltext.coverage`'s
#: "by resolver" grouping.
SCIENCEDIRECT = "sciencedirect"
OPENACCESS = "openaccess"
MANUAL = "manual"


@dataclass(frozen=True)
class FullTextAsset:
    """A successfully resolved full-text asset (BUILD_PLAN line 1141).

    Attributes:
        record_id: The record this asset is for.
        resolver_name: Which resolver produced it -- one of
            :data:`SCIENCEDIRECT`, :data:`OPENACCESS`, :data:`MANUAL`.
        media_type: ``"xml"`` (ScienceDirect) or ``"pdf"`` (open access,
            manual drop).
        path: Where the raw bytes were written, under
            ``project.fulltext_dir`` -- never committed (guard-blocked by
            ``projects/*/fulltext/`` in ``.gitignore``).
        retrieved_at: When this resolver produced the asset.
    """

    record_id: str
    resolver_name: str
    media_type: str
    path: Path
    retrieved_at: datetime


@dataclass(frozen=True)
class FullTextAttempt:
    """One ``fulltext_assets`` row: a resolver's outcome for one record, always recorded.

    Attributes:
        record_id: The record attempted.
        resolver_name: Which resolver this attempt is for.
        media_type: ``None`` when this attempt produced no asset.
        path: ``None`` when this attempt produced no asset.
        retrieved_at: When the attempt was made (success or not).
        entitled: The three-valued column ADR 0019 requires:

            - ``True`` -- an asset was obtained.
            - ``False`` -- the resolver was refused
              (:class:`~prismabib.errors.EntitlementError`). An entitlement
              gap, not an absent paper.
            - ``None`` -- not an entitlement question: no asset, and no
              refusal either (HTTP 404, no OA location, no manual file).
    """

    record_id: str
    resolver_name: str
    media_type: str | None
    path: Path | None
    retrieved_at: datetime
    entitled: bool | None


class FullTextResolver(Protocol):
    """One step of the Stage 6 resolver chain.

    Attributes:
        name: This resolver's name -- one of :data:`SCIENCEDIRECT`,
            :data:`OPENACCESS`, :data:`MANUAL` for the three BUILD_PLAN
            resolvers, persisted verbatim as ``fulltext_assets.resolver_name``.
    """

    name: str

    def resolve(self, *, record_id: str, doi: str | None) -> FullTextAsset | None:
        """Attempt to obtain full text for one record.

        Args:
            record_id: The record to resolve.
            doi: The record's DOI, or ``None`` if it has none. A resolver
                that cannot function without a DOI (ScienceDirect, open
                access) simply returns ``None`` when it is missing, exactly
                as it would for "no result found" -- a record with no DOI
                is not owed a network call it cannot possibly succeed at.

        Returns:
            A :class:`FullTextAsset` on success, or ``None`` when this
            resolver found nothing for this record and that absence is
            *not* an entitlement question.

        Raises:
            EntitlementError: When this resolver was refused access it
                would otherwise be able to use (BUILD_PLAN hard rule 1;
                currently only meaningful for :class:`ScienceDirectResolver`,
                whose underlying client raises this on HTTP 403). Caught
                only by :func:`resolve_fulltext`, never by a resolver
                itself and never re-raised as anything else.
        """
        ...


def resolve_fulltext(
    *, record_id: str, doi: str | None, resolvers: Sequence[FullTextResolver]
) -> tuple[FullTextAsset | None, tuple[FullTextAttempt, ...]]:
    """Run the resolver chain for one record, first hit wins.

    Args:
        record_id: The record to resolve.
        doi: The record's DOI, or ``None``.
        resolvers: The chain, in order. A resolver is invoked only until
            one produces an asset; every resolver after that point is
            never called and contributes no :class:`FullTextAttempt`
            (BUILD_PLAN: "first hit wins").

    Returns:
        ``(asset, attempts)``. ``asset`` is the first resolver's
        :class:`FullTextAsset`, or ``None`` if every resolver in
        ``resolvers`` was tried and none produced one -- exhaustion, which
        is a fact about *this run*, not a verdict (ADR 0019 hard rule 2):
        no decision event is written here or anywhere this function calls.
        ``attempts`` is one :class:`FullTextAttempt` per resolver actually
        invoked, in chain order -- BUILD_PLAN's per-record provenance and
        the raw material for :mod:`prismabib.fulltext.coverage`.
    """
    attempts: list[FullTextAttempt] = []
    for resolver in resolvers:
        try:
            asset = resolver.resolve(record_id=record_id, doi=doi)
        except EntitlementError:
            logger.info(
                "fulltext.resolver.entitlement_refused",
                record_id=record_id,
                resolver=resolver.name,
            )
            attempts.append(
                FullTextAttempt(
                    record_id=record_id,
                    resolver_name=resolver.name,
                    media_type=None,
                    path=None,
                    retrieved_at=datetime.now(UTC),
                    entitled=False,
                )
            )
            continue

        if asset is None:
            attempts.append(
                FullTextAttempt(
                    record_id=record_id,
                    resolver_name=resolver.name,
                    media_type=None,
                    path=None,
                    retrieved_at=datetime.now(UTC),
                    entitled=None,
                )
            )
            continue

        logger.info(
            "fulltext.resolver.resolved",
            record_id=record_id,
            resolver=resolver.name,
            media_type=asset.media_type,
        )
        attempts.append(
            FullTextAttempt(
                record_id=record_id,
                resolver_name=resolver.name,
                media_type=asset.media_type,
                path=asset.path,
                retrieved_at=asset.retrieved_at,
                entitled=True,
            )
        )
        return asset, tuple(attempts)

    return None, tuple(attempts)


def _asset_path(fulltext_dir: Path, resolver_name: str, record_id: str, extension: str) -> Path:
    """Where a resolver writes (or reads) one record's fetched bytes.

    Args:
        fulltext_dir: ``project.fulltext_dir``.
        resolver_name: The resolver's :attr:`FullTextResolver.name`.
        record_id: The record id.
        extension: File extension, without the dot (``"xml"``, ``"pdf"``).

    Returns:
        ``<fulltext_dir>/<resolver_name>/<record_id>.<extension>``.
    """
    return fulltext_dir / resolver_name / f"{record_id}.{extension}"


@dataclass
class ScienceDirectResolver:
    """Entitled Elsevier content via ScienceDirect Article Retrieval (``FULL`` XML).

    Args:
        fulltext_dir: ``project.fulltext_dir``; resolved XML is written
            under ``<fulltext_dir>/sciencedirect/``.
        client: The :class:`~prismabib.sources.sciencedirect.ScienceDirectClient`
            to use.
        name: Fixed at :data:`SCIENCEDIRECT`.
    """

    fulltext_dir: Path
    client: ScienceDirectClient
    name: str = SCIENCEDIRECT

    def resolve(self, *, record_id: str, doi: str | None) -> FullTextAsset | None:
        """See :meth:`FullTextResolver.resolve`.

        Raises:
            EntitlementError: On HTTP 403 -- propagates untranslated so
                :func:`resolve_fulltext` can record ``entitled=False`` and
                continue the chain. This is the one path BUILD_PLAN hard
                rule 1 exists for.
        """
        if not doi:
            return None
        try:
            xml_bytes = self.client.article_retrieval_xml(doi)
        except ArticleNotFoundError:
            return None
        path = _asset_path(self.fulltext_dir, self.name, record_id, "xml")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(xml_bytes)
        return FullTextAsset(
            record_id=record_id,
            resolver_name=self.name,
            media_type="xml",
            path=path,
            retrieved_at=datetime.now(UTC),
        )


@dataclass
class OpenAccessResolver:
    """DOI -> open-access location (Unpaywall) -> PDF fetch.

    Args:
        fulltext_dir: ``project.fulltext_dir``; fetched PDFs are written
            under ``<fulltext_dir>/openaccess/``.
        unpaywall_client: The :class:`~prismabib.sources.unpaywall.UnpaywallClient`
            to use.
        name: Fixed at :data:`OPENACCESS`.
    """

    fulltext_dir: Path
    unpaywall_client: UnpaywallClient
    name: str = OPENACCESS

    def resolve(self, *, record_id: str, doi: str | None) -> FullTextAsset | None:
        """See :meth:`FullTextResolver.resolve`.

        Never raises :class:`~prismabib.errors.EntitlementError`: Unpaywall
        is a public API with no entitlement concept, so an OA miss is
        always "not an entitlement question" (``entitled=None``), never a
        refusal.
        """
        if not doi:
            return None
        response = self.unpaywall_client.lookup(doi)
        if response is None:
            return None
        pdf_url = best_oa_pdf_url(response)
        if pdf_url is None:
            return None
        content = self.unpaywall_client.fetch_bytes(pdf_url)
        path = _asset_path(self.fulltext_dir, self.name, record_id, "pdf")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return FullTextAsset(
            record_id=record_id,
            resolver_name=self.name,
            media_type="pdf",
            path=path,
            retrieved_at=datetime.now(UTC),
        )


@dataclass
class ManualDropResolver:
    """A human-provided PDF at ``projects/<slug>/fulltext/manual/<record_id>.pdf``.

    The chain's last resort: a reviewer with institutional access outside
    prismabib's own resolvers can drop a PDF here and it is picked up on
    the next run, no code change required.

    Args:
        fulltext_dir: ``project.fulltext_dir``.
        name: Fixed at :data:`MANUAL`.
    """

    fulltext_dir: Path
    name: str = MANUAL

    def resolve(self, *, record_id: str, doi: str | None) -> FullTextAsset | None:
        """See :meth:`FullTextResolver.resolve`.

        Never raises :class:`~prismabib.errors.EntitlementError`: reading a
        local file has no entitlement concept, so a missing drop is always
        ``entitled=None``.
        """
        # `doi` is unused: a manual drop is keyed on `record_id` alone (the
        # ADR 0019 path convention), never on the DOI. It stays a named
        # parameter -- not `**_kwargs` -- so this resolver keeps satisfying
        # `FullTextResolver` structurally, keyword for keyword.
        del doi
        path = _asset_path(self.fulltext_dir, self.name, record_id, "pdf")
        if not path.is_file():
            return None
        return FullTextAsset(
            record_id=record_id,
            resolver_name=self.name,
            media_type="pdf",
            path=path,
            retrieved_at=datetime.now(UTC),
        )


@contextmanager
def default_chain(
    project: Project, settings: Settings | None = None
) -> Iterator[tuple[FullTextResolver, ...]]:
    """Build BUILD_PLAN's standard three-resolver chain for one project.

    Degrades gracefully rather than refusing outright: a researcher with no
    Elsevier entitlement at all still gets open access and manual drop, and
    one with no ``UNPAYWALL_EMAIL`` set still gets ScienceDirect and manual
    drop. :class:`ManualDropResolver` is unconditional -- it needs no
    credential and costs no network call to construct.

    Args:
        project: The project resolvers write fetched bytes under
            (``project.fulltext_dir``).
        settings: The environment configuration. Defaults to
            ``Settings()`` when omitted.

    Yields:
        The chain, in BUILD_PLAN order (ScienceDirect, open access, manual
        drop, omitting whichever of the first two lack their credential).

    Raises:
        ConfigError: If ``settings`` is omitted and the environment cannot
            be loaded (``SCOPUS_API_KEY`` is unconditionally required by
            :class:`~prismabib.config.Settings`, regardless of this
            function's own, narrower needs).
    """
    resolved_settings = settings if settings is not None else Settings()
    resolvers: list[FullTextResolver] = []
    closers: list[ScienceDirectClient | UnpaywallClient] = []

    try:
        sd_client = ScienceDirectClient(
            resolved_settings,
            rate_limiter=RateLimiter(),
            cache=HttpCache(project.raw_dir / "_cache"),
        )
    except ConfigError as exc:
        logger.warning("fulltext.chain.sciencedirect_unavailable", reason=str(exc))
    else:
        closers.append(sd_client)
        resolvers.append(ScienceDirectResolver(fulltext_dir=project.fulltext_dir, client=sd_client))

    try:
        oa_client = UnpaywallClient(
            resolved_settings,
            rate_limiter=RateLimiter(),
            cache=HttpCache(project.raw_dir / "_cache"),
        )
    except ConfigError as exc:
        logger.warning("fulltext.chain.openaccess_unavailable", reason=str(exc))
    else:
        closers.append(oa_client)
        resolvers.append(
            OpenAccessResolver(fulltext_dir=project.fulltext_dir, unpaywall_client=oa_client)
        )

    resolvers.append(ManualDropResolver(fulltext_dir=project.fulltext_dir))

    try:
        yield tuple(resolvers)
    finally:
        for client in closers:
            client.close()


__all__ = [
    "MANUAL",
    "OPENACCESS",
    "SCIENCEDIRECT",
    "FullTextAsset",
    "FullTextAttempt",
    "FullTextResolver",
    "ManualDropResolver",
    "OpenAccessResolver",
    "ScienceDirectResolver",
    "default_chain",
    "resolve_fulltext",
]
