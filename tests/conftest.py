from __future__ import annotations

from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("cms-e2e")
    group.addoption("--cms-headed", action="store_true", help="Show Chromium during CMS E2E tests")
    group.addoption("--cms-source-db", type=Path, help="Read-only source database for source_data tests")
    group.addoption("--cms-source-media", type=Path, help="Read-only source media directory")
    group.addoption("--cms-artifacts-dir", type=Path, help="Playwright artifacts directory")


def pytest_configure(config: pytest.Config) -> None:
    for marker in (
        "e2e: real-browser or characterization CMS regression",
        "smoke: short mandatory browser regression",
        "full: long browser regression",
        "source_data: requires explicitly supplied local runtime data",
        "defect(id): characterization check for a registered defect",
        "allow_browser_errors: browser errors are part of the asserted scenario",
        "expected_http_error(status, path): an explicitly asserted validation response",
        "expected_console_error(text): an explicitly asserted browser console error",
    ):
        config.addinivalue_line("markers", marker)
