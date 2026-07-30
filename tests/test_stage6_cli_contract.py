from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from server.db import init_database


ROOT = Path(__file__).resolve().parents[1]


def run_module(module: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONUTF8": "1"},
    )


def test_preparation_cli_success_and_domain_error_are_versioned_json(tmp_path: Path) -> None:
    database = tmp_path / "cms.sqlite3"
    init_database(database)

    status = run_module(
        "server.migration_preparation", "status", "--database", str(database)
    )
    assert status.returncode == 0
    status_payload = json.loads(status.stdout)
    assert status_payload["contract_version"] == "1.1"
    assert status_payload["editorial_gate"]["code"] == "APPLY_REQUIRED"
    assert status.stderr == ""

    verification = run_module(
        "server.migration_preparation", "verify", "--database", str(database)
    )
    assert verification.returncode == 2
    error_payload = json.loads(verification.stdout)
    assert error_payload == {
        "ok": False,
        "contract_version": "1.1",
        "error": {
            "code": "APPLY_REQUIRED",
            "message": "Проверка подготовки не пройдена: fk=0, code=APPLY_REQUIRED",
            "details": {
                "foreign_keys": 0,
                "ok": False,
                "code": "APPLY_REQUIRED",
                "message": "Нет завершённого применения",
            },
        },
    }
    assert verification.stderr == ""


def test_acceptance_cli_missing_actor_is_sanitized_json(tmp_path: Path) -> None:
    database = tmp_path / "cms.sqlite3"
    init_database(database)
    result = run_module(
        "server.migration_acceptance",
        "create-pilot",
        "--database",
        str(database),
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["contract_version"] == "1.1"
    assert payload["error"]["code"] == "ACTOR_ID_REQUIRED"
    assert str(tmp_path) not in result.stdout
    assert result.stderr == ""
