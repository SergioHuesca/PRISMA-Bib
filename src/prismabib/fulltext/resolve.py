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
2. :class:`OpenAccessResolver` -- DOI -> OA location (Unpaywall), PDF fetch,
   verified to actually be a PDF (:func:`~prismabib.sources.unpaywall.looks_like_pdf`)
   before it is accepted -- a bare HTTP 200 is not enough, since Unpaywall's
   fallback location is routinely an HTML landing page.
3. :class:`ManualDropResolver` -- ``projects/<slug>/fulltext/manual/<record_id>.pdf``.
4. None of the above -> :func:`resolve_fulltext` returns ``(None, attempts)``.
   That is a candidate for a human to mark ``INACCESSIBLE`` during
   full-text screening (BUILD_PLAN, ADR 0019 hard rule 2) -- **never**
   something this module, or anything it calls, writes on its own. See
   :mod:`prismabib.screening` for where ``INACCESSIBLE`` may actually be
   constructed, and ``tests/unit/test_inaccessible_ast.py`` for the
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
question" (HTTP 404, no OA location, a non-PDF response, no manual file
present) -- which is the three-valued distinction ADR 0019 requires the
coverage table to be able to draw.

**A non-entitlement failure mid-chain must not discard what was already
learned, or abort the whole run.** Before this module caught only
:class:`~prismabib.errors.EntitlementError`, an ``UpstreamError`` (a 5xx
exhausting retries, or -- until :mod:`prismabib.sources.unpaywall` was
fixed alongside this -- a Cloudflare 403 retried five times and raised
anyway) from resolver *N* propagated straight out of this function,
discarding every :class:`FullTextAttempt` already collected for this record
(including a resolver 1 refusal that had just been recorded) and, one frame
up, aborting :func:`~prismabib.fulltext.capture.capture_fulltext`'s entire
loop -- leaving every later record in the run completely untried. Now, a
:class:`~prismabib.errors.PrismabibError` (other than
:class:`~prismabib.errors.EntitlementError`) or an ``httpx`` transport
failure (``httpx.TransportError`` -- connection failures, timeouts, neither
of which any client here translates into a prismabib exception) raised by a
resolver is caught, logged, and re-raised as
:class:`FullTextResolutionError`, which carries the attempts collected so
far as its own ``.attempts`` attribute. The caller
(:func:`~prismabib.fulltext.capture.capture_fulltext`) catches that one
type, persists ``.attempts`` exactly as it would a normal return, records
the record as failed, and moves on to the next record -- a resolver bug or
an upstream outage now costs one record's progress, not the run's.

**Why ``FullTextAttempt`` exists alongside ``FullTextAsset``.**
:class:`FullTextAsset` is what a resolver returns on success: the resolved
bytes plus enough metadata to place them (BUILD_PLAN's ``record_id``,
``resolver_name``, ``media_type``, ``retrieved_at`` shape for
``fulltext_assets``, minus ``path`` -- see below).
:class:`FullTextAttempt` is what :func:`resolve_fulltext` returns for
*every* resolver invoked, hit or miss -- one per resolver actually called,
per ADR 0019's "one row per attempt" reading. A resolver whose earlier
sibling in the chain already produced an asset is never called at all and
gets no row -- BUILD_PLAN's "first hit wins" is enforced by
:func:`resolve_fulltext` returning as soon as one resolver succeeds, not by
a resolver checking whether it should bother.

**Why ``FullTextAsset``/``FullTextAttempt`` carry raw ``content: bytes``,
not a ``path`` (ADR 0019 Decision 0).** Earlier, each resolver wrote its own
bytes directly into ``project.fulltext_dir/<resolver_name>/<record_id>.<ext>``
and Layer 1's ``fulltext_assets``/``fulltext_sections`` were written straight
from this module -- which made those two tables the first Layer 1 tables
that were not a function of Layer 0: deleting and rebuilding the store lost
every asset and every recorded refusal, falsifying S03-AC3. Resolvers here
now return bytes in memory and do no filesystem writes of their own (except
:class:`ManualDropResolver`'s *read* of the fixed drop-box path, which is not
a run). Placing those bytes under a sealed ``fulltext/runs/<run_id>/`` Layer 0
run -- content-addressed, alongside every attempt including refusals -- is
:mod:`prismabib.fulltext.capture`'s job; :mod:`prismabib.store.load` then
rebuilds ``fulltext_assets``/``fulltext_sections`` from those sealed runs the
same way it already rebuilds ``abstract_runs``/``record_subject_area_coverage``
from sealed abstract runs (ADR 0018). See ADR 0019 Decision 0 for the full
argument.

