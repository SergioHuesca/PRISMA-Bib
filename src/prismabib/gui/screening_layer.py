"""Screening as the screen sees it (GUI spec #312, ticket #332).

Screening has no CLI path, so the layer wraps the library's own :class:`ScreeningQueue` and
:class:`Screener` and adds only: what is missing, the Reviewer, the lock on every Decision, the
Unscreened/Unsure counts, and the blind-mode view model. The Decision log is written by the
library's queue; nothing here formats or edits it.
"""

from __future__ import annotations

import threading
from typing import Any, Final

import panel as pn  # the hosted Screener repaints an extra pane

from prismabib.errors import ValidationError
from prismabib.gui import layer, tasks
from prismabib.gui.layer import Session, write_operation
from prismabib.prisma.events import Decision, DecisionEvent
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.screening import ui
from prismabib.screening.queue import ScreeningQueue
from prismabib.source_mode import load_mode_events
from prismabib.stage import PrismaStage
from prismabib.user_settings import REVIEWER_NAME_KEY, read_user_setting

#: The two names a screen must keep apart. A Decision carries only the Reviewer; the actor is the
#: name the library records on Source mode changes.
REVIEWER_LABEL: Final = "Reviewer (recorded on every Decision you make here)"
ACTOR_LABEL: Final = "Actor (recorded by the library on Source mode changes; a separate field)"
#: What undo really does: the library appends an Unsure Decision, so an undone Record is Unsure.
UNDO_NOTE: Final = (
    "Undo withdraws your Decision on the Record on screen by adding an Unsure Decision; "
    "the Decision log keeps both, and the Record then counts as Unsure, not Unscreened."
)
UNDONE_STATUS: Final = "undone: the Record now counts as Unsure (the log keeps both Decisions)"
LOAD_TASK_NAME: Final = "Opening the Screener"


def missing(project: Project) -> list[str]:
    """What screening still needs, as sentences; empty when it can open."""
    out = []
    if not layer.criteria_written(project):
        out.append("Criteria are not written yet. Finish the Criteria step.")
    if not project.db_path.is_file():
        out.append("The store is not built yet. Finish the Build step.")
    return out


def creation_reviewer(project: Project) -> str:
    """The Reviewer name entered at creation, else the per-user setting, else blank."""
    events = load_mode_events(project)
    return events[0].actor if events else read_user_setting(REVIEWER_NAME_KEY) or ""


class LockedQueue(ScreeningQueue):
    """A queue whose every Decision goes through the layer, so a read-only window refuses it.

    ``session`` is the window's held write lock; without one each Decision takes a short one.
    """

    session: Session | None = None

    def decide(
        self, decision: Decision, *, reason_code: str | None = None, note: str = ""
    ) -> DecisionEvent:
        with layer.session_for(self.project, self.session) as active:
            return decide(active, self, decision, reason_code=reason_code, note=note)

    def undo(self) -> DecisionEvent | None:
        with layer.session_for(self.project, self.session) as active:
            return undo(active, self)


@write_operation
def decide(
    session: Session,
    queue: ScreeningQueue,
    decision: Decision,
    *,
    reason_code: str | None = None,
    note: str = "",
) -> DecisionEvent:
    """Log one Decision for the record under the cursor, unless the session is read-only."""
    session.require_writable()
    return ScreeningQueue.decide(queue, decision, reason_code=reason_code, note=note)


@write_operation
def undo(session: Session, queue: ScreeningQueue) -> DecisionEvent | None:
    """Reverse the Decision on the record under the cursor, unless the session is read-only."""
    session.require_writable()
    return ScreeningQueue.undo(queue)


def counts(queue: ScreeningQueue) -> dict[str, int]:
    """Records at this stage with no Decision yet, and Records whose latest Decision is unsure."""
    latest = [queue.decision_for(record_id) for record_id in queue.order]
    return {
        "unscreened": sum(d is None for d in latest),
        "unsure": sum(d == "unsure" for d in latest),
        "total": queue.total,
    }


def screen_model(
    queue: ScreeningQueue, records: dict[str, ui.ScreeningRecord], *, blind: bool
) -> dict[str, Any]:
    """Everything the screen shows around the current Record, as a plain dict.

    In blind mode the keys for author names, citation counts and other reviewers' Decisions are
    absent, not blanked.
    """
    current = queue.current
    model: dict[str, Any] = {
        "reviewer": queue.reviewer,
        "stage": queue.stage.value,
        "blind": blind,
        "counts": counts(queue),
        "record": None,
    }
    if current is not None:
        record = records.get(current, ui.ScreeningRecord(record_id=current))
        model["record"] = ui.record_view_model(record, blind=blind)
        if not blind:
            model["other_decisions"] = sorted(
                (
                    {"reviewer": who, "decision": str(event.decision)}
                    for (stage, rid, who), event in queue.log.fold().items()
                    if stage is queue.stage and rid == current and who != queue.reviewer
                ),
                key=lambda row: row["reviewer"],
            )
    return model


