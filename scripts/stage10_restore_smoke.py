from __future__ import annotations

import json
import sqlite3
import sys

from stage7_restore_smoke import client


def main() -> None:
    port, credentials_path, database_path = sys.argv[1:]
    base = f"http://127.0.0.1:{port}"
    with open(credentials_path, encoding="utf-8") as source:
        credentials = json.load(source)

    with sqlite3.connect(database_path) as connection:
        before = {
            "contents": connection.execute("SELECT COUNT(*) FROM contents").fetchone()[0],
            "revisions": connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0],
            "redirects": connection.execute("SELECT COUNT(*) FROM redirects").fetchone()[0],
            "media": connection.execute("SELECT COUNT(*) FROM media").fetchone()[0],
            "flags": connection.execute(
                "SELECT COUNT(*) FROM contents WHERE migration_review_required=1"
            ).fetchone()[0],
            "workflow": connection.execute(
                "SELECT group_concat(status||':'||amount,',') FROM (SELECT status,COUNT(*) amount FROM contents GROUP BY status ORDER BY status)"
            ).fetchone()[0],
        }
        actor_id = connection.execute(
            "SELECT id FROM users WHERE username=?", (credentials["username"],)
        ).fetchone()[0]

    admin_request, admin_login = client(base)
    admin_login(credentials["username"], credentials["password"])

    status, dashboard = admin_request("/api/admin/migration")
    assert status == 200 and "acceptance" in dashboard, dashboard
    assert dashboard["acceptance"]["totals"]["review_required"] == before["flags"]

    status, batches = admin_request("/api/admin/migration/batches")
    assert status == 200, batches
    status, pilot = admin_request("/api/admin/migration/batches/pilot", method="POST")
    assert status == 201 and pilot["status"] == "draft", pilot
    assert 1 <= len(pilot["items"]) <= 50
    assert all(item["sampled"] for item in pilot["items"])
    assert all(item["disposition"] == "pending" for item in pilot["items"])

    candidate = pilot["items"][0]
    status, _ = admin_request(
        f"/api/admin/contents/{candidate['content_id']}/review",
        method="POST",
        payload={"version": candidate["current_content_version"]},
    )
    assert status == 409
    status, _ = admin_request(
        "/api/admin/content-bulk",
        method="POST",
        payload={
            "action": "review",
            "items": [{"id": candidate["content_id"], "version": candidate["current_content_version"]}],
        },
    )
    assert status == 409

    with sqlite3.connect(database_path) as connection:
        after = {
            "contents": connection.execute("SELECT COUNT(*) FROM contents").fetchone()[0],
            "revisions": connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0],
            "redirects": connection.execute("SELECT COUNT(*) FROM redirects").fetchone()[0],
            "media": connection.execute("SELECT COUNT(*) FROM media").fetchone()[0],
            "flags": connection.execute(
                "SELECT COUNT(*) FROM contents WHERE migration_review_required=1"
            ).fetchone()[0],
            "workflow": connection.execute(
                "SELECT group_concat(status||':'||amount,',') FROM (SELECT status,COUNT(*) amount FROM contents GROUP BY status ORDER BY status)"
            ).fetchone()[0],
        }
        assert before == after
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM migration_audit_runs WHERE status='completed'"
        ).fetchone()[0] >= 1
        assert connection.execute(
            "SELECT COUNT(*) FROM migration_review_batches WHERE id=? AND created_by=? AND status='draft'",
            (pilot["id"], actor_id),
        ).fetchone()[0] == 1


if __name__ == "__main__":
    main()