**Why ``FullTextResolver.resolve`` takes ``record_id``/``doi`` rather than a
full ``prismabib.models.Record``.** BUILD_PLAN's own Stage 6 sketch shows
``def resolve(self, record: Record) -> FullTextAsset | None``. Layer 1's
``Corpus.records()`` returns a Polars ``DataFrame`` (Stage 3's own,
already-shipped design), not a stream of
:class:`~prismabib.models.Record` objects, and a resolver only ever reads
two of that domain object's fields -- the record id (for the
``fulltext_assets`` key and, downstream, the asset filename) and the DOI
(the only lookup key ScienceDirect and Unpaywall both take). Threading a
full domain object through three resolvers to use two of its fields would
either force a second Layer-1-to-``Record`` assembly step this stage does
not otherwise need, or leave most of a constructed ``Record`` unused. This
is a deliberate, reported adaptation of BUILD_PLAN's sketch to this
codebase's actual Layer 1 shape, not a silent narrowing of it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import structlog

from prismabib.capture.layout import CACHE_DIRNAME
from prismabib.config import Settings
from prismabib.errors import ConfigError, EntitlementError, PrismabibError
from prismabib.sources.cache import HttpCache
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.sciencedirect import ArticleNotFoundError, ScienceDirectClient
from prismabib.sources.unpaywall import UnpaywallClient, best_oa_pdf_url, looks_like_pdf

if TYPE_CHECKING:
    from prismabib.project import Project

logger = structlog.get_logger(__name__)

#: The three resolver names, in chain order. Also the closed vocabulary of
#: ``fulltext_assets.resolver_name`` and of :mod:`prismabib.fulltext.coverage`'s
#: "by resolver" grouping.
SCIENCEDIRECT = "sciencedirect"
OPENACCESS = "openaccess"
MANUAL = "manual"

#: The relative path (under ``project.fulltext_dir``) where a reviewer drops a
#: PDF they obtained through their own institutional access. An operator
#: drop-box, not a Layer 0 run: it is mutable, permanent, and outside any
#: sealed run's immutability guarantee (ADR 0019 Decision 0) -- a resolution
#: run that reads one *copies* its bytes into its own sealed run instead of
#: recording a reference to this mutable path, so a later edit or deletion
#: here cannot retroactively change what a sealed run says it saw.
MANUAL_DROP_DIRNAME = "manual"

#: Characters this project's on-disk record ids (``scopus:2-s2.0-...``) can
#: contain that are illegal in an NTFS filename -- today, only ``:``.
#: :func:`manual_drop_path` replaces each with ``_`` so the same record id is
#: usable as a filename on every platform this project runs
#: ``full-windows`` CI for, including the working copy of this repository
#: itself (an NTFS mount, per this project's own environment notes).
_WINDOWS_ILLEGAL_FILENAME_CHARS = '<>:"/\\|?*'
_FILENAME_SANITISE_TABLE = str.maketrans(dict.fromkeys(_WINDOWS_ILLEGAL_FILENAME_CHARS, "_"))


