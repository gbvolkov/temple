from __future__ import annotations

import secrets
import sqlite3
import sys

from stage7_restore_smoke import client


def main() -> None:
    port, credentials_path, database_path = sys.argv[1:]
    base = f"http://127.0.0.1:{port}"
    public_request, _ = client(base)

    with sqlite3.connect(database_path) as connection:
        initial_submissions = connection.execute(
            "SELECT COUNT(*) FROM submissions"
        ).fetchone()[0]
        initial_new_submissions = connection.execute(
            "SELECT COUNT(*) FROM submissions WHERE status='new'"
        ).fetchone()[0]
        initial_outbox = connection.execute(
            "SELECT COUNT(*) FROM notification_outbox"
        ).fetchone()[0]

    status, note = public_request(
        "/api/public/submissions/prayer-note",
        method="POST",
        payload={
            "remembrance_type": "health",
            "names": ["Тестовая Записка"],
            "website": "",
        },
    )
    assert status == 201 and note["reference_code"].startswith("Z-"), note
    status, school = public_request(
        "/api/public/submissions/school-enrollment",
        method="POST",
        payload={
            "parent_name": "Тестовый Родитель",
            "contact": "+7 999 000-00-00",
            "child_name": "Тестовый Ребёнок",
            "child_age": 9,
            "comment": "Одноразовая проверка восстановленной копии",
            "consent": True,
            "website": "",
        },
    )
    assert status == 201 and school["reference_code"].startswith("S-"), school
    status, _ = public_request(
        "/api/public/submissions/prayer-note",
        method="POST",
        payload={
            "remembrance_type": "repose",
            "names": ["Робот"],
            "website": "https://spam.invalid",
        },
    )
    assert status == 201

    admin_request, admin_login = client(base)
    with open(credentials_path, encoding="utf-8") as source:
        import json

        credentials = json.load(source)
    admin_login(credentials["username"], credentials["password"])

    status, listing = admin_request("/api/admin/submissions?limit=10")
    assert (
        status == 200
        and listing["total"] == initial_submissions + 2
        and listing["new_total"] == initial_new_submissions + 2
    ), listing
    note_item = next(item for item in listing["items"] if item["reference_code"] == note["reference_code"])
    school_item = next(
        item for item in listing["items"] if item["reference_code"] == school["reference_code"]
    )
    status, detail = admin_request(f"/api/admin/submissions/{note_item['id']}")
    assert status == 200 and detail["payload"]["names"] == ["Тестовая Записка"], detail
    assert detail["notification"]["configured"] is False

    status, detail = admin_request(
        f"/api/admin/submissions/{note_item['id']}/status",
        method="PATCH",
        payload={"version": detail["version"], "status": "in_progress"},
    )
    assert status == 200 and detail["status"] == "in_progress" and detail["version"] == 2, detail
    status, conflict = admin_request(
        f"/api/admin/submissions/{note_item['id']}/status",
        method="PATCH",
        payload={"version": 1, "status": "done"},
    )
    assert status == 409, conflict
    status, retried = admin_request(
        f"/api/admin/submissions/{note_item['id']}/retry-notification",
        method="POST",
        payload={"version": detail["version"]},
    )
    assert status == 200 and any(
        event["action"] == "notification_retried" for event in retried["events"]
    ), retried

    editor_username = f"stage8.editor.{secrets.token_hex(6)}"
    editor_password = "Aa1!" + secrets.token_urlsafe(24)
    status, editor = admin_request(
        "/api/admin/users",
        method="POST",
        payload={"username": editor_username, "password": editor_password, "role": "editor"},
    )
    assert status == 201, editor
    editor_request, editor_login = client(base)
    editor_login(editor_username, editor_password)
    status, _ = editor_request("/api/admin/submissions")
    assert status == 403

    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
            == initial_submissions + 2
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0]
            == initial_outbox + 2
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        event_payload = "\n".join(
            row[0]
            for row in connection.execute(
                "SELECT details_json FROM submission_events WHERE submission_id IN (?, ?)",
                (note_item["id"], school_item["id"]),
            )
        )
        assert "Тестовая Записка" not in event_payload


if __name__ == "__main__":
    main()
