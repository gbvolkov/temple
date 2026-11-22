from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Settings
from server.db import init_database
from server.migration_acceptance import (
    AcceptanceError,
    EXTERNAL_CHECKER_VERSION,
    _external_evidence,
    create_batch,
    execute_audit_run,
    get_batch,
    queue_audit,
    recover_interrupted_audits,
    verify_acceptance,
)
from server.migration_preparation import execute_preparation_run, queue_preparation


ROOT = Path(__file__).resolve().parents[1]


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        root=ROOT,
        site_dir=ROOT / "site",
        database_path=tmp_path / "cms.sqlite3",
        media_dir=tmp_path / "media",
        media_derivatives_dir=tmp_path / "media-derivatives",
        schema_path=ROOT / "site" / "cms-schema.json",
        legacy_sections_path=ROOT / "current-sections.json",
        legacy_crawl_path=None,
        media_manifest_path=tmp_path / "legacy-media-manifest.json",
        environment="test",
        bootstrap_user="admin",
        bootstrap_password="test-password",
        session_hours=1,
        submission_worker_interval_seconds=3600,
    )


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/admin/login", json={"username": "admin", "password": "test-password"})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def news_data(*, year: int = 2014, legacy: bool = False) -> dict:
    body = []
    if legacy:
        body = [{"id": "legacy-1", "type": "legacy_text", "data": {"text": "Архивный текст"}}]
    return {
        "publication_date": f"{year:04d}-07-20",
        "category": "Новости прихода",
        "summary": "Проверенное краткое описание материала.",
        "cover": "assets/school-maslenitsa.jpg",
        "cover_alt": "Приходской праздник",
        "body": body,
        "related_content": [],
    }


def create_imported_news(client: TestClient, headers: dict[str, str], title: str, *, year: int = 2014, legacy: bool = False) -> dict:
    data = news_data(year=year, legacy=legacy)
    data["summary"] = f"{data['summary']} {title}"
    response = client.post(
        "/api/admin/contents",
        headers=headers,
        json={"content_type": "news", "title": title, "data": data},
    )
    assert response.status_code == 201, response.text
    item = response.json()
    settings = client.app.state.settings
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("UPDATE contents SET migration_review_required=1 WHERE id=?", (item["id"],))
    item["migration_review_required"] = True
    return item


def run_audit(settings: Settings, *, content_id: str | None = None) -> dict:
    scope = {"content_id": content_id, "check_external": False} if content_id else {"check_external": False}
    run = queue_audit(settings.database_path, actor_id=None, scope=scope)
    return execute_audit_run(
        settings.database_path,
        settings.schema_path,
        settings.media_dir,
        settings.site_dir,
        run["id"],
        check_external=False,
    )


def test_cms_38_clergy_position_is_nfkc_on_save(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        created = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "clergy", "title": "Священник", "data": {"rank": "Иерей", "position": "Служение"}},
        ).json()
        payload = {"title": created["title"], "slug": created["slug"], "version": created["version"],
                   "data": {**created["data"], "position": "Служение\u00a0в храме"}}
        response = client.put(f"/api/admin/contents/{created['id']}", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["data"]["position"] == "Служение в храме"
        assert updated["data"]["rank"] == "Иерей"
        payload.update(version=updated["version"], data=updated["data"])
        again = client.put(f"/api/admin/contents/{created['id']}", headers=headers, json=payload)
        assert again.status_code == 200
        assert again.json()["version"] == updated["version"]


def test_cms_39_card_review_and_local_unicode_repair(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        response = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "clergy", "title": "Иерей Сергий", "data": {"rank": "Иерей", "position": "Служение"}},
        )
        assert response.status_code == 201
        item = response.json()
        with sqlite3.connect(settings.database_path) as connection:
            data = item["data"] | {"position": "Служение\u00a0в храме"}
            connection.execute("UPDATE contents SET data_json=?, migration_review_required=1 WHERE id=?", (json.dumps(data), item["id"]))
        run_audit(settings, content_id=item["id"])
        review = client.get(f"/api/admin/contents/{item['id']}/migration-review")
        assert review.status_code == 200
        review = review.json()
        assert review["assessment"]["content_version"] == item["version"]
        assert review["assessment"]["audit_run_id"]
        issue = next(issue for issue in review["issues"] if issue["code"] == "unicode_not_nfkc")
        assert issue["field_path"] == "content.position"
        url = f"/api/admin/contents/{item['id']}/migration-issues/{issue['id']}/normalize"
        user = client.post("/api/admin/users", headers=headers, json={"username": "viewer39", "password": "Strong-Password-2026!", "role": "viewer"})
        assert user.status_code == 201
        client.post("/api/admin/logout", headers=headers)
        viewer_login = client.post("/api/admin/login", json={"username": "viewer39", "password": "Strong-Password-2026!"})
        viewer_headers = {"X-CSRF-Token": viewer_login.json()["csrf_token"]}
        assert client.post(url, headers=viewer_headers, json={"version": item["version"]}).status_code == 403
        client.post("/api/admin/logout", headers=viewer_headers)
        headers = login(client)
        assert client.post(url, headers=headers, json={"version": 999}).status_code == 409
        fixed = client.post(url, headers=headers, json={"version": item["version"]})
        assert fixed.status_code == 200, fixed.text
        result = fixed.json()
        assert result["content"]["data"]["rank"] == "Иерей"
        assert result["content"]["data"]["position"] == "Служение в храме"
        assert result["content"]["migration_review_required"]
        assert result["review"]["audit_version"] == result["content"]["version"]
        assert all(i["code"] != "unicode_not_nfkc" for i in result["review"]["issues"])
        again = client.post(url, headers=headers, json={"version": result["content"]["version"]})
        assert again.status_code == 200
        assert again.json()["content"]["version"] == result["content"]["version"]


