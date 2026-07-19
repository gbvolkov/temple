from __future__ import annotations

import http.cookiejar
import json
import secrets
import sys
import urllib.error
import urllib.request


def client(base: str):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    csrf = ""

    def request(path: str, *, method: str = "GET", payload=None):
        nonlocal csrf
        headers = {}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        if csrf and method not in {"GET", "HEAD"}:
            headers["X-CSRF-Token"] = csrf
        call = urllib.request.Request(base + path, data=body, headers=headers, method=method)
        try:
            with opener.open(call, timeout=20) as response:
                raw = response.read().decode()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read().decode()
            return error.code, json.loads(raw) if raw else None

    def login(username: str, password: str):
        nonlocal csrf
        status, body = request(
            "/api/admin/login", method="POST",
            payload={"username": username, "password": password},
        )
        assert status == 200, body
        csrf = body["csrf_token"]
        return body["user"]

    return request, login


def main() -> None:
    port, credentials_path = sys.argv[1:]
    base = f"http://127.0.0.1:{port}"
    with open(credentials_path, encoding="utf-8") as source:
        credentials = json.load(source)

    admin_request, admin_login = client(base)
    admin_login(credentials["username"], credentials["password"])
    editor_password = "Aa1!" + secrets.token_urlsafe(24)
    publisher_password = "Aa1!" + secrets.token_urlsafe(24)

    status, editor = admin_request(
        "/api/admin/users", method="POST",
        payload={"username": "stage7.editor", "password": editor_password, "role": "editor"},
    )
    assert status == 201, editor
    status, publisher = admin_request(
        "/api/admin/users", method="POST",
        payload={"username": "stage7.publisher", "password": publisher_password, "role": "publisher"},
    )
    assert status == 201, publisher

    editor_request, editor_login = client(base)
    editor_login("stage7.editor", editor_password)
    status, _ = editor_request("/api/admin/users")
    assert status == 403

    publisher_request, publisher_login = client(base)
    publisher_login("stage7.publisher", publisher_password)
    status, _ = publisher_request("/api/admin/users")
    assert status == 403
    status, draft = publisher_request(
        "/api/admin/contents", method="POST",
        payload={
            "content_type": "page", "title": "Stage 7 disposable workflow check",
            "data": {"placement": "standalone", "body": [], "related_content": []},
        },
    )
    assert status == 201, draft
    bulk = {"action": "archive", "items": [{"id": draft["id"], "version": draft["version"]}]}
    status, _ = editor_request("/api/admin/content-bulk", method="POST", payload=bulk)
    assert status == 403
    status, result = publisher_request("/api/admin/content-bulk", method="POST", payload=bulk)
    assert status == 200 and result["items"][0]["status"] == "archived", result
    status, audit = publisher_request(f"/api/admin/contents/{draft['id']}/audit-events")
    assert status == 200 and any(
        event["action"] == "archive" and event["details"].get("bulk") is True
        for event in audit["items"]
    )

    status, terminated = admin_request(
        f"/api/admin/users/{editor['id']}/terminate-sessions", method="POST",
        payload={"version": editor["version"]},
    )
    assert status == 200 and terminated["closed_sessions"] == 1, terminated
    status, _ = editor_request("/api/admin/contents")
    assert status == 401

    status, changed = publisher_request(
        "/api/admin/change-password", method="POST",
        payload={"current_password": publisher_password, "new_password": "Bb2!" + secrets.token_urlsafe(24)},
    )
    assert status == 200 and changed["reauthenticate"] is True, changed
    status, session = publisher_request("/api/admin/session")
    assert status == 200 and session == {"authenticated": False}, session

    status, events = admin_request("/api/admin/user-events")
    assert status == 200
    actions = {event["action"] for event in events["items"]}
    assert {"user_create", "sessions_terminated", "password_change"} <= actions
    for event in events["items"]:
        assert not any(
            marker in key.casefold()
            for key in event["details"]
            for marker in ("password", "token", "secret", "hash", "csrf")
        )


if __name__ == "__main__":
    main()
