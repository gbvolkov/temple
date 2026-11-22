from __future__ import annotations

import json
import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.app import create_app
from server.config import Settings
from server.migration_acceptance import execute_audit_run, queue_audit
from server.migration_preparation import (
    PreparationError,
    execute_preparation_run,
    get_preparation_run,
    list_preparation_items,
    queue_preparation,
    preparation_summary,
    recover_interrupted_preparations,
    rollback_preparation,
    verify_preparation,
)


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
        missing_media_report_path=tmp_path / "missing-legacy-media.csv",
        environment="test",
        bootstrap_user="admin",
        bootstrap_password="test-password",
        submission_worker_interval_seconds=3600,
    )


def login(
    client: TestClient,
    username: str = "admin",
    password: str = "test-password",
) -> dict[str, str]:
    response = client.post(
        "/api/admin/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def create_imported_news(
    client: TestClient,
    headers: dict[str, str],
    *,
    title: str = "Приходская встреча 12.05.2018",
    legacy_url: str = "/news/12-05-2018.html",
) -> dict:
    response = client.post(
        "/api/admin/contents",
        headers=headers,
        json={
            "content_type": "news",
            "title": title,
            "data": {
                "publication_date": "2000-05-12",
                "category": "Новости прихода",
                "summary": "Временное описание",
                "cover": "assets/school-maslenitsa.jpg",
                "cover_alt": title,
                "body": [{"type": "paragraph", "data": {"runs": [{"text": "Временный текст", "marks": []}]}}],
            },
        },
    )
    assert response.status_code == 201, response.text
    item = response.json()
    legacy = {
        "publication_date": "2000-05-12",
        "category": "Новости прихода",
        "summary": "",
        "cover": "assets/school-maslenitsa.jpg",
        "cover_alt": title,
        "body": [{"type": "legacy_text", "text": "Приходская встреча состоялась в храме.\n\nУчастники обсудили планы."}],
        "body_text": "Приходская встреча состоялась в храме.\n\nУчастники обсудили планы.",
        "migration_note": "Импортировано из crawl",
    }
    with sqlite3.connect(client.app.state.settings.database_path) as connection:
        connection.execute(
            """UPDATE contents SET data_json=?,legacy_url=?,migration_review_required=1
               WHERE id=?""",
            (json.dumps(legacy, ensure_ascii=False), legacy_url, item["id"]),
        )
    return item


def full_audit(settings: Settings) -> dict:
    run = queue_audit(
        settings.database_path, actor_id=None, scope={"check_external": False}
    )
    return execute_audit_run(
        settings.database_path,
        settings.schema_path,
        settings.media_dir,
        settings.site_dir,
        run["id"],
        check_external=False,
    )


def preview(settings: Settings) -> dict:
    full_audit(settings)
    run = queue_preparation(settings.database_path, actor_id=None)
    return execute_preparation_run(settings, run["id"])


def test_cms_40_stale_preview_is_not_actionable(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers)
        run = preview(settings)
        assert preparation_summary(settings.database_path)["actionable_preview"]["id"] == run["id"]
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE contents SET version=version+1 WHERE id=?", (item["id"],))
        assert preparation_summary(settings.database_path)["actionable_preview"] is None