def manual_drop_path(fulltext_dir: Path, record_id: str) -> Path:
    """Where a reviewer's manually-dropped PDF for ``record_id`` is expected to be.

    Args:
        fulltext_dir: ``project.fulltext_dir``.
        record_id: The record id, e.g. ``"scopus:2-s2.0-85100000001"``.

    Returns:
        ``<fulltext_dir>/manual/<sanitised record_id>.pdf``. BUILD_PLAN's own
        Stage 6 sketch names this path with the *literal* record id
        (``projects/<slug>/fulltext/manual/<record_id>.pdf``); it is
        sanitised here because ``record_id`` contains ``:`` (the
        ``scopus:`` namespace prefix, BUILD_PLAN §3.2), which is a legal
        POSIX filename character but not a legal NTFS one -- a project this
        codebase already develops on, and CI already tests, that path would
        raise `OSError` creating the file at all. Every character
        :data:`_WINDOWS_ILLEGAL_FILENAME_CHARS` names is replaced with
        ``_``, which keeps the record id trivially recoverable by eye
        (``scopus_2-s2.0-85100000001.pdf`` unambiguously names
        ``scopus:2-s2.0-85100000001``) without attempting a reversible
        encoding no operator would want to type by hand into a drop-box
        anyway.
    """
    sanitised = record_id.translate(_FILENAME_SANITISE_TABLE)
    return fulltext_dir / MANUAL_DROP_DIRNAME / f"{sanitised}.pdf"


@dataclass(frozen=True)
class FullTextAsset:
    """A successfully resolved full-text asset, in memory (BUILD_PLAN line 1141, ADR 0019 Decision 0).

    Attributes:
        record_id: The record this asset is for.
        resolver_name: Which resolver produced it -- one of
            :data:`SCIENCEDIRECT`, :data:`OPENACCESS`, :data:`MANUAL`.
        media_type: ``"xml"`` (ScienceDirect) or ``"pdf"`` (open access,
            manual drop).
        content: The raw fetched (or read) bytes. Not yet written anywhere --
            :mod:`prismabib.fulltext.capture` places them under a sealed Layer 0
            run, content-addressed by their own SHA-256 digest.
        retrieved_at: When this resolver produced the asset.
    """

    record_id: str
    resolver_name: str
    media_type: str
    content: bytes
    retrieved_at: datetime


@dataclass(frozen=True)
class FullTextAttempt:
    """One resolver's outcome for one record, always recorded (ADR 0019).

    Attributes:
        record_id: The record attempted.
        resolver_name: Which resolver this attempt is for.
        media_type: ``None`` when this attempt produced no asset.
        content: The fetched bytes, or ``None`` when this attempt produced no
            asset. Mirrors :attr:`FullTextAsset.content` -- not yet placed
            anywhere on disk.
        retrieved_at: When the attempt was made (success or not).
        entitled: The three-valued column ADR 0019 requires:

            - ``True`` -- an asset was obtained.
            - ``False`` -- the resolver was refused
              (:class:`~prismabib.errors.EntitlementError`). An entitlement
              gap, not an absent paper.
            - ``None`` -- not an entitlement question: no asset, and no
              refusal either (HTTP 404, no OA location, a response that came
              back 200 but was not actually a PDF, no manual file).
    """

    record_id: str
    resolver_name: str
    media_type: str | None
    content: bytes | None
    retrieved_at: datetime
    entitled: bool | None