def test_cms_41_batch_rejects_changed_selection(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers, "Проверка версии партии")
        run_audit(settings)
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE contents SET version=version+1 WHERE id=?", (item["id"],))
        run_audit(settings, content_id=item["id"])
        response = client.post("/api/admin/migration/batches", headers=headers, json={
            "name": "Проверка", "kind": "priority", "content_ids": [item["id"]],
            "expected_versions": {item["id"]: item["version"]},
        })
        assert response.status_code == 409, response.text
        assert client.get("/api/admin/migration/batches").json()["total"] == 0


def test_cms_42_batch_candidate_lookup_by_title(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers, "Особый заголовок")
        run_audit(settings)
        response = client.get("/api/admin/migration/batch-candidates", params={"q": "Особый"})
        assert response.status_code == 200
        candidate = response.json()["items"][0]
        assert candidate["id"] == item["id"]
        assert candidate["eligible"] and candidate["version"] == item["version"]
        assert candidate["audit_run_id"]


def test_cms_43_refresh_stale_batch_resets_only_changed_decisions(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        first = create_imported_news(client, headers, "Одинаковый заголовок")
        second = create_imported_news(client, headers, "Одинаковый заголовок")
        run_audit(settings)
        with sqlite3.connect(settings.database_path) as connection:
            actor_id = connection.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        batch = create_batch(settings.database_path, name="Тест", kind="priority", content_ids=[first["id"], second["id"]], actor_id=actor_id)
        for item in batch["items"]:
            response = client.patch(f"/api/admin/migration/batches/{batch['id']}/items/{item['content_id']}", headers=headers,
                                    json={"version": item["version"], "manual_reviewed": True, "disposition": "accept", "note": "Проверено"})
            assert response.status_code == 200
            batch = response.json()
        content = client.get(f"/api/admin/contents/{first['id']}").json()
        content["data"]["summary"] += " Изменено."
        saved = client.put(f"/api/admin/contents/{first['id']}", headers=headers,
                           json={"version": content["version"], "title": content["title"], "slug": content["slug"], "data": content["data"]})
        assert saved.status_code == 200
        run_audit(settings, content_id=first["id"])
        response = client.post(f"/api/admin/migration/batches/{batch['id']}/refresh", headers=headers, json={"version": batch["version"]})
        assert response.status_code == 200, response.text
        refreshed = {item["content_id"]: item for item in response.json()["items"]}
        assert refreshed[first["id"]]["disposition"] == "pending"
        assert not refreshed[first["id"]]["manual_reviewed"]
        assert refreshed[second["id"]]["disposition"] == "accept"
        assert refreshed[second["id"]]["manual_reviewed"]


def test_cms_46_final_audit_includes_accepted_active_content(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers, "Принятый черновик")
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE contents SET migration_review_required=0 WHERE id=?", (item["id"],))
        run = queue_audit(settings.database_path, actor_id=None, scope={"population": "active", "check_external": False})
        result = execute_audit_run(settings.database_path, settings.schema_path, settings.media_dir,
                                   settings.site_dir, run["id"], check_external=False)
        assert result["counts"]["contents"] == 1
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM migration_audit_items WHERE run_id=? AND content_id=?", (run["id"], item["id"])).fetchone()[0] == 1


def test_cms_47_final_gate_rejects_missing_or_stale_active_audit(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers, "Проверенный материал")
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE contents SET migration_review_required=0 WHERE id=?", (item["id"],))
        assert client.get("/api/admin/migration/final-verification").status_code == 409
        run = queue_audit(settings.database_path, actor_id=None, scope={"population": "active", "check_external": False})
        execute_audit_run(settings.database_path, settings.schema_path, settings.media_dir, settings.site_dir, run["id"], check_external=False)
        assert client.get("/api/admin/migration/final-verification").status_code == 200
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE contents SET version=version+1 WHERE id=?", (item["id"],))
        assert client.get("/api/admin/migration/final-verification").status_code == 409


def test_cms_50_final_gate_checks_new_current_blockers(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers, "Проверенный материал")
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE contents SET migration_review_required=0 WHERE id=?", (item["id"],))
        run = queue_audit(settings.database_path, actor_id=None, scope={"population": "active", "check_external": False})
        execute_audit_run(settings.database_path, settings.schema_path, settings.media_dir, settings.site_dir, run["id"], check_external=False)
        assert client.get("/api/admin/migration/final-verification").status_code == 200
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("""INSERT INTO migration_review_issues(id,audit_run_id,content_id,content_version,code,severity,field_path,fingerprint,status,message,details_json,detected_at)
                VALUES('final-blocker',?,?,?,'late_blocker','blocker','title','final-blocker','open','Блокер','{}','2026-01-01')""", (run["id"], item["id"], item["version"]))
        assert client.get("/api/admin/migration/final-verification").status_code == 409


def test_cms_48_card_does_not_hide_failed_latest_audit(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers, "Неудачная проверка")
        run_audit(settings, content_id=item["id"])
        failed = queue_audit(settings.database_path, actor_id=None, scope={"content_id": item["id"], "check_external": False})
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE migration_audit_runs SET status='failed',error='Проверка не выполнена' WHERE id=?", (failed["id"],))
        review = client.get(f"/api/admin/contents/{item['id']}/migration-review").json()
        assert review["state"] == "failed"
        assert review["blocker_count"] is None and review["warning_count"] is None


def test_cms_55_card_detects_hash_drift_without_version_change(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers, "Проверка хеша")
        run_audit(settings, content_id=item["id"])
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE contents SET title='Другая версия данных' WHERE id=?", (item["id"],))
        review = client.get(f"/api/admin/contents/{item['id']}/migration-review").json()
        assert review["state"] == "stale"
        assert review["blocker_count"] is None


def test_migration_9_is_idempotent_and_preserves_existing_rows(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("contents", "revisions", "users", "media", "redirects", "submissions")
        }
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 15
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    init_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        assert before == after
    assert verify_acceptance(settings.database_path) == {
        "ok": True,
        "schema": 9,
            "rules_version": "1.1.0",
    }


def test_audit_identity_dedup_conflict_and_restart_recovery(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    first = queue_audit(
        settings.database_path,
        actor_id=None,
        scope={"check_external": False},
    )
    duplicate = queue_audit(
        settings.database_path,
        actor_id=None,
        scope={"check_external": False},
    )
    assert duplicate["id"] == first["id"]
    assert duplicate["deduplicated"] is True
    try:
        queue_audit(
            settings.database_path,
            actor_id=None,
            scope={"year": 2026, "check_external": False},
        )
    except AcceptanceError as error:
        assert error.code == "ACTIVE_AUDIT_SCOPE_CONFLICT"
        assert error.details["active_run_id"] == first["id"]
    else:
        raise AssertionError("Conflicting active audit must be rejected")
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "UPDATE migration_audit_runs SET status='running' WHERE id=?",
            (first["id"],),
        )
    assert recover_interrupted_audits(settings.database_path) == 1
    with sqlite3.connect(settings.database_path) as connection:
        row = connection.execute(
            """SELECT status,recovered,recovery_reason
               FROM migration_audit_runs WHERE id=?""",
            (first["id"],),
        ).fetchone()
    assert row == ("queued", 1, "APPLICATION_RESTART")


def test_external_evidence_is_canonical_ttl_aware_and_fail_closed(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    checked: list[str] = []

    def unavailable(url: str) -> tuple[bool, int | None, str | None]:
        checked.append(url)
        return False, 404, "HTTP 404"

    with sqlite3.connect(settings.database_path) as connection:
        connection.row_factory = sqlite3.Row
        first = _external_evidence(
            connection,
            "HTTPS://EXAMPLE.COM:443/a/../missing#fragment",
            unavailable,
            allow_online=True,
        )
        cached = _external_evidence(
            connection,
            "https://example.com/missing",
            lambda _: (_ for _ in ()).throw(AssertionError("cache must be reused")),
            allow_online=False,
        )
        connection.execute(
            """UPDATE migration_link_cache SET checked_at='2000-01-01T00:00:00+00:00'
               WHERE url_hash=?""",
            (first["url_identity"],),
        )
        stale = _external_evidence(
            connection,
            "https://example.com/missing",
            unavailable,
            allow_online=False,
        )
    assert checked == ["https://example.com/missing"]
    assert first["result"] == cached["result"] == "content_unavailable"
    assert cached["cached"] is True
    assert cached["checker_version"] == EXTERNAL_CHECKER_VERSION
    assert stale["result"] == "missing_or_stale"


def test_navigation_detector_does_not_flag_normal_phrase_you_are_here(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(
            client, headers, "Форум православной молодёжи", year=2024
        )
        with sqlite3.connect(settings.database_path) as connection:
            data = json.loads(connection.execute(
                "SELECT data_json FROM contents WHERE id=?", (item["id"],)
            ).fetchone()[0])
            data["body"] = [{
                "id": "paragraph-1",
                "type": "paragraph",
                "data": {
                    "runs": [{
                        "text": "Ценим всё, что происходит в сфере, которую вы здесь представляете.",
                        "marks": [],
                    }]
                },
            }]
            connection.execute(
                "UPDATE contents SET data_json=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), item["id"]),
            )
        run_audit(settings)
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute(
                """SELECT COUNT(*) FROM migration_review_issues
                   WHERE content_id=? AND code='legacy_navigation_text'
                     AND status='open'""",
                (item["id"],),
            ).fetchone()[0] == 0


def test_audit_rules_repeat_without_content_changes_and_old_bypass_is_closed(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers, "Техническая дата")
        broken = {
            "publication_date": "2000-01-01",
            "category": "Новости прихода",
            "summary": "",
            "cover": "",
            "cover_alt": "",
            "body": [{"id": "old", "type": "legacy_text", "data": {"text": "Главное меню\nО храме\nЖизнь прихода\nВоскресная школа\nКонтакты"}}],
            "target_url": "/does-not-exist",
        }
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE contents SET data_json=? WHERE id=?", (json.dumps(broken, ensure_ascii=False), item["id"]))
            baseline = {
                "contents": connection.execute("SELECT COUNT(*) FROM contents").fetchone()[0],
                "revisions": connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0],
                "version": connection.execute("SELECT version FROM contents WHERE id=?", (item["id"],)).fetchone()[0],
            }

        first = run_audit(settings, content_id=item["id"])
        second = run_audit(settings, content_id=item["id"])
        assert first["counts"]["contents"] == second["counts"]["contents"] == 1
        issues = client.get(f"/api/admin/migration/issues?q={item['title']}").json()["items"]
        codes = {issue["code"] for issue in issues}
        assert {"fallback_date_2000", "required_field_missing", "legacy_text", "legacy_navigation_text"} <= codes
        code_search = client.get("/api/admin/migration/issues?q=required_field_missing&severity=blocker").json()
        assert code_search["total"] >= 1
        assert {issue["code"] for issue in code_search["items"]} == {"required_field_missing"}
        assert next(issue for issue in issues if issue["code"] == "fallback_date_2000")["severity"] == "blocker"
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM migration_review_issues WHERE content_id=? AND status='open'",
                (item["id"],),
            ).fetchone()[0] == len(issues)
            assert baseline == {
                "contents": connection.execute("SELECT COUNT(*) FROM contents").fetchone()[0],
                "revisions": connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0],
                "version": connection.execute("SELECT version FROM contents WHERE id=?", (item["id"],)).fetchone()[0],
            }

        bypass = client.post(
            f"/api/admin/contents/{item['id']}/review",
            headers=headers,
            json={"version": item["version"]},
        )
        assert bypass.status_code == 409
        bulk_bypass = client.post(
            "/api/admin/content-bulk",
            headers=headers,
            json={"action": "review", "items": [{"id": item["id"], "version": item["version"]}]},
        )
        assert bulk_bypass.status_code == 409


