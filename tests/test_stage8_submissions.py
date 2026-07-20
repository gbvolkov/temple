from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from server.app import create_app
from server.config import Settings
from server.db import init_database
from server.submissions import (
    SubmissionRateLimit,
    cleanup_expired_submissions,
    client_ip,
    create_submission,
    notification_configured,
    process_notification_once,
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
        environment="test",
        bootstrap_user="admin",
        bootstrap_password="test-password",
        session_hours=1,
        submission_ip_hash_secret="stage8-test-hmac-secret",
        submission_worker_interval_seconds=3600,
    )


def login(client: TestClient, username: str = "admin", password: str = "test-password") -> dict[str, str]:
    response = client.post("/api/admin/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def create_user(client: TestClient, headers: dict[str, str], username: str, role: str) -> None:
    response = client.post(
        "/api/admin/users",
        headers=headers,
        json={"username": username, "password": "Strong-Password-2026!", "role": role},
    )
    assert response.status_code == 201, response.text


def note_payload(name: str = "Александр") -> dict:
    return {"remembrance_type": "health", "names": [name], "website": ""}


def school_payload() -> dict:
    return {
        "parent_name": "Мария Иванова",
        "contact": "+7 (999) 123-45-67",
        "child_name": "Анна Иванова",
        "child_age": 9,
        "comment": "Хотим прийти на первое занятие.",
        "consent": True,
        "website": "",
    }


def test_migration_7_is_idempotent_and_preserves_existing_tables(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            """INSERT INTO contents(
                 id,content_type,slug,title,status,data_json,migration_review_required,version,
                 created_at,updated_at
               ) VALUES('stage8-content','page','stage8','Stage 8','draft','{}',0,1,'now','now')"""
        )
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("contents", "users", "media", "redirects")
        }
    init_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 8
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 8
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        } == before
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        } >= {"submissions", "notification_outbox", "submission_events"}


