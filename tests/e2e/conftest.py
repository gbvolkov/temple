from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

import pytest
import uvicorn
from fastapi.testclient import TestClient
from PIL import Image
from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from server.app import create_app
from server.baseline import readonly_connection
from server.config import Settings


ROOT = Path(__file__).resolve().parents[2]
E2E_ROOT = ROOT / "tests" / "e2e"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    registry = json.loads((E2E_ROOT / "defects.json").read_text(encoding="utf-8"))
    coverage = json.loads((E2E_ROOT / "coverage.json").read_text(encoding="utf-8"))
    known = {item["id"] for item in registry["defects"]}
    open_ids = {item["id"] for item in registry["defects"] if item["status"] == "open"}
    required_fields = {"id", "priority", "area", "status", "kind", "title", "test_nodeid", "bad_fingerprint", "expected"}
    for defect in registry["defects"]:
        missing_fields = required_fields - set(defect)
        if missing_fields:
            raise pytest.UsageError(f"{defect.get('id', '<unknown>')} missing fields: {sorted(missing_fields)}")
    seen: set[str] = set()
    for item in items:
        if "tests/e2e" in item.nodeid.replace("\\", "/"):
            item.add_marker("e2e")
        marker = item.get_closest_marker("defect")
        if marker:
            item.add_marker("allow_browser_errors")
            defect_id = str(marker.args[0])
            if defect_id not in known:
                raise pytest.UsageError(f"Unknown defect marker: {defect_id}")
            if defect_id not in open_ids:
                raise pytest.UsageError(f"Fixed defect must be a positive regression test: {defect_id}")
            seen.add(defect_id)
    collected_nodeids = {item.nodeid.replace("\\", "/").split("[")[0] for item in items}
    collection_roots = {Path(str(arg)).resolve() for arg in config.args if not str(arg).startswith("-")}
    full_collection = bool(collection_roots & {ROOT.resolve(), (ROOT / "tests").resolve(), E2E_ROOT.resolve()})
    if full_collection:
        missing = open_ids - seen
        if missing:
            raise pytest.UsageError(f"Defects without tests: {', '.join(sorted(missing))}")
        coverage_nodes = {suite["nodeid"] for suite in coverage["suites"]} | set(coverage["profiles"]["smoke"])
        missing_nodes = coverage_nodes - collected_nodeids
        if missing_nodes:
            raise pytest.UsageError(f"Coverage references unknown tests: {', '.join(sorted(missing_nodes))}")
        defect_nodes = {defect["test_nodeid"] for defect in registry["defects"]}
        missing_defect_nodes = defect_nodes - collected_nodeids
        if missing_defect_nodes:
            raise pytest.UsageError(
                f"Defect registry references unknown tests: {', '.join(sorted(missing_defect_nodes))}"
            )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def settings_for(root: Path, port: int) -> Settings:
    return Settings(
        root=ROOT,
        site_dir=ROOT / "site",
        database_path=root / "cms.sqlite3",
        media_dir=root / "media",
        media_derivatives_dir=root / "media-derivatives",
        schema_path=ROOT / "site" / "cms-schema.json",
        legacy_sections_path=ROOT / "current-sections.json",
        legacy_crawl_path=None,
        media_manifest_path=root / "legacy-media-manifest.json",
        environment="test",
        bootstrap_user="admin",
        bootstrap_password="test-password",
        session_hours=1,
        public_base_url=f"http://127.0.0.1:{port}",
        submission_ip_hash_secret="cms-e2e-submission-secret-2026-07-21",
        submission_worker_interval_seconds=3600,
    )