class FullTextResolutionError(PrismabibError):
    """A resolver failed mid-chain for reasons other than an entitlement refusal.

    Not one of the named leaves in BUILD_PLAN §3.3's error tree (that tree
    predates this module, the same way :class:`~prismabib.capture.layout.SealedRunError`
    is a direct :class:`~prismabib.errors.PrismabibError` subclass outside it): this
    carries data (``attempts``) no taxonomy leaf needs to, and it exists purely to
    cross the one function boundary (:func:`resolve_fulltext` ->
    :func:`~prismabib.fulltext.capture.capture_fulltext`) where "what did we learn
    before this broke" has to survive the exception.

    Attributes:
        record_id: The record whose chain was interrupted.
        resolver_name: The resolver that raised.
        attempts: Every :class:`FullTextAttempt` collected before the failing
            resolver was reached -- exactly what :func:`resolve_fulltext` would have
            returned so far had nothing gone wrong. The caller persists these; they
            are not lost with the exception.
    """

    def __init__(
        self,
        *,
        record_id: str,
        resolver_name: str,
        attempts: tuple[FullTextAttempt, ...],
        cause: BaseException,
    ) -> None:
        super().__init__(
            f"full-text resolution for {record_id!r} was interrupted at resolver "
            f"{resolver_name!r}: {cause}"
        )
        self.record_id = record_id
        self.resolver_name = resolver_name
        self.attempts = attempts


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

    Raises:
        FullTextResolutionError: If a resolver raises anything other than
            :class:`~prismabib.errors.EntitlementError` -- a
            :class:`~prismabib.errors.PrismabibError` (an upstream 5xx
            exhausting retries, a rate limit exhausting retries, ...) or an
            ``httpx`` transport failure. Carries every :class:`FullTextAttempt`
            collected before the failure, so the caller can persist them
            rather than losing that work. See the module docstring.
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
                    content=None,
                    retrieved_at=datetime.now(UTC),
                    entitled=False,
                )
            )
            continue
        # Deliberately `Exception`, not a curated tuple. This frame owns the
        # promise that one record's failure costs one record, and the axis it
        # defends on is *scope*, not exception type: anything a resolver can
        # raise must stop at the record, or a single bad input kills the run.
        #
        # A narrower tuple was tried and was not enough. `idna.IDNAError`
        # (a `UnicodeError`, not an `httpx.TransportError`) escapes on an OA URL
        # whose host label is malformed -- a URL that arrives verbatim from
        # Unpaywall, i.e. untrusted third-party data -- and `OSError` escapes
        # from `ManualDropResolver`'s `read_bytes` when a file passes
        # `is_file()` and then fails to open. Either aborts the run *before the
        # manifest is written*, so nothing seals, every recorded refusal is
        # lost, and the resumed run dies on the same record forever.
        except Exception as exc:
            logger.warning(
                "fulltext.resolver.failed",
                record_id=record_id,
                resolver=resolver.name,
                error=str(exc),
                exc_info=True,
            )
            raise FullTextResolutionError(
                record_id=record_id,
                resolver_name=resolver.name,
                attempts=tuple(attempts),
                cause=exc,
            ) from exc

        if asset is None:
            attempts.append(
                FullTextAttempt(
                    record_id=record_id,
                    resolver_name=resolver.name,
                    media_type=None,
                    content=None,
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
                content=asset.content,
                retrieved_at=asset.retrieved_at,
                entitled=True,
            )
        )
        return asset, tuple(attempts)

    return None, tuple(attempts)


@dataclass
class ScienceDirectResolver:
    """Entitled Elsevier content via ScienceDirect Article Retrieval (``FULL`` XML).

    Args:
        client: The :class:`~prismabib.sources.sciencedirect.ScienceDirectClient`
            to use.
        name: Fixed at :data:`SCIENCEDIRECT`.
    """

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
        return FullTextAsset(
            record_id=record_id,
            resolver_name=self.name,
            media_type="xml",
            content=xml_bytes,
            retrieved_at=datetime.now(UTC),
        )