def test_cms_44_automatic_acceptance_is_replayable_and_rollback_safe(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers)
        prepared = preview(settings)
        apply = queue_preparation(settings.database_path, actor_id=None, mode="apply", preview_run_id=prepared["id"])
        execute_preparation_run(settings, apply["id"])
        full_audit(settings)
        assert verify_preparation(settings.database_path)["ok"]
        current = client.get(f"/api/admin/contents/{item['id']}").json()
        current["data"]["summary"] += " Обновлено."
        edited = client.put(f"/api/admin/contents/{item['id']}", headers=headers, json={"version": current["version"],
            "title": current["title"], "slug": current["slug"], "data": current["data"]})
        assert edited.status_code == 200
        full_audit(settings)
        assert verify_preparation(settings.database_path)["ok"]
        current = edited.json()
        candidates = client.get("/api/admin/migration/automatic-acceptance/candidates").json()
        assert candidates["ready"] and candidates["items"][0]["id"] == item["id"]
        payload = {"operation_id": "test-operation-44", "items": [{"id": item["id"], "version": current["version"]}]}
        assert client.post("/api/admin/users", headers=headers, json={"username": "viewer44", "password": "Strong-Password-2026!", "role": "viewer"}).status_code == 201
        client.post("/api/admin/logout", headers=headers)
        viewer = login(client, "viewer44", "Strong-Password-2026!")
        assert client.get("/api/admin/migration/automatic-acceptance/candidates").status_code == 403
        assert client.post("/api/admin/migration/automatic-acceptance", headers=viewer, json=payload).status_code == 403
        client.post("/api/admin/logout", headers=viewer)
        headers = login(client)
        response = client.post("/api/admin/migration/automatic-acceptance", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        assert not client.get(f"/api/admin/contents/{item['id']}").json()["migration_review_required"]
        assert client.get(f"/api/admin/contents/{item['id']}").json()["status"] == "draft"
        replay = client.post("/api/admin/migration/automatic-acceptance", headers=headers, json=payload)
        assert replay.status_code == 200 and replay.json()["replayed"]
        rollback = client.post("/api/admin/migration/acceptance-operations/test-operation-44/rollback", headers=headers)
        assert rollback.status_code == 200, rollback.text
        assert client.get(f"/api/admin/contents/{item['id']}").json()["migration_review_required"]
        assert client.post("/api/admin/migration/acceptance-operations/test-operation-44/rollback", headers=headers).json()["replayed"]
        payload["operation_id"] = "test-operation-44-second"
        assert client.post("/api/admin/migration/automatic-acceptance", headers=headers, json=payload).status_code == 200
        accepted = client.get(f"/api/admin/contents/{item['id']}").json()
        accepted["data"]["summary"] += " Последующая правка."
        edited = client.put(f"/api/admin/contents/{item['id']}", headers=headers, json={"version": accepted["version"],
            "title": accepted["title"], "slug": accepted["slug"], "data": accepted["data"]})
        assert edited.status_code == 200
        conflict = client.post("/api/admin/migration/acceptance-operations/test-operation-44-second/rollback", headers=headers)
        assert conflict.status_code == 409
        assert client.get(f"/api/admin/contents/{item['id']}").json()["data"]["summary"].endswith("Последующая правка.")


def test_cms_49_semantic_gate_detects_current_issue_from_later_audit(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers)
        prepared = preview(settings)
        apply = queue_preparation(settings.database_path, actor_id=None, mode="apply", preview_run_id=prepared["id"])
        execute_preparation_run(settings, apply["id"])
        full_audit(settings)
        assert verify_preparation(settings.database_path)["ok"]
        active = queue_audit(settings.database_path, actor_id=None, scope={"population": "active", "check_external": False})
        execute_audit_run(settings.database_path, settings.schema_path, settings.media_dir, settings.site_dir, active["id"], check_external=False)
        with sqlite3.connect(settings.database_path) as connection:
            version = connection.execute("SELECT version FROM contents WHERE id=?", (item["id"],)).fetchone()[0]
            connection.execute("""INSERT INTO migration_review_issues(id,audit_run_id,content_id,content_version,code,severity,field_path,fingerprint,status,message,details_json,detected_at)
                VALUES('unknown-49',?,?,?,'unknown_49','blocker','title','unknown-49','open','Неизвестная проблема','{}','2026-01-01')""", (active["id"], item["id"], version))
        with pytest.raises(PreparationError):
            verify_preparation(settings.database_path)


def test_cms_52_batch_creation_rejects_stale_global_gate(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        first = create_imported_news(client, headers, title="Дубли 12.05.2018", legacy_url="/news/a")
        create_imported_news(client, headers, title="Дубли 12.05.2018", legacy_url="/news/b")
        prepared = preview(settings)
        apply = queue_preparation(settings.database_path, actor_id=None, mode="apply", preview_run_id=prepared["id"])
        execute_preparation_run(settings, apply["id"])
        full_audit(settings)
        assert verify_preparation(settings.database_path)["ok"]
        current = client.get(f"/api/admin/contents/{first['id']}").json()
        current["data"]["summary"] += " Уточнено."
        saved = client.put(f"/api/admin/contents/{first['id']}", headers=headers, json={"title": current["title"], "slug": current["slug"], "data": current["data"], "version": current["version"]}).json()
        run = queue_audit(settings.database_path, actor_id=None, scope={"content_id": first["id"], "check_external": False})
        execute_audit_run(settings.database_path, settings.schema_path, settings.media_dir, settings.site_dir, run["id"], check_external=False)
        response = client.post("/api/admin/migration/batches", headers=headers, json={"name": "Устаревший gate", "kind": "priority", "content_ids": [first["id"]],
            "expected_versions": {first["id"]: saved["version"]}, "expected_audits": {first["id"]: run["id"]}})
        assert response.status_code == 409
        assert client.get("/api/admin/migration/batches").json()["total"] == 0


def test_cms_53_finalized_batch_requires_new_full_audit(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        first = create_imported_news(client, headers, title="Дубли 12.05.2018", legacy_url="/news/a")
        create_imported_news(client, headers, title="Дубли 12.05.2018", legacy_url="/news/b")
        prepared = preview(settings)
        apply = queue_preparation(settings.database_path, actor_id=None, mode="apply", preview_run_id=prepared["id"])
        execute_preparation_run(settings, apply["id"])
        full_audit(settings)
        created = client.post("/api/admin/migration/batches", headers=headers, json={"name": "Архив", "kind": "priority", "content_ids": [first["id"]]}).json()
        item = created["items"][0]
        saved = client.patch(f"/api/admin/migration/batches/{created['id']}/items/{first['id']}", headers=headers,
            json={"version": item["version"], "manual_reviewed": True, "disposition": "archive", "note": "Архивная копия"}).json()
        submitted = client.post(f"/api/admin/migration/batches/{created['id']}/submit", headers=headers, json={"version": saved["version"]}).json()
        result = client.post(f"/api/admin/migration/batches/{created['id']}/finalize", headers=headers,
                             json={"version": submitted["version"], "warning_acknowledgements": {}})
        assert result.status_code == 200, result.text
        assert not preparation_summary(settings.database_path)["editorial_gate"]["ready"]
        full_audit(settings)
        assert preparation_summary(settings.database_path)["editorial_gate"]["ready"]


def test_cms_54_finalization_rejects_stale_global_gate(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        first = create_imported_news(client, headers, title="Дубли 12.05.2018", legacy_url="/news/a")
        second = create_imported_news(client, headers, title="Дубли 12.05.2018", legacy_url="/news/b")
        prepared = preview(settings)
        apply = queue_preparation(settings.database_path, actor_id=None, mode="apply", preview_run_id=prepared["id"])
        execute_preparation_run(settings, apply["id"])
        full_audit(settings)
        batch = client.post("/api/admin/migration/batches", headers=headers, json={"name": "Архив", "kind": "priority", "content_ids": [first["id"]]}).json()
        item = batch["items"][0]
        batch = client.patch(f"/api/admin/migration/batches/{batch['id']}/items/{first['id']}", headers=headers,
            json={"version": item["version"], "manual_reviewed": True, "disposition": "archive", "note": "Архив"}).json()
        batch = client.post(f"/api/admin/migration/batches/{batch['id']}/submit", headers=headers, json={"version": batch["version"]}).json()
        other = client.get(f"/api/admin/contents/{second['id']}").json()
        other["data"]["summary"] += " Изменено."
        assert client.put(f"/api/admin/contents/{second['id']}", headers=headers, json={"version": other["version"], "title": other["title"], "slug": other["slug"], "data": other["data"]}).status_code == 200
        result = client.post(f"/api/admin/migration/batches/{batch['id']}/finalize", headers=headers,
                             json={"version": batch["version"], "warning_acknowledgements": {}})
        assert result.status_code == 409
        assert client.get(f"/api/admin/contents/{first['id']}").json()["migration_review_required"]


def test_cms_56_semantic_gate_rejects_hash_drift(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers)
        prepared = preview(settings)
        apply = queue_preparation(settings.database_path, actor_id=None, mode="apply", preview_run_id=prepared["id"])
        execute_preparation_run(settings, apply["id"])
        full_audit(settings)
        assert verify_preparation(settings.database_path)["ok"]
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE contents SET title='Изменено без новой версии' WHERE id=?", (item["id"],))
        with pytest.raises(PreparationError):
            verify_preparation(settings.database_path)


def test_preview_apply_is_idempotent_and_rollback_is_compensating(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers)
        result = preview(settings)
        assert result["counts"] == {
            "auto_ready": 1,
            "changed": 1,
            "contents": 1,
        }
        planned = list_preparation_items(settings.database_path, result["id"])
        assert planned["total"] == 1
        assert planned["items"][0]["classification"] == "auto_ready"
        assert {step["rule"] for step in planned["items"][0]["transformations"]} >= {
            "date_from_evidence",
            "legacy_text_to_blocks",
            "provenance_to_journal",
            "summary_from_body",
        }

        queued = queue_preparation(
            settings.database_path,
            actor_id=None,
            mode="apply",
            preview_run_id=result["id"],
        )
        applied = execute_preparation_run(settings, queued["id"])
        same = queue_preparation(
            settings.database_path,
            actor_id=None,
            mode="apply",
            preview_run_id=result["id"],
        )
        assert same["id"] == applied["id"]
        full_audit(settings)
        assert verify_preparation(settings.database_path)["run_id"] == applied["id"]
        with sqlite3.connect(settings.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM contents WHERE id=?", (item["id"],)
            ).fetchone()
            data = json.loads(row["data_json"])
            assert row["migration_review_required"] == 1
            assert row["version"] == 2
            assert data["publication_date"] == "2018-05-12"
            assert data["summary"].startswith("Приходская встреча")
            assert data["body"][0]["type"] == "paragraph"
            assert "body_text" not in data
            assert connection.execute(
                "SELECT COUNT(*) FROM revisions WHERE content_id=?", (item["id"],)
            ).fetchone()[0] == 2

        rolled_back = rollback_preparation(
            settings.database_path, applied["id"], actor_id=None
        )
        assert rolled_back["status"] == "rolled_back"
        with sqlite3.connect(settings.database_path) as connection:
            row = connection.execute(
                "SELECT version,migration_review_required,data_json FROM contents WHERE id=?",
                (item["id"],),
            ).fetchone()
            assert row[0:2] == (3, 1)
            assert json.loads(row[2])["publication_date"] == "2000-05-12"


def test_apply_rejects_stale_preview_without_partial_changes(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers)
        result = preview(settings)
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "UPDATE contents SET version=version+1 WHERE id=?", (item["id"],)
            )
        queued = queue_preparation(
            settings.database_path,
            actor_id=None,
            mode="apply",
            preview_run_id=result["id"],
        )
        with pytest.raises(PreparationError, match="устарел"):
            execute_preparation_run(settings, queued["id"])
        assert get_preparation_run(settings.database_path, queued["id"])["status"] == "failed"
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM revisions WHERE content_id=?", (item["id"],)
            ).fetchone()[0] == 1


def test_apply_rejects_stale_hash_without_version_change(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers)
        result = preview(settings)
        with sqlite3.connect(settings.database_path) as connection:
            revisions_before = connection.execute(
                "SELECT COUNT(*) FROM revisions WHERE content_id=?", (item["id"],)
            ).fetchone()[0]
            data = json.loads(connection.execute(
                "SELECT data_json FROM contents WHERE id=?", (item["id"],)
            ).fetchone()[0])
            data["summary"] = "Изменено вне API без повышения версии"
            connection.execute(
                "UPDATE contents SET data_json=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), item["id"]),
            )
        queued = queue_preparation(
            settings.database_path,
            actor_id=None,
            mode="apply",
            preview_run_id=result["id"],
        )
        with pytest.raises(PreparationError) as captured:
            execute_preparation_run(settings, queued["id"])
        assert captured.value.code == "STALE_HASH"
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM revisions WHERE content_id=?", (item["id"],)
            ).fetchone()[0] == revisions_before


def test_preview_identity_deduplicates_active_and_restart_requeues_same_run(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        create_imported_news(client, headers)
        full_audit(settings)
        first = queue_preparation(settings.database_path, actor_id=None)
        duplicate = queue_preparation(settings.database_path, actor_id=None)
        assert duplicate["id"] == first["id"]
        assert duplicate["deduplicated"] is True
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "UPDATE migration_preparation_runs SET status='running' WHERE id=?",
                (first["id"],),
            )
        assert recover_interrupted_preparations(settings.database_path) == 1
        recovered = get_preparation_run(settings.database_path, first["id"])
        assert recovered["status"] == "queued"
        assert recovered["recovered"] is True
        assert recovered["recovery_reason"] == "APPLICATION_RESTART"


def test_generated_values_receive_final_nfkc_normalization(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(
            client,
            headers,
            title="Приходская встреча 12.05.2018",
        )
        with sqlite3.connect(settings.database_path) as connection:
            data = json.loads(connection.execute(
                "SELECT data_json FROM contents WHERE id=?", (item["id"],)
            ).fetchone()[0])
            data["body_text"] = "И\u0306юльская встреча\u0001 состоялась 12.05.2018."
            data["summary"] = ""
            connection.execute(
                "UPDATE contents SET data_json=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), item["id"]),
            )
        result = preview(settings)
        proposed = list_preparation_items(
            settings.database_path, result["id"]
        )["items"][0]["proposed"]
        serialized = json.dumps(proposed, ensure_ascii=False)
        assert serialized == __import__("unicodedata").normalize("NFKC", serialized)
        assert "\u0001" not in serialized


def test_unresolved_date_and_duplicate_are_editorial_not_technical(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        first = create_imported_news(
            client, headers, title="Встреча прихода", legacy_url="/news/vstrecha-a.html"
        )
        second = create_imported_news(
            client, headers, title="Встреча прихода", legacy_url="/news/vstrecha-b.html"
        )
        result = preview(settings)
        assert result["counts"]["editorial_review"] == 2
        assert result["counts"].get("technical_blocker", 0) == 0
        items = list_preparation_items(
            settings.database_path,
            result["id"],
            classification="editorial_review",
        )["items"]
        assert all(item["recommendation"] for item in items)
        assert all(item["options"] for item in items)
        assert all(
            {"date_requires_editor", "duplicate_title"} <=
            {reason["code"] for reason in item["reasons"]}
            for item in items
        )


def test_cover_reused_as_first_photo_is_not_duplicate_media_issue(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        response = client.post(
            "/api/admin/contents",
            headers=headers,
            json={
                "content_type": "gallery",
                "title": "Альбом без ложного дубля",
                "data": {
                    "event_date": "2018-05-12",
                    "category": "Жизнь прихода",
                    "summary": "",
                    "cover": "assets/school-maslenitsa.jpg",
                    "photos": [
                        {
                            "image": "assets/school-maslenitsa.jpg",
                            "alt": "Праздник",
                            "order": 1,
                        }
                    ],
                },
            },
        )
        assert response.status_code == 201, response.text
        item = response.json()
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "UPDATE contents SET migration_review_required=1 WHERE id=?",
                (item["id"],),
            )
        full_audit(settings)
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute(
                """SELECT COUNT(*) FROM migration_review_issues
                   WHERE content_id=? AND code='duplicate_media_reference' AND status='open'""",
                (item["id"],),
            ).fetchone()[0] == 0


def test_preparation_api_roles_preview_apply_items_and_rollback(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        admin_headers = login(client)
        item = create_imported_news(client, admin_headers)
        full_audit(settings)
        password = "Strong-Password-2026!"
        for username, role in (("publisher.prepare", "publisher"), ("viewer.prepare", "viewer")):
            response = client.post(
                "/api/admin/users",
                headers=admin_headers,
                json={"username": username, "password": password, "role": role},
            )
            assert response.status_code == 201, response.text

        publisher_headers = login(client, "publisher.prepare", password)
        response = client.post(
            "/api/admin/migration/preparations",
            headers=publisher_headers,
            json={"mode": "preview"},
        )
        assert response.status_code == 202, response.text
        preview_run = execute_preparation_run(settings, response.json()["id"])
        assert preview_run["status"] == "completed"
        forbidden = client.post(
            "/api/admin/migration/preparations",
            headers=publisher_headers,
            json={"mode": "apply", "preview_run_id": preview_run["id"]},
        )
        assert forbidden.status_code == 403

        viewer_headers = login(client, "viewer.prepare", password)
        assert client.get(
            f"/api/admin/migration/preparations/{preview_run['id']}",
            headers=viewer_headers,
        ).status_code == 200
        items = client.get(
            f"/api/admin/migration/preparations/{preview_run['id']}/items"
            "?classification=auto_ready",
            headers=viewer_headers,
        )
        assert items.status_code == 200
        assert items.json()["items"][0]["content_id"] == item["id"]
        assert client.post(
            "/api/admin/migration/preparations",
            headers=viewer_headers,
            json={"mode": "preview"},
        ).status_code == 403

        admin_headers = login(client)
        response = client.post(
            "/api/admin/migration/preparations",
            headers=admin_headers,
            json={"mode": "apply", "preview_run_id": preview_run["id"]},
        )
        assert response.status_code == 202, response.text
        applied = execute_preparation_run(settings, response.json()["id"])
        rollback = client.post(
            f"/api/admin/migration/preparations/{applied['id']}/rollback",
            headers=admin_headers,
        )
        assert rollback.status_code == 200, rollback.text
        assert rollback.json()["status"] == "rolled_back"


def test_leaflet_pdf_cover_is_generated_and_registered(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    stream = io.BytesIO()
    Image.new("RGB", (80, 120), "white").save(stream, "PDF")
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        uploaded = client.post(
            "/api/admin/media",
            headers=headers,
            files={"file": ("leaflet.pdf", stream.getvalue(), "application/pdf")},
        )
        assert uploaded.status_code == 201, uploaded.text
        pdf = uploaded.json()
        created = client.post(
            "/api/admin/contents",
            headers=headers,
            json={
                "content_type": "leaflet_issue",
                "title": "Приходской листок № 1",
                "data": {
                    "number": 1,
                    "period": "Июль 2026",
                    "publication_date": "2026-07-30",
                    "cover": "assets/school-maslenitsa.jpg",
                    "pdf": pdf["url"],
                    "featured": False,
                },
            },
        )
        assert created.status_code == 201, created.text
        content_id = created.json()["id"]
        with sqlite3.connect(settings.database_path) as connection:
            data = json.loads(connection.execute(
                "SELECT data_json FROM contents WHERE id=?", (content_id,)
            ).fetchone()[0])
            data["cover"] = ""
            connection.execute(
                """UPDATE contents SET data_json=?,migration_review_required=1
                   WHERE id=?""",
                (json.dumps(data, ensure_ascii=False), content_id),
            )
        result = preview(settings)
        item = list_preparation_items(settings.database_path, result["id"])["items"][0]
        cover_rule = next(
            rule for rule in item["transformations"]
            if rule["rule"] == "leaflet_pdf_cover"
        )
        apply_run = queue_preparation(
            settings.database_path,
            actor_id=None,
            mode="apply",
            preview_run_id=result["id"],
        )
        applied = execute_preparation_run(settings, apply_run["id"])
        assert applied["status"] == "completed"
        generated = settings.media_dir / cover_rule["stored_name"]
        assert generated.is_file()
        with sqlite3.connect(settings.database_path) as connection:
            connection.row_factory = sqlite3.Row
            media = connection.execute(
                "SELECT * FROM media WHERE id=?", (cover_rule["media_id"],)
            ).fetchone()
            content = json.loads(connection.execute(
                "SELECT data_json FROM contents WHERE id=?", (content_id,)
            ).fetchone()[0])
        assert media["mime_type"] == "image/webp"
        assert media["source"] == "migration_preparation"
        assert content["cover"] == cover_rule["url"]
        with sqlite3.connect(settings.database_path) as connection:
            event = connection.execute(
                """SELECT event_type,source_media_id FROM migration_generated_media_events
                   WHERE preparation_run_id=? AND media_id=?""",
                (applied["id"], cover_rule["media_id"]),
            ).fetchone()
        assert event == ("generated", cover_rule["pdf_media_id"])

        rolled_back = rollback_preparation(
            settings.database_path, applied["id"], actor_id=None
        )
        replayed = rollback_preparation(
            settings.database_path, applied["id"], actor_id=None
        )
        assert rolled_back["replayed"] is False
        assert replayed["replayed"] is True
        assert generated.is_file()
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute(
                """SELECT COUNT(*) FROM migration_generated_media_events
                   WHERE preparation_run_id=? AND media_id=?
                     AND event_type='retained_on_rollback'""",
                (applied["id"], cover_rule["media_id"]),
            ).fetchone()[0] == 1


def test_rollback_conflict_stops_before_changing_any_material(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        first = create_imported_news(
            client,
            headers,
            title="Первая встреча 12.05.2018",
            legacy_url="/news/first-12-05-2018.html",
        )
        second = create_imported_news(
            client,
            headers,
            title="Вторая встреча 13.05.2018",
            legacy_url="/news/second-13-05-2018.html",
        )
        result = preview(settings)
        queued = queue_preparation(
            settings.database_path,
            actor_id=None,
            mode="apply",
            preview_run_id=result["id"],
        )
        applied = execute_preparation_run(settings, queued["id"])
        with sqlite3.connect(settings.database_path) as connection:
            before_versions = dict(connection.execute(
                "SELECT id,version FROM contents WHERE id IN (?,?)",
                (first["id"], second["id"]),
            ))
            connection.execute(
                "UPDATE contents SET version=version+1 WHERE id=?",
                (second["id"],),
            )
        with pytest.raises(PreparationError, match="изменён после"):
            rollback_preparation(
                settings.database_path,
                applied["id"],
                actor_id=None,
            )
        with sqlite3.connect(settings.database_path) as connection:
            after_versions = dict(connection.execute(
                "SELECT id,version FROM contents WHERE id IN (?,?)",
                (first["id"], second["id"]),
            ))
        assert after_versions[first["id"]] == before_versions[first["id"]]
        assert after_versions[second["id"]] == before_versions[second["id"]] + 1
        assert get_preparation_run(
            settings.database_path, applied["id"]
        )["status"] == "completed"