def test_public_note_is_db_first_deduplicated_and_honeypot_is_not_stored(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        first = client.post("/api/public/submissions/prayer-note", json=note_payload())
        assert first.status_code == 201, first.text
        reference = first.json()["reference_code"]
        assert reference.startswith("Z-")
        duplicate = client.post("/api/public/submissions/prayer-note", json=note_payload())
        assert duplicate.status_code == 201
        assert duplicate.json()["reference_code"] == reference
        bot = client.post(
            "/api/public/submissions/prayer-note",
            json={**note_payload("Пётр"), "website": "https://spam.example"},
        )
        assert bot.status_code == 201
        assert bot.json()["reference_code"] != reference
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM submission_events").fetchone()[0] == 1
        row = connection.execute(
            "SELECT payload_json,ip_hash,payload_fingerprint FROM submissions"
        ).fetchone()
        assert json.loads(row[0]) == {"names": ["Александр"], "remembrance_type": "health"}
        assert len(row[1]) == len(row[2]) == 64
        assert "testclient" not in settings.database_path.read_bytes().decode("utf-8", errors="ignore")


def test_public_validation_payload_limit_and_burst_rate_limit(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.post(
            "/api/public/submissions/prayer-note",
            json={"remembrance_type": "health", "names": [], "website": ""},
        ).status_code == 422
        assert client.post(
            "/api/public/submissions/prayer-note",
            json={"remembrance_type": "health", "names": ["<b>Иван</b>"], "website": ""},
        ).status_code == 422
        assert client.post(
            "/api/public/submissions/school-enrollment",
            json={**school_payload(), "consent": False},
        ).status_code == 422
        assert client.post(
            "/api/public/submissions/school-enrollment",
            json={**school_payload(), "contact": "not-a-contact"},
        ).status_code == 422

    limited_settings = settings_for(tmp_path / "limited")
    with TestClient(create_app(limited_settings)) as client:
        for name in ("Анна", "Мария", "Пётр"):
            assert client.post(
                "/api/public/submissions/prayer-note", json=note_payload(name)
            ).status_code == 201
        limited = client.post(
            "/api/public/submissions/prayer-note", json=note_payload("Иоанн")
        )
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) >= 1

    large_settings = settings_for(tmp_path / "large")
    body = json.dumps(note_payload()) + (" " * 33_000)
    with TestClient(create_app(large_settings)) as client:
        response = client.post(
            "/api/public/submissions/prayer-note",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413


def test_public_configuration_unicode_and_forwarded_ip_rules(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.post(
            "/api/public/submissions/prayer-note", json=note_payload("И")
        ).status_code == 201
        assert client.post(
            "/api/public/submissions/prayer-note", json=note_payload("И\tван")
        ).status_code == 422
    unavailable = replace(settings_for(tmp_path / "unavailable"), submission_ip_hash_secret=None)
    with TestClient(create_app(unavailable)) as client:
        assert client.post(
            "/api/public/submissions/prayer-note", json=note_payload()
        ).status_code == 503

    trusted = replace(settings, submission_trusted_proxy_networks=("10.0.0.0/8",))
    trusted_request = Request({
        "type": "http", "method": "POST", "path": "/", "headers": [
            (b"x-real-ip", b"203.0.113.15"),
        ], "client": ("10.0.0.2", 1234), "scheme": "http", "server": ("test", 80),
    })
    assert client_ip(trusted_request, trusted) == "203.0.113.15"
    assert client_ip(trusted_request, settings) == "10.0.0.2"


def test_empty_smtp_environment_is_a_valid_disabled_mode(monkeypatch) -> None:
    for name in (
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
        "SMTP_SECURITY", "SUBMISSION_NOTIFY_TO",
    ):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv(
        "SUBMISSION_IP_HASH_SECRET",
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )
    settings = Settings.from_env()
    assert settings.smtp_port == 587
    assert settings.smtp_security == "starttls"
    assert notification_configured(settings) is False


def test_persistent_hour_and_day_limits(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    for suffix in "ABCDE":
        create_submission(
            settings.database_path, settings, submission_type="prayer_note",
            payload={"remembrance_type": "health", "names": [f"Name {suffix}"]},
            ip_hash="hour" * 16,
        )
    with pytest.raises(SubmissionRateLimit) as hour_error:
        create_submission(
            settings.database_path, settings, submission_type="prayer_note",
            payload={"remembrance_type": "health", "names": ["Name F"]},
            ip_hash="hour" * 16,
        )
    assert hour_error.value.retry_after == 3600

    day_settings = settings_for(tmp_path / "daily")
    init_database(day_settings.database_path)
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds")
    with sqlite3.connect(day_settings.database_path) as connection:
        for index in range(20):
            connection.execute(
                """INSERT INTO submissions(
                     id,reference_code,submission_type,status,payload_json,ip_hash,
                     payload_fingerprint,version,created_at,updated_at
                   ) VALUES(?,?,?,'new','{}',?,?,1,?,?)""",
                (f"daily-{index}", f"D-{index}", "prayer_note", "day" * 16, f"fp-{index}", old, old),
            )
    with pytest.raises(SubmissionRateLimit) as day_error:
        create_submission(
            day_settings.database_path, day_settings, submission_type="prayer_note",
            payload={"remembrance_type": "health", "names": ["Daily Name"]},
            ip_hash="day" * 16,
        )
    assert day_error.value.retry_after == 86400


def test_school_submission_stores_consent_and_admin_queue_enforces_roles(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    app = create_app(settings)
    with (
        TestClient(app) as admin_client,
        TestClient(app) as editor_client,
        TestClient(app) as publisher_client,
    ):
        created = admin_client.post(
            "/api/public/submissions/school-enrollment", json=school_payload()
        )
        assert created.status_code == 201, created.text
        admin_headers = login(admin_client)
        create_user(admin_client, admin_headers, "stage8.editor", "editor")
        create_user(admin_client, admin_headers, "stage8.publisher", "publisher")
        login(editor_client, "stage8.editor", "Strong-Password-2026!")
        publisher_headers = login(
            publisher_client, "stage8.publisher", "Strong-Password-2026!"
        )
        assert editor_client.get("/api/admin/submissions").status_code == 403
        listing = publisher_client.get("/api/admin/submissions?type=school_enrollment")
        assert listing.status_code == 200
        assert listing.headers["Cache-Control"] == "no-store"
        assert listing.json()["new_total"] == 1
        item = listing.json()["items"][0]
        assert "payload" not in item
        detail = publisher_client.get(f"/api/admin/submissions/{item['id']}").json()
        assert detail["payload"]["parent_name"] == school_payload()["parent_name"]
        assert detail["payload"]["consent"] is True
        assert detail["payload"]["consented_at"]
        assert publisher_client.patch(
            f"/api/admin/submissions/{item['id']}/status",
            json={"version": item["version"], "status": "in_progress"},
        ).status_code == 403
        in_progress = publisher_client.patch(
            f"/api/admin/submissions/{item['id']}/status",
            headers=publisher_headers,
            json={"version": item["version"], "status": "in_progress"},
        )
        assert in_progress.status_code == 200, in_progress.text
        assert in_progress.json()["version"] == 2
        assert publisher_client.patch(
            f"/api/admin/submissions/{item['id']}/status",
            headers=publisher_headers,
            json={"version": item["version"], "status": "done"},
        ).status_code == 409
        done = publisher_client.patch(
            f"/api/admin/submissions/{item['id']}/status",
            headers=publisher_headers,
            json={"version": 2, "status": "done"},
        )
        assert done.status_code == 200
        assert done.json()["closed_at"]
        assert publisher_client.patch(
            f"/api/admin/submissions/{item['id']}/status",
            headers=publisher_headers,
            json={"version": 3, "status": "new"},
        ).status_code == 409
        reopened = publisher_client.patch(
            f"/api/admin/submissions/{item['id']}/status",
            headers=publisher_headers,
            json={"version": 3, "status": "in_progress"},
        )
        assert reopened.status_code == 200
        assert reopened.json()["closed_at"] is None
        retried = publisher_client.post(
            f"/api/admin/submissions/{item['id']}/retry-notification",
            headers=publisher_headers,
            json={"version": reopened.json()["version"]},
        )
        assert retried.status_code == 200
        assert any(
            event["action"] == "notification_retried"
            for event in retried.json()["events"]
        )


def test_outbox_failure_is_safe_and_success_is_recorded(tmp_path: Path, monkeypatch) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    reference, created = create_submission(
        settings.database_path,
        settings,
        submission_type="prayer_note",
        payload={"remembrance_type": "health", "names": ["Секретное Имя"]},
        ip_hash="a" * 64,
    )
    assert created and reference.startswith("Z-")
    mail_settings = replace(
        settings,
        smtp_host="smtp.example.org",
        smtp_from="site@example.org",
        submission_notify_to=("recipient@example.org",),
    )

    def fail_delivery(*_args):
        raise RuntimeError("Секретное Имя must never be persisted in the error")

    monkeypatch.setattr("server.submissions.send_notification", fail_delivery)
    assert process_notification_once(mail_settings) is True
    with sqlite3.connect(settings.database_path) as connection:
        status, attempts, error = connection.execute(
            "SELECT status,attempts,last_error FROM notification_outbox"
        ).fetchone()
        assert status == "pending" and attempts == 1
        assert "Секретное" not in error
        connection.execute(
            "UPDATE notification_outbox SET next_attempt_at='2000-01-01T00:00:00+00:00'"
        )

    monkeypatch.setattr("server.submissions.send_notification", lambda *_args: None)
    assert process_notification_once(mail_settings) is True
    with sqlite3.connect(settings.database_path) as connection:
        status, attempts, error, sent_at = connection.execute(
            "SELECT status,attempts,last_error,sent_at FROM notification_outbox"
        ).fetchone()
        assert status == "sent" and attempts == 2 and error is None and sent_at
        assert connection.execute(
            "SELECT COUNT(*) FROM submission_events WHERE action='notification_sent'"
        ).fetchone()[0] == 1


def test_outbox_max_attempts_and_stale_claim_recovery(tmp_path: Path, monkeypatch, caplog) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    create_submission(
        settings.database_path, settings, submission_type="prayer_note",
        payload={"remembrance_type": "health", "names": ["Private Name"]},
        ip_hash="b" * 64,
    )
    mail_settings = replace(
        settings, smtp_host="smtp.example.org", smtp_from="site@example.org",
        submission_notify_to=("recipient@example.org",),
    )

    def fail_delivery(*_args):
        raise RuntimeError("Private Name must not enter logs")

    monkeypatch.setattr("server.submissions.send_notification", fail_delivery)
    for _ in range(8):
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "UPDATE notification_outbox SET next_attempt_at='2000-01-01T00:00:00+00:00'"
            )
        assert process_notification_once(mail_settings) is True
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute(
            "SELECT status,attempts FROM notification_outbox"
        ).fetchone() == ("failed", 8)
    assert "Private Name" not in caplog.text
    assert process_notification_once(mail_settings) is False

    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            """UPDATE notification_outbox SET status='sending',attempts=0,
               locked_at='2000-01-01T00:00:00+00:00',next_attempt_at='2000-01-01T00:00:00+00:00'"""
        )
    monkeypatch.setattr("server.submissions.send_notification", lambda *_args: None)
    assert process_notification_once(mail_settings) is True
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute(
            "SELECT status,attempts FROM notification_outbox"
        ).fetchone() == ("sent", 1)


def test_retention_deletes_only_closed_expired_submissions(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    now = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    with sqlite3.connect(settings.database_path) as connection:
        for index, (kind, status, age) in enumerate((
            ("prayer_note", "done", 30),
            ("school_enrollment", "spam", 180),
            ("prayer_note", "new", 365),
            ("school_enrollment", "done", 179),
        )):
            submission_id = f"submission-{index}"
            created = (now - timedelta(days=age + 1)).isoformat()
            closed = (now - timedelta(days=age)).isoformat() if status in {"done", "spam"} else None
            connection.execute(
                """INSERT INTO submissions(
                     id,reference_code,submission_type,status,payload_json,ip_hash,payload_fingerprint,
                     version,created_at,updated_at,closed_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    submission_id, f"R-{index}", kind, status, "{}", f"ip-{index}",
                    f"payload-{index}", 1, created, created, closed,
                ),
            )
            connection.execute(
                """INSERT INTO notification_outbox(
                     id,submission_id,status,attempts,next_attempt_at,created_at,updated_at
                   ) VALUES(?,?,'pending',0,?,?,?)""",
                (f"outbox-{index}", submission_id, created, created, created),
            )
    assert cleanup_expired_submissions(settings.database_path, now=now) == 2
    with sqlite3.connect(settings.database_path) as connection:
        assert set(connection.execute("SELECT id FROM submissions")) == {
            ("submission-2",), ("submission-3",),
        }
        assert connection.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