def _seed(settings: Settings) -> None:
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    settings.derivatives_dir.mkdir(parents=True, exist_ok=True)
    settings.media_manifest_path.write_text("{}\n", encoding="utf-8")
    with TestClient(create_app(settings)) as client:
        login = client.post("/api/admin/login", json={"username": "admin", "password": "test-password"})
        assert login.status_code == 200, login.text
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        for role in ("viewer", "editor", "publisher"):
            response = client.post(
                "/api/admin/users",
                headers=headers,
                json={"username": f"e2e_{role}", "password": "Strong-Password-2026!", "role": role},
            )
            assert response.status_code == 201, response.text
        schema = json.loads(settings.schema_path.read_text(encoding="utf-8"))
        for content_type in schema["content_types"]:
            response = client.post(
                "/api/admin/contents",
                headers=headers,
                json={"content_type": content_type, "title": f"E2E {content_type}", "data": {}},
            )
            assert response.status_code == 201, response.text
        for index in range(105):
            response = client.post(
                "/api/admin/contents",
                headers=headers,
                json={"content_type": "news", "title": f"E2E pagination {index:03d}", "data": {}},
            )
            assert response.status_code == 201, response.text
    with sqlite3.connect(settings.database_path) as connection:
        content_id, version = connection.execute(
            "SELECT id,version FROM contents WHERE content_type='news' ORDER BY title LIMIT 1"
        ).fetchone()
        now = "2026-07-21T00:00:00+00:00"
        connection.execute(
            """INSERT INTO migration_audit_runs(
                   id,rules_version,scope_json,status,counts_json,created_at,started_at,finished_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            ("e2e-audit", "1.0.0", "{}", "completed", '{"blocker":1}', now, now, now),
        )
        connection.execute(
            """INSERT INTO migration_audit_items(
                   run_id,content_id,content_version,blocker_count,warning_count,info_count,scanned_at
               ) VALUES(?,?,?,?,?,?,?)""",
            ("e2e-audit", content_id, version, 1, 0, 0, now),
        )
        connection.execute(
            """INSERT INTO migration_review_issues(
                   id,audit_run_id,content_id,content_version,code,severity,field_path,fingerprint,
                   status,message,details_json,detected_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "e2e-issue", "e2e-audit", content_id, version, "required_field_missing", "blocker",
                "cover", "e2e-fingerprint", "open", "Не заполнена обложка", "{}", now,
            ),
        )
        connection.execute(
            """INSERT INTO missing_media_issues(
                   id,source_url,error,source_directory,reference_count,status,version,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                "e2e-missing", "https://legacy.example/e2e-missing.jpg", "404", "/legacy/",
                1, "pending", 1, now, now,
            ),
        )
        connection.execute(
            "INSERT INTO missing_media_issue_contents(issue_id,content_id) VALUES(?,?)",
            ("e2e-missing", content_id),
        )
        pilot_rows = connection.execute(
            """SELECT id,content_type,version FROM contents
               WHERE content_type IN ('site_contact','page','clergy','parish_section')
               ORDER BY content_type,title"""
        ).fetchall()
        for pilot_index, (pilot_id, pilot_type, pilot_version) in enumerate(pilot_rows):
            connection.execute(
                "UPDATE contents SET migration_review_required=1 WHERE id=?",
                (pilot_id,),
            )
            if pilot_type == "page":
                connection.execute(
                    "UPDATE contents SET legacy_url=?,data_json=? WHERE id=?",
                    (
                        "/o-hrame/e2e-pilot-page.html",
                        json.dumps({"body_text": "Исходный crawl-текст пилотной страницы."}, ensure_ascii=False),
                        pilot_id,
                    ),
                )
            blocker_count = 1 if pilot_index == 0 else 0
            warning_count = 1 if pilot_index in {1, 2} else 0
            connection.execute(
                """INSERT INTO migration_audit_items(
                     run_id,content_id,content_version,blocker_count,warning_count,info_count,scanned_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                ("e2e-audit", pilot_id, pilot_version, blocker_count, warning_count, 0, now),
            )
            if blocker_count:
                connection.execute(
                    """INSERT INTO migration_review_issues(
                         id,audit_run_id,content_id,content_version,code,severity,field_path,fingerprint,
                         status,message,details_json,detected_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "e2e-pilot-blocker", "e2e-audit", pilot_id, pilot_version,
                        "e2e_pilot_blocker", "blocker", "title", "e2e-pilot-blocker-fingerprint",
                        "open", "Тестовая блокирующая проблема пилота", "{}", now,
                    ),
                )
            elif pilot_index == 1:
                connection.execute(
                    """INSERT INTO migration_review_issues(
                         id,audit_run_id,content_id,content_version,code,severity,field_path,fingerprint,
                         status,message,details_json,detected_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "e2e-pilot-individual-warning", "e2e-audit", pilot_id, pilot_version,
                        "duplicate_content", "warning", "body_text", "e2e-pilot-individual-warning-fingerprint",
                        "open", "Тестовое индивидуальное предупреждение пилота", "{}", now,
                    ),
                )
            elif pilot_index == 2:
                connection.execute(
                    """INSERT INTO migration_review_issues(
                         id,audit_run_id,content_id,content_version,code,severity,field_path,fingerprint,
                         status,message,details_json,detected_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "e2e-pilot-batch-warning", "e2e-audit", pilot_id, pilot_version,
                        "e2e_common_warning", "warning", "title", "e2e-pilot-batch-warning-fingerprint",
                        "open", "Тестовое общее предупреждение партии", "{}", now,
                    ),
                )


@dataclass
class LiveCms:
    base_url: str
    settings: Settings


@dataclass
class CoverageSteps:
    """Runtime proof that every manifest assertion was actually exercised."""

    expected: set[str]
    seen: set[str]

    def mark(self, assertion_id: str) -> None:
        assert assertion_id in self.expected, f"Undeclared coverage assertion: {assertion_id}"
        assert assertion_id not in self.seen, f"Coverage assertion executed twice: {assertion_id}"
        self.seen.add(assertion_id)

    def check(self, assertion_id: str, condition: object, detail: str = "") -> None:
        assert condition, detail or assertion_id
        self.mark(assertion_id)


@pytest.fixture(scope="session")
def artifacts_dir(pytestconfig: pytest.Config) -> Path:
    configured = pytestconfig.getoption("--cms-artifacts-dir")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = configured or ROOT / "output" / "playwright" / "cms-regression" / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


@pytest.fixture
def coverage_steps(request: pytest.FixtureRequest) -> CoverageSteps:
    coverage = json.loads((E2E_ROOT / "coverage.json").read_text(encoding="utf-8"))
    nodeid = request.node.nodeid.replace("\\", "/").split("[")[0]
    suite = next((item for item in coverage["suites"] if item["nodeid"] == nodeid), None)
    expected = set(suite["assertions"]) if suite and suite.get("runtime") else set()
    tracker = CoverageSteps(expected=expected, seen=set())
    yield tracker
    report = getattr(request.node, "rep_call", None)
    if report and report.passed:
        missing = tracker.expected - tracker.seen
        assert not missing, f"Declared coverage assertions not executed: {', '.join(sorted(missing))}"


@pytest.fixture(scope="session")
def seed_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("cms-e2e-seed")
    settings = settings_for(root, 1)
    _seed(settings)
    return root


@pytest.fixture
def live_cms(tmp_path: Path, seed_template: Path) -> LiveCms:
    root = tmp_path / "runtime"
    root.mkdir()
    port = _free_port()
    settings = settings_for(root, port)
    with readonly_connection(seed_template / "cms.sqlite3") as source, sqlite3.connect(settings.database_path) as target:
        source.backup(target)
    shutil.copytree(seed_template / "media", settings.media_dir)
    shutil.copytree(seed_template / "media-derivatives", settings.derivatives_dir)
    shutil.copy2(seed_template / "legacy-media-manifest.json", settings.media_manifest_path)
    app = create_app(settings)

    @app.middleware("http")
    async def e2e_delay_migration_query(request, call_next):
        if request.url.path == "/api/admin/migration/issues" and request.query_params.get("q") == "e2e-slow":
            await asyncio.sleep(0.5)
        if request.url.path == "/api/admin/media/reindex" and request.method == "POST":
            await asyncio.sleep(0.5)
        return await call_next(request)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="cms-e2e-uvicorn", daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/cms.html", timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("CMS E2E server did not start")
    yield LiveCms(base_url, settings)
    server.should_exit = True
    thread.join(timeout=10)
    if thread.is_alive():
        raise RuntimeError("CMS E2E server did not stop")


@pytest.fixture(scope="session")
def playwright_runtime() -> Playwright:
    with sync_playwright() as runtime:
        yield runtime


@pytest.fixture(scope="session")
def browser(playwright_runtime: Playwright, pytestconfig: pytest.Config) -> Browser:
    browser = playwright_runtime.chromium.launch(headless=not pytestconfig.getoption("--cms-headed"))
    yield browser
    browser.close()


@pytest.fixture
def page(browser: Browser, live_cms: LiveCms, artifacts_dir: Path, request: pytest.FixtureRequest) -> Page:
    context = browser.new_context(
        viewport={"width": 1440, "height": 1000},
        locale="ru-RU",
        timezone_id="Europe/Moscow",
    )
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    page.set_default_timeout(10_000)
    errors: list[str] = []
    http_errors: list[tuple[int, str]] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
    page.on("response", lambda response: http_errors.append((response.status, response.url)) if response.status >= 400 else None)
    page.goto(f"{live_cms.base_url}/cms.html", wait_until="domcontentloaded")
    yield page
    report = getattr(request.node, "rep_call", None)
    failed = bool(report and report.failed)
    expected_defect = request.node.get_closest_marker("defect") is not None
    expected_http = [
        (int(marker.args[0]), str(marker.args[1]))
        for marker in request.node.iter_markers("expected_http_error")
    ]
    expected_console = [
        str(marker.args[0]) for marker in request.node.iter_markers("expected_console_error")
    ]
    unmatched_http = [
        f"{status} {url}" for status, url in http_errors
        if not any(status == expected_status and path in url for expected_status, path in expected_http)
    ]
    expected_network_failure = bool(expected_http) and any(
        any(status == expected_status and path in url for expected_status, path in expected_http)
        for status, url in http_errors
    )
    filtered_console = [
        message for message in errors
        if not any(expected in message for expected in expected_console)
        and not (expected_network_failure and message.startswith("Failed to load resource: the server responded with a status of"))
    ]
    unexpected_errors = (
        [] if request.node.get_closest_marker("allow_browser_errors")
        else [*filtered_console, *unmatched_http]
    )
    preserve = failed or expected_defect or bool(unexpected_errors)
    safe_name = request.node.nodeid.replace("/", "_").replace("\\", "_").replace(":", "_")
    if preserve:
        page.screenshot(path=artifacts_dir / f"{safe_name}.png", full_page=True)
        context.tracing.stop(path=artifacts_dir / f"{safe_name}.zip")
        (artifacts_dir / f"{safe_name}.console.json").write_text(
            json.dumps({"console": errors, "http": http_errors}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        context.tracing.stop()
    context.close()
    if unexpected_errors:
        pytest.fail("Unexpected browser errors: " + " | ".join(unexpected_errors[:5]))


def login(page: Page, username: str = "admin", password: str = "test-password") -> None:
    dialog = page.locator("[data-login-dialog]")
    dialog.locator('[name="username"]').fill(username)
    dialog.locator('[name="password"]').fill(password)
    dialog.get_by_role("button", name="Войти").click()
    dialog.wait_for(state="hidden")
    page.locator(".cms-user strong").wait_for()


def api_login(page: Page, live_cms: LiveCms, username: str = "admin", password: str = "test-password") -> dict:
    response = page.request.post(
        f"{live_cms.base_url}/api/admin/login",
        data={"username": username, "password": password},
    )
    assert response.ok, response.text()
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def api_create_content(
    page: Page,
    live_cms: LiveCms,
    headers: dict[str, str],
    *,
    content_type: str,
    title: str,
    data: dict | None = None,
) -> dict:
    response = page.request.post(
        f"{live_cms.base_url}/api/admin/contents",
        headers=headers,
        data={"content_type": content_type, "title": title, "data": data or {}},
    )
    assert response.status == 201, response.text()
    return response.json()


def open_content(page: Page, content_type: str, title: str) -> None:
    page.locator(f'[data-content-type="{content_type}"]').click()
    unsaved = page.locator("[data-unsaved-dialog]")
    if unsaved.is_visible():
        unsaved.locator("[data-unsaved-discard]").click()
    picker = page.locator("[data-content-select]")
    picker.wait_for(state="visible")
    option = picker.locator("option").filter(has_text=title)
    option.wait_for(state="attached")
    picker.select_option(option.get_attribute("value"))
    page.locator("[data-editor-title]").filter(has_text=title).wait_for()


@pytest.fixture
def media_files(tmp_path: Path) -> dict[str, Path]:
    # Two valid tiny PNGs with different pixels and a small deterministic document.
    files = {
        "image": tmp_path / "e2e-media-primary.png",
        "replacement": tmp_path / "e2e-media-replacement.png",
        "second_image": tmp_path / "e2e-media-second.png",
        "document": tmp_path / "e2e-document.txt",
    }
    Image.new("RGB", (8, 8), (220, 20, 60)).save(files["image"], "PNG")
    Image.new("RGB", (8, 8), (30, 90, 210)).save(files["replacement"], "PNG")
    Image.new("RGB", (8, 8), (20, 160, 90)).save(files["second_image"], "PNG")
    files["document"].write_text("CMS E2E document\n", encoding="utf-8")
    return files


@pytest.fixture(scope="session")
def defect_registry() -> dict[str, dict]:
    data = json.loads((E2E_ROOT / "defects.json").read_text(encoding="utf-8"))
    return {item["id"]: item for item in data["defects"]}


def observe_open_defect(registry: dict[str, dict], defect_id: str, *, bad: bool, good: bool, detail: str) -> None:
    defect = registry[defect_id]
    if defect["status"] == "fixed":
        assert good, detail
        return
    if good:
        pytest.fail(f"{defect_id} behaves as fixed; update defects.json before accepting the run")
    assert bad, f"{defect_id} changed outside its registered fingerprint: {detail}"
    pytest.xfail(f"{defect_id}: {defect['title']}")
