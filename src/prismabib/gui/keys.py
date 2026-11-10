"""The keys screen: enter and test the OpenAlex and Scopus keys (ticket #325)."""

from __future__ import annotations

from pathlib import Path

import httpx
import panel as pn  # widget layer only

from prismabib.credentials import probe_scopus_key
from prismabib.credentials_ui import ConnectScopusPanel
from prismabib.errors import PrismabibError
from prismabib.gui import keys_model as km
from prismabib.gui.layer import translate
from prismabib.project import Project
from prismabib.user_settings import load_settings, user_settings_path, write_user_settings


def build_keys(
    project: Project, *, user_file: Path | None = None, http_client: httpx.Client | None = None
) -> pn.Column:
    """The keys step for ``project``. ``user_file`` and ``http_client`` are for tests."""
    where = user_file if user_file is not None else user_settings_path()
    needed = km.scopus_needed(project)
    intro = (
        "## Keys\n\nKeys are saved in your per-user settings file "
        f"(`{where}`), never inside a Project folder."
    )
    intro += (
        "\n\nThis Source mode searches Scopus, so a **Scopus key is needed**."
        if needed
        else "\n\nThis Source mode is OpenAlex only: no Scopus key is needed."
    )
    sources = pn.pane.Markdown("")
    openalex = pn.widgets.PasswordInput(name="OpenAlex API key")
    save = pn.widgets.Button(name="Save OpenAlex key", button_type="primary")
    test = pn.widgets.Button(name="Test OpenAlex key")
    message = pn.pane.Markdown("")

    def refresh() -> None:
        lines = [f"OpenAlex key comes from: {km.key_source(km.OPENALEX_KEY, project, where)}."]
        if needed:
            lines.append(f"Scopus key comes from: {km.key_source(km.SCOPUS_KEY, project, where)}.")
        sources.object = "\n\n".join(lines)

    def on_save(_: object) -> None:
        value, openalex.value, openalex.value_input = openalex.value or "", "", ""
        try:
            km.save_openalex_key(value, where)
        except PrismabibError as exc:
            msg = translate(exc)
            message.object = f"**{msg.sentence}** {msg.next_step}"
        else:
            message.object = "Saved. Test the key to see that it works."
        refresh()

    def on_test(_: object) -> None:
        message.object = km.readable(km.probe_openalex_key(project, where, http_client=http_client))

    save.on_click(on_save)
    test.on_click(on_test)
    refresh()
    parts: list[pn.viewable.Viewable] = [
        pn.pane.Markdown(intro),
        sources,
        openalex,
        save,
        test,
        message,
    ]
    if needed:
        panel = ConnectScopusPanel(
            project,
            env_file=where,
            writer=lambda path, updates: write_user_settings(updates, path),
        )
        check = pn.widgets.Button(name="Test Scopus key")
        result = pn.pane.Markdown("")

        def on_check(_: object) -> None:
            try:
                settings = load_settings(km.project_env_file(project), where)
                result.object = km.readable(probe_scopus_key(settings, http_client=http_client))
            except PrismabibError as exc:
                msg = translate(exc)
                result.object = f"**{msg.sentence}** {msg.next_step}"
            panel.reload()
            refresh()

        check.on_click(on_check)
        parts += [panel.view(), check, result]
    return pn.Column(*parts, sizing_mode="stretch_width")