@dataclass
class OpenAccessResolver:
    """DOI -> open-access location (Unpaywall) -> PDF fetch, verified to be a PDF.

    Args:
        unpaywall_client: The :class:`~prismabib.sources.unpaywall.UnpaywallClient`
            to use.
        name: Fixed at :data:`OPENACCESS`.
    """

    unpaywall_client: UnpaywallClient
    name: str = OPENACCESS

    def resolve(self, *, record_id: str, doi: str | None) -> FullTextAsset | None:
        """See :meth:`FullTextResolver.resolve`.

        Never raises :class:`~prismabib.errors.EntitlementError`: Unpaywall
        is a public API with no entitlement concept, so an OA miss is
        always "not an entitlement question" (``entitled=None``), never a
        refusal.

        A response that comes back HTTP 200 but is not actually a PDF (an
        HTML landing page -- :func:`~prismabib.sources.unpaywall.best_oa_pdf_url`
        falls back to a generic ``url`` when no ``url_for_pdf`` exists) is
        treated exactly like "nothing found": ``None``, not an asset. Before
        this check existed, that HTML was written to disk as
        ``media_type="pdf"``/``entitled=True``, ``pdfplumber`` silently
        extracted zero sections from it, and the record was counted resolved
        forever with no full text behind it -- overstating coverage, the one
        thing ADR 0019's report must not do.
        """
        if not doi:
            return None
        response = self.unpaywall_client.lookup(doi)
        if response is None:
            return None
        pdf_url = best_oa_pdf_url(response)
        if pdf_url is None:
            return None
        content, content_type = self.unpaywall_client.fetch_bytes(pdf_url)
        if not looks_like_pdf(content, content_type):
            logger.info(
                "fulltext.resolver.openaccess.not_a_pdf",
                record_id=record_id,
                content_type=content_type,
            )
            return None
        return FullTextAsset(
            record_id=record_id,
            resolver_name=self.name,
            media_type="pdf",
            content=content,
            retrieved_at=datetime.now(UTC),
        )


@dataclass
class ManualDropResolver:
    """A human-provided PDF at :func:`manual_drop_path`.

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
        ``entitled=None``. A file present but not actually a PDF (an operator
        mistake -- the wrong file dropped, a ``.pdf``-renamed HTML save) is
        treated the same way, for the same reason
        :class:`OpenAccessResolver` verifies its own download.
        """
        # `doi` is unused: a manual drop is keyed on `record_id` alone (the
        # ADR 0019 path convention), never on the DOI. It stays a named
        # parameter -- not `**_kwargs` -- so this resolver keeps satisfying
        # `FullTextResolver` structurally, keyword for keyword.
        del doi
        path = manual_drop_path(self.fulltext_dir, record_id)
        if not path.is_file():
            return None
        content = path.read_bytes()
        if not looks_like_pdf(content, None):
            logger.warning(
                "fulltext.resolver.manual.not_a_pdf",
                record_id=record_id,
                path=str(path),
            )
            return None
        return FullTextAsset(
            record_id=record_id,
            resolver_name=self.name,
            media_type="pdf",
            content=content,
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
        project: The project resolvers read the manual drop-box under
            (``project.fulltext_dir``) and cache HTTP responses under
            (``project.fulltext_dir`` / the shared cache directory --
            **not** ``project.raw_dir``: fetched full text is licensed
            content and must never sit anywhere near the Layer 0 archive,
            per ``project.fulltext_dir``'s own docstring and ADR 0019).
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
    cache_dir = project.fulltext_dir / CACHE_DIRNAME

    try:
        sd_client = ScienceDirectClient(
            resolved_settings,
            rate_limiter=RateLimiter(),
            cache=HttpCache(cache_dir),
        )
    except ConfigError as exc:
        logger.warning("fulltext.chain.sciencedirect_unavailable", reason=str(exc))
    else:
        closers.append(sd_client)
        resolvers.append(ScienceDirectResolver(client=sd_client))

    try:
        oa_client = UnpaywallClient(
            resolved_settings,
            rate_limiter=RateLimiter(),
            cache=HttpCache(cache_dir),
        )
    except ConfigError as exc:
        logger.warning("fulltext.chain.openaccess_unavailable", reason=str(exc))
    else:
        closers.append(oa_client)
        resolvers.append(OpenAccessResolver(unpaywall_client=oa_client))

    resolvers.append(ManualDropResolver(fulltext_dir=project.fulltext_dir))

    try:
        yield tuple(resolvers)
    finally:
        for client in closers:
            client.close()


__all__ = [
    "MANUAL",
    "MANUAL_DROP_DIRNAME",
    "OPENACCESS",
    "SCIENCEDIRECT",
    "FullTextAsset",
    "FullTextAttempt",
    "FullTextResolutionError",
    "FullTextResolver",
    "ManualDropResolver",
    "OpenAccessResolver",
    "ScienceDirectResolver",
    "default_chain",
    "manual_drop_path",
    "resolve_fulltext",
]
