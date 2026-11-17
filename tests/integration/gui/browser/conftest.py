"""The one app-and-browser rig every module of the browser check shares (once per worker)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Playwright, sync_playwright
from pytest_socket import enable_socket

from tests.integration.gui.browser import harness as h


@pytest.fixture(scope="session")
def rig(tmp_path_factory: pytest.TempPathFactory) -> Iterator[h.Rig]:
    enable_socket()
    # Importing the GUI makes tornado select the selector event loop on Windows, which cannot
    # start Playwright's driver process. Its loop is created at start, so the policy is only
    # swapped for that call and put back: it is process-global state.
    policy = asyncio.get_event_loop_policy()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        playwright: Playwright = sync_playwright().start()
    finally:
        asyncio.set_event_loop_policy(policy)
    app: h.App | None = None
    browser: Browser | None = None
    try:
        browser = h.launch_browser(playwright)
        app = h.App(tmp_path_factory.mktemp("browser-app"))
        yield h.Rig(app, browser)
    finally:
        if browser:
            browser.close()
        if app:
            app.stop()
        playwright.stop()
