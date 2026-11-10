"""The size-check screen: how big a search is before committing to it (ticket #328)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, tzinfo
from pathlib import Path

import httpx
import panel as pn  # widget layer only

from prismabib.errors import PrismabibError
from prismabib.gui import keys_model as km
from prismabib.gui import size_check_model as model
from prismabib.gui.capture_layer import note_size_check
from prismabib.gui.layer import translate
from prismabib.project import Project
from prismabib.query import build_openalex_query_for_project, build_query_for_project
from prismabib.sizecheck import check_openalex_size, check_scopus_size
from prismabib.user_settings import load_settings


def build_size_check(
    project: Project,
    *,
    on_keys: Callable[[], None] | None = None,
    on_continue: Callable[[], None] | None = None,
    user_file: Path | None = None,
    transport: httpx.BaseTransport | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    tz: tzinfo | None = None,
) -> pn.Column:
    """The size check for ``project``. The keyword arguments after ``on_continue`` are for tests."""
    scopus = project.source_mode != "openalex"
    name = "Scopus" if scopus else "OpenAlex"
    run = pn.widgets.Button(name=f"Check the size of the {name} search", button_type="primary")
    confirm = pn.widgets.Checkbox(
        name="I understand this is a large set and want to continue", visible=False
    )
    go = pn.widgets.Button(name="Continue to capture", disabled=True)
    keys = pn.widgets.Button(name="Go to the keys step", visible=False)
    result = pn.pane.Markdown("")
    notice = pn.pane.Markdown("")
    state: dict[str, model.Verdict] = {"verdict": "block"}

    def sync(*_: object) -> None:
        v = state["verdict"]
        go.disabled = v == "block" or (v == "warn" and not confirm.value)

    def on_check(_: object) -> None:
        result.object = notice.object = ""
        keys.visible = confirm.visible = False
        confirm.value = False
        state["verdict"] = "block"
        note_size_check(project, None)
        sync()
        try:
            settings = load_settings(km.project_env_file(project), user_file)
            key = settings.scopus_api_key if scopus else settings.openalex_api_key
            if key is None:
                keys.visible = True
                result.object = (
                    f"**A {name} key is needed** to check the size. Add it in the keys step."
                )
                return
            if scopus:
                found = check_scopus_size(
                    build_query_for_project(project), settings=settings, transport=transport
                )
                lines, count = model.scopus_lines(found), found.record_count
            else:
                oa = check_openalex_size(
                    build_openalex_query_for_project(project),
                    settings=settings,
                    transport=transport,
                    now=now,
                )
                lines, count = model.openalex_lines(oa, tz), oa.record_count
        except PrismabibError as exc:
            msg = translate(exc)
            result.object = f"**{msg.sentence}** {msg.next_step} The saved query is unchanged."
            return
        state["verdict"] = model.verdict(count)
        if scopus and state["verdict"] != "block":
            note_size_check(project, build_query_for_project(project))
        result.object = "\n\n".join(lines)
        if state["verdict"] == "block":
            notice.object = f"**{model.BLOCK_TEXT}**"
        elif state["verdict"] == "warn":
            notice.object = f"**{model.WARN_TEXT}**"
            confirm.visible = True
        sync()

    run.on_click(on_check)
    confirm.param.watch(sync, "value")
    keys.on_click(lambda _: on_keys() if on_keys else None)
    go.on_click(lambda _: on_continue() if on_continue else None)
    return pn.Column(
        pn.pane.Markdown(
            "## Size check\n\nSee how many Records the saved query matches before capturing. "
            "It needs a saved query and a working key; nothing is written."
        ),
        run,
        result,
        notice,
        keys,
        confirm,
        go,
        sizing_mode="stretch_width",
    )
