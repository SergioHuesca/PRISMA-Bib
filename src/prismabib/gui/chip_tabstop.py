"""Keep the chip boxes' dropdown list out of the Tab order (ticket #376, spec #371).

The dropdown of a chip box (Choices.js) holds a list that scrolls (``overflow auto``, 300 px) and
has no focusable child, so Chromium makes the list itself a Tab stop. Choices closes the dropdown
when the search input loses focus, so the list that just took focus is hidden a few milliseconds
later and focus falls to the page body. Hiding the list is Choices' choice, so the stop is
removed instead: ``tabindex="-1"`` keeps the list scrollable by wheel and arrow keys but makes it
no Tab stop. CSS cannot do this (``overflow: hidden`` would stop the wheel).
"""

from __future__ import annotations

from typing import ClassVar

from panel.reactive import ReactiveHTML

# Chip boxes render inside shadow roots, after this node and without an event that reaches the page
# (a document-level focusin listener sees only the first focus), and Choices builds its list again
# when the dropdown first opens, replacing the box too. So every shadow root found gets an
# observer, and a change in it sets the attribute on every list in it at once (a timer would
# lose the race against a quick Tab). ponytail: each mutation re-reads its tree; fine for this
# app's pages, upgrade if a screen ever mutates thousands of nodes a second.
#
# One registry lives on the window for the whole session. Every render disconnects and forgets all
# observers of the earlier renders (the page keeps no observer of a screen it has left) before it
# starts its own, so visiting Query again never adds observers. ``stats`` is a read-only view of it.
_SCRIPT = """
const registry = window.__prismabib_chip_tabstop || (window.__prismabib_chip_tabstop = {
  seen: new WeakSet(), observers: [], renders: 0,
  stats: function () { return {observers: registry.observers.length, renders: registry.renders}; },
});
for (const old of registry.observers) { old.disconnect(); }
registry.observers.length = 0;
registry.seen = new WeakSet();
registry.renders += 1;
const sweep = function (root) {
  for (const list of root.querySelectorAll('.choices__list--dropdown .choices__list')) {
    if (list.getAttribute('tabindex') !== '-1') { list.setAttribute('tabindex', '-1'); }
  }
  for (const node of root.querySelectorAll('*')) {
    if (node.shadowRoot && !registry.seen.has(node.shadowRoot)) { watch(node.shadowRoot); }
  }
};
const watch = function (root) {
  registry.seen.add(root);
  const observer = new MutationObserver(function () { sweep(root); });
  observer.observe(root, {childList: true, subtree: true});
  registry.observers.push(observer);
  sweep(root);
};
watch(document);
"""


class ChipTabstop(ReactiveHTML):
    """An invisible node whose only job is to install the listener above on the page."""

    _template: ClassVar[str] = '<div id="chip-tabstop" hidden></div>'
    _scripts: ClassVar[dict[str, str]] = {"render": _SCRIPT}
