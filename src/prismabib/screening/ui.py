"""The Panel screening view (BUILD_PLAN §Stage 5, lines 1063-1092).

The half of screening a reviewer actually touches, for hours. BUILD_PLAN is
blunt about why it matters: *"Screening is the project's critical path; UI
ergonomics determine whether the review is ever finished."* A review of a
thousand records is rarely lost to a missing feature; it is lost to friction.

**Two of the requirements here are bias controls, not conveniences.** The
queue presents records in an order seeded from the project slug and
uncorrelated with anything (:mod:`prismabib.screening.queue`), and this module
omits author names and citation counts unless the reviewer opts out. Both
exist so that a human-only protocol stays human-only: a ranked queue imports
the ranker's judgement into the review, and a visible citation count imports
the field's.

**The keyboard path is data, end to end.** :data:`KEY_MAP` is the single
source of truth for what every key does. Three things are built from it and
nothing else: the JavaScript that listens for keystrokes
(:func:`keymap_javascript`), the help text, and :meth:`Screener.handle_key`'s
dispatch. That is what makes BUILD_PLAN's *"no dead keys; the map is data, so
it is assertable"* true rather than aspirational -- a binding whose action has
no handler fails a test, and a handler no key reaches fails the same test.

The delivery mechanism is a document-level ``keydown`` listener, not a
widget-level one, because BUILD_PLAN's acceptance criterion is that "keyboard
bindings work **without the mouse entering the widget**". A listener attached
to a focusable Panel widget would satisfy the letter of "keyboard-first" and
fail its purpose: the reviewer would have to click the widget after every
scroll. The listener sets one parameter on a hidden
:class:`~panel.reactive.ReactiveHTML` component, Panel syncs it back to the
kernel, and :meth:`Screener.handle_key` takes it from there -- so everything
downstream of the browser event is ordinary Python and is tested as such.

**Why the module is shaped like this.** Everything a test can meaningfully
assert -- the view model, the pace arithmetic, the reason palette, the key map
and its dispatch -- is a plain structure or a pure function. Panel widgets are
built *from* those and asserted on only to the extent that "it constructs
headlessly" is a real claim. §3.7.6 sets this module's line gate to 60% and
BUILD_PLAN says why in as many words: *"Resist the temptation to write
assertion-free render tests to lift the coverage number."*

Browser interaction, keyboard event *delivery* and visual layout are
deliberately untested (BUILD_PLAN's "Deliberately not tested here"); a
Playwright smoke test is deferred in §8. What is tested is everything on this
side of the wire.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Final

import panel as pn
import param
from panel.reactive import ReactiveHTML

from prismabib.errors import PrismabibError, ValidationError
from prismabib.screening.queue import screening_queue
from prismabib.stage import PrismaStage
from prismabib.store.db import connect

if TYPE_CHECKING:
    from prismabib.prisma.events import Decision
    from prismabib.project import Criteria, Project
    from prismabib.screening.queue import ScreeningQueue

# ---------------------------------------------------------------------------
# What the reviewer is shown
# ---------------------------------------------------------------------------

#: The fields a reviewer sees for every record, in reading order. BUILD_PLAN
#: line 1074 fixes this list: "Title, abstract, venue, year, doc type, author
#: keywords. Nothing else." The restriction *is* the feature -- every extra
#: field is one more cue that is not an eligibility criterion.
VISIBLE_FIELDS: Final[tuple[str, ...]] = (
    "title",
    "abstract",
    "venue",
    "year",
    "doc_type",
    "keywords",
)

#: Shown only when ``blind=False``. Author names carry institutional and
#: seniority signal; citation counts carry the field's own prior about which
#: papers matter. Neither is an eligibility criterion and both are known to
#: move human judgement, so they are absent by default rather than merely
#: de-emphasised.
UNBLINDED_FIELDS: Final[tuple[str, ...]] = ("authors", "cited_by")


@dataclass(frozen=True)
class ScreeningRecord:
    """One record's screenable content, read from Layer 1 once per session.

    Holds the blinded fields *and* the blinded-away ones: the bias control is
    applied by :func:`record_view_model`, in one place, where a test can see
    it. A record type that simply never carried ``authors`` would make
    blinding untestable -- the assertion would pass against an empty column
    just as happily as against a working control.

    Attributes:
        record_id: Layer 1's ``records.record_id``.
        title: ``records.title``.
        abstract: ``records.abstract``; ``None`` when the source carried none.
        venue: ``venues.name``.
        year: ``records.year``.
        doc_type: ``records.doc_type``.
        keywords: Author keywords, alphabetical by normalised term.
        authors: Display names, in the source's author order.
        cited_by: The most recent ``citation_snapshots.cited_by_count``,
            or ``None`` when the record has no snapshot.
    """

    record_id: str
    title: str | None = None
    abstract: str | None = None
    venue: str | None = None
    year: int | None = None
    doc_type: str | None = None
    keywords: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()
    cited_by: int | None = None


def record_view_model(record: ScreeningRecord, *, blind: bool = True) -> dict[str, Any]:
    """Everything the reviewer is shown for one record, as a plain dict.

    A dict rather than a widget so the bias control is testable without a
    browser: BUILD_PLAN's test table asks specifically for an assertion on
    "the **view model dict**, not rendered HTML".

    Args:
        record: The record to describe.
        blind: When ``True`` (the default), author names and citation counts
            are *absent from the returned mapping*, not blanked and not styled
            away. A field hidden in CSS is one inspect-element from being
            seen, and would pass a test that searched the rendered text for
            it; a field that was never put in the model cannot be rendered by
            any later mistake.

    Returns:
        A mapping whose keys are exactly :data:`VISIBLE_FIELDS`, plus
        :data:`UNBLINDED_FIELDS` when ``blind`` is ``False``. ``record_id`` is
        deliberately not among them: it is identity, not content, and the view
        shows it in the status line where it cannot be mistaken for a
        bibliographic cue.
    """
    model: dict[str, Any] = {
        "title": record.title,
        "abstract": record.abstract,
        "venue": record.venue,
        "year": record.year,
        "doc_type": record.doc_type,
        "keywords": list(record.keywords),
    }
    if not blind:
        model["authors"] = list(record.authors)
        model["cited_by"] = record.cited_by
    return model


def record_markdown(model: Mapping[str, Any]) -> str:
    """Render one record's view model as the markdown the reviewer reads.

    Args:
        model: A mapping from :func:`record_view_model`.

    Returns:
        Markdown. Only keys *present* in ``model`` are rendered, so blinding
        is enforced by the model's shape rather than by a second decision
        here: there is no branch in this function that could disagree with the
        one that made the model.
    """
    lines = [f"### {model.get('title') or '(no title)'}", ""]

    meta = " · ".join(str(model[key]) for key in ("venue", "year", "doc_type") if model.get(key))
    if meta:
        lines += [f"*{meta}*", ""]

    if model.get("authors"):
        lines += ["**authors:** " + ", ".join(str(name) for name in model["authors"]), ""]
    if model.get("cited_by") is not None:
        lines += [f"**cited by:** {model['cited_by']}", ""]

    lines += [str(model.get("abstract") or "*(no abstract recorded)*"), ""]
    if model.get("keywords"):
        lines += ["**keywords:** " + ", ".join(str(term) for term in model["keywords"])]
    return "\n".join(lines)


def load_records(project: Project, record_ids: Iterable[str]) -> dict[str, ScreeningRecord]:
    """Read every screenable field for ``record_ids`` from Layer 1, in one pass.

    Read once per session rather than per keystroke. A screening session is a
    long sequence of navigations over a fixed set of records, and reopening
    the store on each one would put a file open, four queries and a close
    between the reviewer's keystroke and the next title -- the exact friction
    BUILD_PLAN's ergonomics requirement is about. A thousand abstracts is a
    few megabytes.

    Args:
        project: The project whose Layer 1 store to read.
        record_ids: The records to load, in any order.

    Returns:
        ``{record_id: ScreeningRecord}`` for every requested id present in
        Layer 1. An id with no row is simply absent from the result.

    Raises:
        StoreError: If no Layer 1 store exists yet for ``project``.
    """
    wanted = sorted(set(record_ids))
    if not wanted:
        return {}

    connection = connect(project, read_only=True)
    try:
        rows = connection.execute(
            "SELECT r.record_id, r.title, r.abstract, r.year, r.doc_type, v.name "
            "FROM records r LEFT JOIN venues v ON v.venue_id = r.venue_id "
            "WHERE r.record_id = ANY(?)",
            [wanted],
        ).fetchall()
        keyword_rows = connection.execute(
            "SELECT rk.record_id, k.term_raw FROM record_keywords rk "
            "JOIN keywords k ON k.keyword_id = rk.keyword_id "
            "WHERE rk.kind = 'author' AND rk.record_id = ANY(?) "
            "ORDER BY rk.record_id, k.term_norm",
            [wanted],
        ).fetchall()
        author_rows = connection.execute(
            "SELECT ra.record_id, a.surname, a.given_name FROM record_authors ra "
            "JOIN authors a ON a.author_id = ra.author_id "
            "WHERE ra.record_id = ANY(?) ORDER BY ra.record_id, ra.position",
            [wanted],
        ).fetchall()
        citation_rows = connection.execute(
            "SELECT record_id, cited_by_count FROM ("
            "SELECT record_id, cited_by_count, ROW_NUMBER() OVER "
            "(PARTITION BY record_id ORDER BY retrieved_at DESC) AS rn "
            "FROM citation_snapshots WHERE record_id = ANY(?)) WHERE rn = 1",
            [wanted],
        ).fetchall()
    finally:
        connection.close()

    keywords: dict[str, list[str]] = {}
    for record_id, term in keyword_rows:
        keywords.setdefault(record_id, []).append(term)

    authors: dict[str, list[str]] = {}
    for record_id, surname, given_name in author_rows:
        authors.setdefault(record_id, []).append(_display_name(surname, given_name))

    cited_by: dict[str, int] = dict(citation_rows)

    return {
        record_id: ScreeningRecord(
            record_id=record_id,
            title=title,
            abstract=abstract,
            venue=venue,
            year=year,
            doc_type=doc_type,
            keywords=tuple(keywords.get(record_id, ())),
            authors=tuple(authors.get(record_id, ())),
            cited_by=cited_by.get(record_id),
        )
        for record_id, title, abstract, year, doc_type, venue in rows
    }


def _display_name(surname: str | None, given_name: str | None) -> str:
    """Format one author's stored name parts for display.

    Args:
        surname: ``authors.surname``.
        given_name: ``authors.given_name``, often ``NULL``.

    Returns:
        ``"Surname, Given"``, or just the surname when no given name was
        captured -- never a dangling comma, which is what a plain SQL
        concatenation of a nullable column produces.
    """
    if surname and given_name:
        return f"{surname}, {given_name}"
    return surname or given_name or "(unnamed)"


# ---------------------------------------------------------------------------
# Progress and pace (BUILD_PLAN line 1073)
# ---------------------------------------------------------------------------


def progress_view_model(
    *,
    decided: int,
    total: int,
    session_decisions: int,
    session_seconds: float,
) -> dict[str, Any]:
    """Decided/total, pace, and estimated time remaining.

    BUILD_PLAN line 1073 insists this "is not decoration -- it is what sustains
    a multi-hour task", so it is arithmetic in a pure function rather than a
    format string inside a widget callback.

    **Pace is measured over this session, not over the log.** ``decided``
    counts every decision this reviewer has ever made at this stage, because
    that is what progress means; dividing *it* by the time this session has
    been open would report the whole review's work as though it had happened
    since the notebook was opened. Re-opening at record 900 of 1110 would then
    claim hundreds of decisions per minute and an ETA of seconds -- and the
    number nobody can sanity-check is the one that stops being looked at.

    Args:
        decided: Records this reviewer has resolved at this stage in total
            (``ScreeningQueue.decided``).
        total: Records eligible at this stage (``ScreeningQueue.total``).
        session_decisions: Resolving decisions made since this view was
            constructed.
        session_seconds: Seconds elapsed since the first of them.

    Returns:
        ``decided``/``total``/``remaining``/``per_minute``/``eta_minutes``.
        ``per_minute`` and ``eta_minutes`` are ``None`` until there is
        something to measure -- a rate over zero decisions is not a slow rate,
        and printing ``0.0/min`` beside an infinite ETA would tell a reviewer
        starting a session that they will never finish.
    """
    remaining = total - decided
    per_minute: float | None = None
    eta_minutes: float | None = None

    if session_decisions > 0 and session_seconds > 0:
        per_minute = session_decisions / (session_seconds / 60.0)
        eta_minutes = remaining / per_minute

    return {
        "decided": decided,
        "total": total,
        "remaining": remaining,
        "per_minute": per_minute,
        "eta_minutes": eta_minutes,
    }


def progress_markdown(model: Mapping[str, Any]) -> str:
    """Render a progress view model as the one line the reviewer glances at.

    Args:
        model: A mapping from :func:`progress_view_model`.

    Returns:
        Markdown: counts always, pace and ETA only once they exist.
    """
    head = f"**{model['decided']} / {model['total']}** decided · {model['remaining']} to go"
    if model["per_minute"] is None:
        return f"{head} · pace —"
    return f"{head} · {model['per_minute']:.1f}/min · ~{model['eta_minutes']:.0f} min left"


# ---------------------------------------------------------------------------
# The reason palette (BUILD_PLAN line 1077)
# ---------------------------------------------------------------------------

#: The digits BUILD_PLAN line 1077 numbers the reason palette with. Nine, not
#: ten: ``0`` would have to mean "the tenth", and a reviewer who has learnt
#: "the digit is the position" would file the wrong reason the first time they
#: used it.
REASON_DIGITS: Final[tuple[str, ...]] = tuple(str(digit) for digit in range(1, 10))


def reason_palette(criteria: Criteria, stage: PrismaStage) -> dict[str, str]:
    """The numbered exclusion reasons for ``stage``, taken from ``criteria.yaml``.

    Read from the criteria file rather than hard-coded, so adding a reason
    code to a protocol surfaces it in the UI with no code change (BUILD_PLAN
    line 1077). This matters beyond convenience: the log refuses an exclusion
    whose code is not declared there, so a hard-coded palette would eventually
    offer a button that cannot be used, and omit the code that must be.

    Args:
        criteria: The project's parsed ``criteria.yaml``.
        stage: Which screening stage's vocabulary to use.

    Returns:
        ``{digit: reason_code}`` in the order the criteria file declares them,
        for at most the first nine codes. A tenth declared code is reachable
        by mouse but has no digit; see :data:`REASON_DIGITS`.

    Raises:
        ValidationError: If ``stage`` is not one of the two human-screened
            stages -- the computed stages have no exclusion vocabulary, so
            asking for one is a programming error, not an empty palette.
    """
    if stage is PrismaStage.TITLE_ABSTRACT:
        codes = criteria.manual_abstract.exclude_reason_codes
    elif stage is PrismaStage.FULLTEXT:
        codes = criteria.manual_fulltext.exclude_reason_codes
    else:
        raise ValidationError(
            f"stage {stage.value!r} is not a screening stage; only "
            f"{PrismaStage.TITLE_ABSTRACT.value!r} and {PrismaStage.FULLTEXT.value!r} "
            "have exclusion reason codes."
        )
    return dict(zip(REASON_DIGITS, codes, strict=False))


# ---------------------------------------------------------------------------
# The key map (BUILD_PLAN line 1071)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyBinding:
    """One keyboard binding: the key, the handler it reaches, and its help line.

    Attributes:
        key: The ``KeyboardEvent.key`` value the reviewer presses.
        action: The name of the :class:`Screener` handler it invokes. Not a
            callable: the map has to be inspectable data (BUILD_PLAN's "the
            map is data, so it is assertable"), and a name can be checked
            against the handlers a screener actually exposes.
        help: One line, shown by ``?``.
    """

    key: str
    action: str
    help: str


#: Every binding, and the only place they are declared. BUILD_PLAN line 1071
#: fixes the letters: ``i`` include, ``e`` exclude then a digit for the reason,
#: ``u`` unsure, ``n``/``p`` next/previous, ``?`` help; line 1078 adds ``z``
#: for undo.
#:
#: The digits are bindings like any other, rather than a special case inside
#: the key handler, so that "no dead keys" covers them too: a palette digit
#: with no handler would be a key that silently does nothing at the one moment
#: the reviewer is mid-exclusion.
KEY_MAP: Final[tuple[KeyBinding, ...]] = (
    KeyBinding("i", "include", "include this record"),
    KeyBinding("e", "begin_exclude", "exclude — then a digit for the reason"),
    KeyBinding("u", "unsure", "unsure; the record stays in the queue"),
    KeyBinding("n", "next_record", "next record"),
    KeyBinding("p", "previous_record", "previous record"),
    KeyBinding("z", "undo", "undo the last decision and step back"),
    KeyBinding("?", "show_help", "show or hide this help"),
    *(
        KeyBinding(digit, f"reason_{digit}", f"exclusion reason {digit} (after e)")
        for digit in REASON_DIGITS
    ),
)

#: Separates the key from a monotonic counter in the value the browser syncs
#: back to the kernel. Panel only notifies Python when a parameter's value
#: *changes*, so pressing ``i`` twice in a row would deliver one event; the
#: counter makes every keystroke a distinct value. ASCII unit separator, so it
#: cannot collide with a key name.
KEYSTROKE_SEPARATOR: Final[str] = "\x1f"


def keymap_javascript(bindings: Sequence[KeyBinding]) -> str:
    """Generate the document-level ``keydown`` listener for ``bindings``.

    Generated from the same :data:`KEY_MAP` the Python dispatch reads, so the
    browser and the kernel cannot drift: adding a binding adds it to both.

    The listener is attached to ``document``, with ``capture``, which is what
    makes BUILD_PLAN's "without the mouse entering the widget" true -- a
    listener on the widget would need focus, and the reviewer would have to
    click after every scroll. It stands down inside text inputs so that typing
    a note is typing, and it removes a previously installed listener first, so
    re-executing the notebook cell does not double-fire every keystroke.

    Args:
        bindings: The key map to render.

    Returns:
        JavaScript, evaluated by Panel in the ``render`` lifecycle of
        :class:`_KeyboardBridge`, where ``data`` is the component's synced
        parameter object.
    """
    table = json.dumps({binding.key: binding.action for binding in bindings}, sort_keys=True)
    return f"""
    const keymap = {table};
    const separator = {json.dumps(KEYSTROKE_SEPARATOR)};
    let pressed = 0;
    if (window.__prismabib_screener_keydown) {{
      document.removeEventListener('keydown', window.__prismabib_screener_keydown, true);
    }}
    const listener = function (event) {{
      if (event.ctrlKey || event.metaKey || event.altKey) {{ return; }}
      const target = event.target;
      if (target) {{
        const tag = (target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select') {{ return; }}
        if (target.isContentEditable) {{ return; }}
      }}
      if (!(event.key in keymap)) {{ return; }}
      event.preventDefault();
      pressed += 1;
      data.keystroke = event.key + separator + pressed;
    }};
    window.__prismabib_screener_keydown = listener;
    document.addEventListener('keydown', listener, true);
    """


def help_markdown(bindings: Sequence[KeyBinding]) -> str:
    """Render the keyboard map as the panel ``?`` shows.

    Generated from the same data the dispatch reads rather than written out,
    so a binding cannot be added without its help line appearing, or removed
    while its line lingers. An undocumented key is, to the reviewer,
    indistinguishable from a key that does not exist.

    Args:
        bindings: The key map to document.

    Returns:
        Markdown, one line per binding.
    """
    lines = ["**Keyboard**", ""]
    lines += [f"- `{binding.key}` — {binding.help}" for binding in bindings]
    return "\n".join(lines)


class _KeyboardBridge(ReactiveHTML):
    """The one DOM node whose only job is to carry keystrokes to the kernel.

    A :class:`~panel.reactive.ReactiveHTML` component because that is Panel's
    supported way to run JavaScript and sync a value back: ``pn.pane.HTML``
    inserts markup with ``innerHTML``, which never executes a ``<script>``, so
    the obvious implementation would have shipped a keyboard that silently did
    nothing in the browser while every Python test passed.
    """

    #: Set by the browser listener to ``"<key><KEYSTROKE_SEPARATOR><counter>"``.
    keystroke = param.String(default="")

    _template: ClassVar[str] = '<div id="bridge" class="prismabib-keyboard"></div>'
    _scripts: ClassVar[dict[str, str]] = {"render": keymap_javascript(KEY_MAP)}


# ---------------------------------------------------------------------------
# The screener
# ---------------------------------------------------------------------------


class Screener:
    """One reviewer's screening session: the queue, the view, and the keyboard.

    Holds the state a queue deliberately does not: which reason digit the
    reviewer is expected to press next, what the status line says, and when
    the decisions of *this* session were made (the queue is clock-free by
    design, so pace is this class's problem).

    Every handler is a plain no-argument method, and :attr:`handlers` maps the
    :data:`KEY_MAP` action names onto them. Keystrokes, buttons and tests all
    go through that one mapping, so there is no path by which a key could
    reach behaviour a test cannot.
    """

    def __init__(
        self,
        queue: ScreeningQueue,
        records: Mapping[str, ScreeningRecord],
        *,
        blind: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Build a session over an already-constructed queue.

        Args:
            queue: The reviewer's queue, already folded and positioned.
            records: Screenable content for (at least) every id in
                ``queue.pending``, from :func:`load_records`.
            blind: Hide author names and citation counts (default ``True``).
            clock: Monotonic seconds source for the pace display. Injected
                because :mod:`prismabib.screening.queue` is deliberately
                clock-free and a pace that read ``time.monotonic()`` inline
                could only be tested by freezing time globally -- §3.7.3
                rule 1 permits doubling the clock, and this is what it is for.
        """
        self._queue = queue
        self._records = dict(records)
        self._blind = blind
        self._clock = clock
        self._palette = reason_palette(queue.project.criteria, queue.stage)
        self._session_marks: list[float] = []
        self._awaiting_reason = False
        self._status = "Press ? for the keyboard map."
        self._handlers: dict[str, Callable[[], None]] = {
            "include": self.include,
            "begin_exclude": self.begin_exclude,
            "unsure": self.unsure,
            "next_record": self.next_record,
            "previous_record": self.previous_record,
            "undo": self.undo,
            "show_help": self.show_help,
            **{digit_action(digit): _reason_handler(self, digit) for digit in REASON_DIGITS},
        }

        self._record_pane = pn.pane.Markdown(sizing_mode="stretch_width")
        self._progress_pane = pn.pane.Markdown(sizing_mode="stretch_width")
        self._status_pane = pn.pane.Markdown(sizing_mode="stretch_width")
        self._help_pane = pn.pane.Markdown(help_markdown(KEY_MAP), visible=False)
        self._bridge = _KeyboardBridge(height=0, width=0, margin=0)
        self._bridge.param.watch(self._on_keystroke, "keystroke")
        self.refresh()

    # -- state a test can read ----------------------------------------------

    @property
    def queue(self) -> ScreeningQueue:
        """The queue being screened."""
        return self._queue

    @property
    def blind(self) -> bool:
        """Whether author names and citation counts are omitted."""
        return self._blind

    @property
    def palette(self) -> dict[str, str]:
        """``{digit: reason_code}`` for this stage, from ``criteria.yaml``."""
        return dict(self._palette)

    @property
    def handlers(self) -> dict[str, Callable[[], None]]:
        """Every action name :data:`KEY_MAP` may name, mapped to its handler."""
        return dict(self._handlers)

    @property
    def record_pane(self) -> pn.pane.Markdown:
        """The pane holding the current record's markdown.

        Exposed so a test can read what the reviewer is actually shown -- the
        end of the blinding path and of the exhausted-queue path -- without
        reaching into the assembled layout by index, which would pin the test
        to the arrangement rather than the content.
        """
        return self._record_pane

    @property
    def bridge(self) -> _KeyboardBridge:
        """The component the browser's keydown listener writes into.

        Exposed because it is the one seam of the keyboard path that is both
        testable in Python and invisible to :meth:`handle_key`: whether the
        watcher is registered, on the right parameter, and parses the payload.
        Setting :attr:`_KeyboardBridge.keystroke` on it is what the browser
        does, and is as close to a keypress as a headless test can get.
        """
        return self._bridge

    @property
    def awaiting_reason(self) -> bool:
        """Whether ``e`` has been pressed and a reason digit is expected."""
        return self._awaiting_reason

    @property
    def status(self) -> str:
        """The last message shown to the reviewer."""
        return self._status

    @property
    def session_decisions(self) -> int:
        """Resolving decisions made since this screener was constructed."""
        return len(self._session_marks)

    # -- the view -----------------------------------------------------------

    def view(self) -> pn.viewable.Viewable:
        """Assemble the Panel view.

        Returns:
            A ``Viewable``: the record, the reason palette, the mouse controls,
            progress, status, help, and the hidden keyboard bridge.
        """
        return pn.Column(
            self._progress_pane,
            pn.layout.Divider(),
            self._record_pane,
            pn.layout.Divider(),
            pn.Row(*self._reason_buttons()),
            pn.Row(*self._control_buttons()),
            self._status_pane,
            self._help_pane,
            self._bridge,
            sizing_mode="stretch_width",
        )

    def refresh(self) -> None:
        """Recompute every pane from the queue's current state."""
        current = self._queue.current
        if current is None:
            self._record_pane.object = (
                "### Screening complete\n\nEvery record at this stage has a decision."
            )
        else:
            record = self._records.get(current, ScreeningRecord(record_id=current))
            self._record_pane.object = record_markdown(record_view_model(record, blind=self._blind))

        self._progress_pane.object = progress_markdown(self.progress())
        self._status_pane.object = f"{self._status}  \n`{current or '—'}`"

    def progress(self) -> dict[str, Any]:
        """This session's progress and pace.

        Returns:
            A :func:`progress_view_model` mapping, with the session's own
            decision count and elapsed seconds filled in from the injected
            clock.
        """
        elapsed = self._clock() - self._session_marks[0] if self._session_marks else 0.0
        return progress_view_model(
            decided=self._queue.decided,
            total=self._queue.total,
            session_decisions=len(self._session_marks),
            session_seconds=elapsed,
        )

    # -- keyboard -----------------------------------------------------------

    def handle_key(self, key: str) -> None:
        """Dispatch one keystroke through :data:`KEY_MAP`.

        The whole keyboard path on this side of the browser. Unbound keys are
        ignored in silence: a reviewer scrolling with the arrow keys should
        not be told off, and the browser listener filters the map's keys
        anyway.

        Args:
            key: A ``KeyboardEvent.key`` value.
        """
        for binding in KEY_MAP:
            if binding.key == key:
                self.dispatch(binding.action)
                return

    def dispatch(self, action: str) -> None:
        """Run one named handler, turning a refused action into a status line.

        Errors are caught here and nowhere deeper: the handlers raise, so they
        stay testable, and this -- the edge the browser calls -- is where a
        ``LogError`` has to become something a reviewer can read instead of a
        traceback in a kernel log they will never open.

        Args:
            action: A :data:`KEY_MAP` action name.

        Raises:
            ValidationError: If ``action`` names no handler. Not a reviewer
                error -- it can only be a key map that has outrun the
                handlers, which is a bug and must be loud.
        """
        handler = self._handlers.get(action)
        if handler is None:
            raise ValidationError(
                f"no handler named {action!r}; KEY_MAP and Screener.handlers disagree"
            )
        try:
            handler()
        except PrismabibError as exc:
            self._set_status(f"⚠ {exc}")

    def _on_keystroke(self, event: param.parameterized.Event) -> None:
        """Bridge callback: unpack the browser's payload and dispatch it.

        Args:
            event: The param event carrying ``"<key><separator><counter>"``.
        """
        payload = str(event.new)
        if payload:
            self.handle_key(payload.split(KEYSTROKE_SEPARATOR, 1)[0])

    # -- handlers -----------------------------------------------------------

    def include(self) -> None:
        """``i`` — include the current record."""
        self.decide("include")

    def unsure(self) -> None:
        """``u`` — log an unsure decision; the record stays in the queue."""
        self.decide("unsure")

    def begin_exclude(self) -> None:
        """``e`` — arm the reason palette and wait for a digit.

        Two keystrokes rather than one because PRISMA requires the published
        diagram to break exclusions down by reason: an exclusion without a
        code is not a decision this project can report, and the log refuses
        it. Arming is deliberately not itself a decision -- nothing is
        appended until the digit arrives, so ``e`` pressed by accident costs
        nothing.
        """
        if not self._palette:
            self._set_status("no exclusion reason codes declared in criteria.yaml")
            return
        self._awaiting_reason = True
        offered = " · ".join(f"{digit} {code}" for digit, code in self._palette.items())
        self._set_status(f"exclude — press a digit: {offered}")

    def select_reason(self, digit: str) -> None:
        """A digit key: file the armed exclusion under that reason code.

        Args:
            digit: ``"1"`` .. ``"9"``.
        """
        if not self._awaiting_reason:
            self._set_status(f"press e first, then {digit}, to exclude with a reason")
            return
        code = self._palette.get(digit)
        if code is None:
            self._set_status(f"no reason code {digit} in criteria.yaml")
            return
        self.decide("exclude", reason_code=code)

    def next_record(self) -> None:
        """``n`` — move to the next record without deciding.

        Disarms any pending exclusion. Without that, ``e`` then ``n`` then a
        digit files the exclusion against the record the reviewer navigated
        *to*, not the one they were looking at when they pressed ``e`` -- a
        decision recorded against the wrong paper, under a reason code chosen
        while reading a different abstract. Nothing surfaces it: the log is
        well-formed, the counts add up, and it is invisible until the PRISMA
        breakdown is published.
        """
        self._disarm()
        self._queue.advance()
        self._set_status("next")

    def previous_record(self) -> None:
        """``p`` — move back one record without deciding.

        Disarms any pending exclusion, for the reason given in
        :meth:`next_record`.
        """
        self._disarm()
        self._queue.step_back()
        self._set_status("previous")

    def _disarm(self) -> None:
        """Cancel a pending exclusion, if one is armed.

        Called by every action that changes which record is current. Arming is
        a statement about *this* record, so it must not outlive it.
        """
        self._awaiting_reason = False

    def undo(self) -> None:
        """``z`` — supersede the previous record's decision and step back.

        Disarms any pending exclusion first: this moves the cursor, so an arm
        held across it would file against the wrong record.

        The queue appends a reversal rather than editing the log
        (:meth:`~prismabib.screening.queue.ScreeningQueue.undo`); this only
        reports what happened. The session's pace mark is dropped with it, so
        a correction does not read as progress.
        """
        self._disarm()
        reversal = self._queue.undo()
        if reversal is not None and self._session_marks:
            self._session_marks.pop()
        self._set_status("undone" if reversal is not None else "nothing to undo")

    def show_help(self) -> None:
        """``?`` — show or hide the keyboard map."""
        self._help_pane.visible = not self._help_pane.visible
        self._set_status("help shown" if self._help_pane.visible else "help hidden")

    # -- deciding -----------------------------------------------------------

    def decide(self, decision: Decision, *, reason_code: str | None = None) -> None:
        """Append one decision and move on.

        The append is synchronous and fsynced before the view advances
        (BUILD_PLAN line 1072, "autosave per decision"), so a kernel death
        costs at most the record on screen and never a decision already made.

        Args:
            decision: ``"include"``, ``"exclude"`` or ``"unsure"``.
            reason_code: Required for ``"exclude"``; validated against
                ``criteria.yaml`` by the log, not here.

        Raises:
            ValidationError: If the queue is exhausted.
            LogError: Anything the decision log refuses.
        """
        self._queue.decide(decision, reason_code=reason_code)
        if decision != "unsure":
            self._session_marks.append(self._clock())
        self._awaiting_reason = False
        self._set_status(f"{decision}{f' ({reason_code})' if reason_code else ''}")

    def _set_status(self, message: str) -> None:
        """Record a message and repaint.

        Args:
            message: What to show the reviewer.
        """
        self._status = message
        self.refresh()

    # -- widgets ------------------------------------------------------------

    def _reason_buttons(self) -> list[pn.widgets.Button]:
        """One button per declared reason code, for the mouse."""
        return [
            _button(f"{digit}: {code}", _reason_handler(self, digit, arm=True))
            for digit, code in self._palette.items()
        ]

    def _control_buttons(self) -> list[pn.widgets.Button]:
        """The non-exclusion actions, for the mouse."""
        labels = {
            "include": "include (i)",
            "unsure": "unsure (u)",
            "undo": "undo (z)",
            "previous_record": "prev (p)",
            "next_record": "next (n)",
            "show_help": "help (?)",
        }
        return [_button(label, _action_handler(self, action)) for action, label in labels.items()]


def digit_action(digit: str) -> str:
    """The :data:`KEY_MAP` action name for one palette digit.

    Args:
        digit: ``"1"`` .. ``"9"``.

    Returns:
        ``"reason_<digit>"``. One function so the name is spelled once: the
        key map, the handler table and the tests all ask for it here.
    """
    return f"reason_{digit}"


def _reason_handler(screener_: Screener, digit: str, *, arm: bool = False) -> Callable[[], None]:
    """Bind one digit to :meth:`Screener.select_reason`.

    A factory rather than a lambda written inside the comprehension, because a
    captured loop variable would give every button the last reason code -- a
    classic that would misfile exclusions silently, which is the one failure a
    screening UI must not have: nothing surfaces it until the PRISMA
    exclusion breakdown is published.

    Args:
        screener_: The screener to act on.
        digit: The palette digit this handler files under.
        arm: Whether to arm the exclusion first. ``True`` for the mouse (one
            click is the whole gesture), ``False`` for the keyboard (``e``
            has already armed it, and a bare digit must not exclude).

    Returns:
        A no-argument handler.
    """

    def handler() -> None:
        if arm:
            screener_.begin_exclude()
        screener_.select_reason(digit)

    return handler


def _action_handler(screener_: Screener, action: str) -> Callable[[], None]:
    """Bind one action name to :meth:`Screener.dispatch`.

    Args:
        screener_: The screener to act on.
        action: A :data:`KEY_MAP` action name.

    Returns:
        A no-argument handler that dispatches through the same table the
        keyboard uses, so a button and its key can never diverge.
    """
    return lambda: screener_.dispatch(action)


def _button(label: str, handler: Callable[[], None]) -> pn.widgets.Button:
    """A Panel button wired to a no-argument handler.

    Args:
        label: The button's text.
        handler: Called on click; the Panel click event is discarded, so
            handlers stay callable from a test with no arguments at all.

    Returns:
        The wired button.
    """
    # `label=`, not `name=`: Panel deprecated the latter and will remove it in
    # 2.0, and a PendingDeprecationWarning per button is the kind of noise that
    # trains people to ignore warnings.
    button = pn.widgets.Button(label=label)
    button.on_click(lambda _event: handler())
    return button


def screener(
    project: Project,
    *,
    stage: str,
    reviewer: str,
    blind: bool = True,
) -> pn.viewable.Viewable:
    """Return a Panel view; display in a notebook cell or ``panel serve`` it.

    The frozen contract, BUILD_PLAN line 1083. One construction has to satisfy
    both halves of it, so this calls :meth:`~panel.viewable.Viewable.servable`
    on the result: outside a server that is a no-op returning the same object,
    and under ``panel serve`` it is what registers the view with the document.
    The reviewer's notebook cell and the served script are then the identical
    line of code, which is what "with no code change" means.

    Args:
        project: The project to screen.
        stage: ``"title_abstract"`` or ``"fulltext"``.
        reviewer: Who is screening. Decisions fold per reviewer, so a second
            coder screens the same corpus without seeing the first's calls.
        blind: Omit author names and citation counts (default ``True``). See
            :func:`record_view_model` for why the default is on.

    Returns:
        A Panel ``Viewable``.

    Raises:
        ValidationError: If ``stage`` is not a human-screened stage, or
            ``reviewer`` is blank.
        ConfigError: If ``criteria.yaml`` fails to parse.
        LogError: If the decision log fails to load.
        StoreError: If no Layer 1 store exists yet.
    """
    pn.extension()
    queue = screening_queue(project, _resolve_stage(stage), reviewer)
    records = load_records(project, queue.pending)
    view = Screener(queue, records, blind=blind).view()
    return view.servable()


def _resolve_stage(stage: str) -> PrismaStage:
    """Turn the contract's ``stage`` string into a :class:`PrismaStage`.

    Args:
        stage: The string a caller passed.

    Returns:
        The matching stage.

    Raises:
        ValidationError: If ``stage`` names no stage at all. ``PrismaStage``
            would raise ``ValueError`` here, which is neither in §3.3's error
            taxonomy nor able to say what the caller should have typed --
            and this is the argument a notebook user is most likely to
            mistype.
    """
    try:
        return PrismaStage(stage)
    except ValueError:
        screenable = sorted(
            candidate.value for candidate in (PrismaStage.TITLE_ABSTRACT, PrismaStage.FULLTEXT)
        )
        raise ValidationError(
            f"stage {stage!r} is not a PRISMA stage; expected one of {screenable}"
        ) from None


__all__ = [
    "KEYSTROKE_SEPARATOR",
    "KEY_MAP",
    "REASON_DIGITS",
    "UNBLINDED_FIELDS",
    "VISIBLE_FIELDS",
    "KeyBinding",
    "Screener",
    "ScreeningRecord",
    "digit_action",
    "help_markdown",
    "keymap_javascript",
    "load_records",
    "progress_markdown",
    "progress_view_model",
    "reason_palette",
    "record_markdown",
    "record_view_model",
    "screener",
]
