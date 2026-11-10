"""The home screen: first-run check, Projects folder, the list of Projects (ticket #322)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import panel as pn  # widget layer only

from prismabib.doctor_checks import Check
from prismabib.gui import home_model as hm


def build_home(
    root: Path | None = None,
    *,
    checks: Callable[[], Mapping[str, Check]] = hm.default_checks,
    on_new_project: Callable[[], None] | None = None,
    on_open: Callable[[Path, str], None] | None = None,
) -> pn.Column:
    """The home screen. ``root`` is the explicit Projects folder option, when there is one."""
    column = pn.Column(sizing_mode="stretch_width")
    first_run = hm.take_first_run()
    report = hm.prerequisite_problems(checks(), first_run=first_run)
    folder = pn.widgets.TextInput(name="Projects folder", value=str(hm.resolve_folder(root).path))
    use = pn.widgets.Button(name="Use this folder", button_type="primary")
    create = pn.widgets.Button(name="Create this folder")
    new = pn.widgets.Button(name="New Project", button_type="success")
    message = pn.pane.Markdown("")
    listing = pn.Column()

    def show_projects(path: Path) -> None:
        entries = hm.list_projects(path)
        listing[:] = [_entry(e, path) for e in entries] or [
            pn.pane.Markdown("No Projects in this folder yet.")
        ]

    def _entry(e: hm.ProjectEntry, path: Path) -> pn.viewable.Viewable:
        text = pn.pane.Markdown(
            f"- **{e.title}** (`{e.slug}`)" if e.title != e.slug else f"- `{e.slug}`"
        )
        if on_open is None:
            return text
        open_button = pn.widgets.Button(name=f"Open {e.slug}")
        open_button.on_click(lambda _: on_open(path, e.slug))
        return pn.Row(text, open_button)

    def choose(create_it: bool) -> None:
        path = Path((folder.value or "").strip() or ".")
        result = hm.use_folder(path, create=create_it)
        if result.refusal:
            message.object = f"**{result.refusal}**"
            return
        message.object = result.warning or f"Using {path.absolute()}."
        show_projects(path.absolute())

    use.on_click(lambda _: choose(False))
    create.on_click(lambda _: choose(True))
    if on_new_project is not None:
        new.on_click(lambda _: on_new_project())

    state = hm.resolve_folder(root)
    if state.missing:
        message.object = f"**{state.missing}**"
    else:
        check = hm.check_folder(state.path)
        message.object = check.refusal or check.warning or ""
        show_projects(state.path)

    parts: list[pn.viewable.Viewable] = [pn.pane.Markdown("# prismabib")]
    for problem in report.problems:
        parts.append(pn.pane.Markdown(f"**{problem.sentence}** {problem.fix}"))
    if report.app_control_hint:
        parts.append(pn.pane.Markdown(report.app_control_hint))
    if first_run:
        parts.append(pn.pane.Markdown(hm.FIREWALL_NOTE))
    parts += [folder, pn.Row(use, create), message, new, pn.pane.Markdown("## Projects"), listing]
    column[:] = parts
    return column