def test_batch_sampling_stale_guard_and_atomic_archive(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        items = [create_imported_news(client, headers, f"Архив {index:02d}") for index in range(10)]
        run_audit(settings)
        with sqlite3.connect(settings.database_path) as connection:
            actor_id = connection.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
            revisions_before = connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
        batch = create_batch(
            settings.database_path,
            name="Архив 2014",
            kind="archive",
            content_ids=[item["id"] for item in items],
            actor_id=actor_id,
            sample_rate=0.1,
            batch_id="00000000-0000-4000-8000-000000000010",
        )
        assert batch["progress"]["items"] == 10
        assert batch["progress"]["sampled"] == 1
        assert get_batch(settings.database_path, batch["id"])["items"] == batch["items"]

        stale_item = batch["items"][0]
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE contents SET version=version+1 WHERE id=?", (stale_item["content_id"],))
        stale = client.patch(
            f"/api/admin/migration/batches/{batch['id']}/items/{stale_item['content_id']}",
            headers=headers,
            json={"version": stale_item["version"], "manual_reviewed": True, "disposition": "archive", "note": "Дубликат"},
        )
        assert stale.status_code == 409

        cancelled = client.post(
            f"/api/admin/migration/batches/{batch['id']}/cancel",
            headers=headers,
            json={"version": batch["version"]},
        )
        assert cancelled.status_code == 200, cancelled.text

        # Use a fresh, fully audited one-item batch to exercise atomic finalization.
        target = next(item for item in items if item["id"] != stale_item["content_id"])
        run_audit(settings, content_id=target["id"])
        final_batch = create_batch(
            settings.database_path,
            name="Решение по архиву",
            kind="archive",
            content_ids=[target["id"]],
            actor_id=actor_id,
            sample_rate=0.1,
        )
        row = final_batch["items"][0]
        final_batch = client.patch(
            f"/api/admin/migration/batches/{final_batch['id']}/items/{target['id']}",
            headers=headers,
            json={"version": row["version"], "manual_reviewed": True, "disposition": "archive", "note": "Редакционное решение"},
        ).json()
        final_batch = client.post(
            f"/api/admin/migration/batches/{final_batch['id']}/submit",
            headers=headers,
            json={"version": final_batch["version"]},
        ).json()
        finalized = client.post(
            f"/api/admin/migration/batches/{final_batch['id']}/finalize",
            headers=headers,
            json={"version": final_batch["version"], "warning_acknowledgements": {}},
        )
        assert finalized.status_code == 200, finalized.text
        with sqlite3.connect(settings.database_path) as connection:
            status, flag, version = connection.execute(
                "SELECT status,migration_review_required,version FROM contents WHERE id=?",
                (target["id"],),
            ).fetchone()
            assert (status, flag, version) == ("archived", 0, target["version"])
            assert connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == revisions_before


def test_priority_material_is_always_in_manual_sample(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        priority = create_imported_news(client, headers, "Новая приоритетная новость", year=2026)
        archive = [create_imported_news(client, headers, f"Старый материал {index}") for index in range(9)]
        run_audit(settings)
        with sqlite3.connect(settings.database_path) as connection:
            actor_id = connection.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        # Priority batches are manual in full; the year rule is enforced even with a low requested sample.
        batch = create_batch(
            settings.database_path,
            name="Приоритет 2026",
            kind="priority",
            content_ids=[priority["id"], *[item["id"] for item in archive]],
            actor_id=actor_id,
            sample_rate=0.1,
        )
        assert batch["progress"]["sampled"] == 10
        assert all(item["sampled"] for item in batch["items"])


def test_api_roles_csrf_and_pilot_does_not_clear_flags(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        response = client.post(
            "/api/admin/contents",
            headers=headers,
            json={
                "content_type": "page",
                "title": "История храма",
                "data": {"placement": "about_history", "body": []},
            },
        )
        assert response.status_code == 201, response.text
        item = response.json()
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "UPDATE contents SET migration_review_required=1,legacy_url='/o-hrame/istoriya/' WHERE id=?",
                (item["id"],),
            )
        run_audit(settings)
        assert client.post("/api/admin/migration/batches/pilot").status_code == 403
        preview = queue_preparation(settings.database_path, actor_id=None)
        preview = execute_preparation_run(settings, preview["id"])
        apply_run = queue_preparation(
            settings.database_path,
            actor_id=None,
            mode="apply",
            preview_run_id=preview["id"],
        )
        execute_preparation_run(settings, apply_run["id"])
        run_audit(settings)
        pilot = client.post("/api/admin/migration/batches/pilot", headers=headers)
        assert pilot.status_code == 201, pilot.text
        assert len(pilot.json()["items"]) == 1
        assert pilot.json()["status"] == "draft"
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute(
                "SELECT migration_review_required,status FROM contents WHERE id=?", (item["id"],)
            ).fetchone() == (1, "draft")
