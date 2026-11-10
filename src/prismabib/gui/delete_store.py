"""The delete-the-store control: a button and a confirmation that names what goes and stays."""

from __future__ import annotations

from collections.abc import Callable

import panel as pn  # widget layer only

from prismabib.gui import delete_store_layer as dl
from prismabib.gui import layer, palette
from prismabib.project import Project


def confirmation_text() -> str:
    """The confirmation, as the dialog shows it."""
    stays = "\n".join(f"- {item}" for item in dl.NOTICE.stays)
    return (
        f"**This deletes:** {dl.NOTICE.goes}\n\n**This stays:**\n\n{stays}\n\n{dl.NOTICE.never_deleted}"
        "\n\nYou can rebuild the store afterwards."
    )


def build_delete_store(
    project: Project,
    session: layer.Session | None = None,
    *,
    after: Callable[[], None] = lambda: None,
) -> tuple[pn.Column, Callable[[], None]]:
    """The delete control for ``project`` and its refresh; ``after`` runs once a delete is tried.

    ``session`` is the window's held write lock; a bare screen takes one per click.
    """
    ask = pn.widgets.Button(name="Delete the built store")
    yes = pn.widgets.Button(
        name="Yes, delete the store",
        button_type="danger",
        visible=False,
        stylesheets=[palette.BUTTON_CSS],
    )
    no = pn.widgets.Button(name="Keep the store", visible=False)
    text = pn.pane.Markdown(confirmation_text(), visible=False)
    message = pn.pane.Markdown("")

    def refresh() -> None:
        ask.disabled = not project.db_path.is_file()

    def toggle(open_: bool) -> None:
        text.visible = yes.visible = no.visible = open_

    def on_ask(_: object) -> None:
        message.object = ""
        toggle(True)

    def on_yes(_: object) -> None:
        toggle(False)
        try:
            with layer.session_for(project, session) as active:
                dl.delete_store(active, confirmed=True)
            message.object = "**The store was deleted.** Build the store to make it again."
        except Exception as exc:  # noqa: BLE001 - every failure is shown as a sentence
            msg = layer.translate(exc)
            message.object = f"**{msg.sentence}** {msg.next_step}"
        refresh()
        after()

    ask.on_click(on_ask)
    yes.on_click(on_yes)
    no.on_click(lambda _: toggle(False))
    refresh()
    return pn.Column(text, message, pn.Row(ask, yes, no), sizing_mode="stretch_width"), refresh