def screen_markdown(model: dict[str, Any]) -> str:
    """The header above the Screener: who is screening and the Unscreened/Unsure counts."""
    c = model["counts"]
    lines = [
        f"**{REVIEWER_LABEL}:** {model['reviewer']}",
        f"**{ACTOR_LABEL}:** set by the library, not by you",
        f"**Unscreened:** {c['unscreened']} · **Unsure:** {c['unsure']} · **Records:** {c['total']}",
        f"**Blind mode:** {'on' if model['blind'] else 'off'}",
        f"_{UNDO_NOTE}_",
    ]
    others = model.get("other_decisions")
    if others:
        lines.append(
            "**Other reviewers:** " + ", ".join(f"{o['reviewer']}: {o['decision']}" for o in others)
        )
    return "  \n".join(lines)


class HostedScreener(ui.Screener):
    """The library's Screener with the layer's header (Reviewer, counts) kept in step."""

    def __init__(
        self, queue: ScreeningQueue, records: dict[str, ui.ScreeningRecord], *, blind: bool
    ) -> None:
        super().__init__(queue, records, blind=blind)
        self._screen_records = records
        self._header = pn.pane.Markdown(
            sizing_mode="stretch_width", renderer_options={"html": False}
        )
        self.refresh()

    def refresh(self) -> None:
        super().refresh()
        header = getattr(self, "_header", None)  # the base constructor repaints before it exists
        if header is not None:
            header.object = screen_markdown(
                screen_model(self.queue, self._screen_records, blind=self.blind)
            )

    def undo(self) -> None:
        super().undo()
        if self.status == "undone":  # the library's own word, which hides that the Record is Unsure
            self._set_status(UNDONE_STATUS)

    def view(self) -> pn.viewable.Viewable:
        return pn.Column(self._header, super().view(), sizing_mode="stretch_width")


def open_screening(
    project: Project,
    stage: PrismaStage,
    reviewer: str,
    *,
    blind: bool = True,
    log: DecisionLog | None = None,
) -> HostedScreener:
    """The Screener for ``reviewer`` at ``stage``.

    Raises:
        ValidationError: With a blank Reviewer, or when Criteria or the store are missing.
    """
    if gaps := missing(project):
        raise ValidationError(" ".join(gaps))
    queue, records = _load(project, stage, reviewer, log)
    return HostedScreener(queue, records, blind=blind)


def _load(
    project: Project,
    stage: PrismaStage,
    reviewer: str,
    log: DecisionLog | None = None,
    session: Session | None = None,
) -> tuple[LockedQueue, dict[str, ui.ScreeningRecord]]:
    """The slow, widget-free half of opening: the queue and every Record it will show."""
    queue = LockedQueue(project, stage, reviewer, log=log)
    queue.session = session
    return queue, ui.load_records(project, queue.pending)


def start_loading(
    project: Project, stage: PrismaStage, reviewer: str, session: Session | None = None
) -> None:
    """Load the queue on the Project's task runner, so :func:`load_status` can be read meanwhile.

    Not a write operation: loading reads only; every Decision still goes through the lock.

    Raises:
        ValidationError: When Criteria or the store are missing, or another task is running.
    """
    if gaps := missing(project):
        raise ValidationError(" ".join(gaps))

    def work(
        _cancel: threading.Event, _report: Any
    ) -> tuple[LockedQueue, dict[str, ui.ScreeningRecord]]:
        return _load(project, stage, reviewer, session=session)

    tasks.runner_for(project).start(LOAD_TASK_NAME, work)


def load_status(project: Project) -> tasks.TaskStatus:
    """The load's status, else an idle one (a memory read, so it answers while the queue loads)."""
    status = tasks.runner_for(project).status()
    return status if status.name == LOAD_TASK_NAME else tasks.TaskStatus()


def finish_loading(project: Project, *, blind: bool) -> HostedScreener | None:
    """The Screener once the load is done (built on the caller's thread); else ``None``.

    Raises:
        Exception: Whatever the load raised (a blank Reviewer, for one).
    """
    status = load_status(project)
    if status.error is not None:
        raise status.error
    if status.outcome != "done":
        return None
    queue, records = status.result
    return HostedScreener(queue, records, blind=blind)
