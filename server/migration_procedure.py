"""Sealed policy plan, atomic application, verification and guarded rollback.

The existing audit_events/revisions/media_events tables are the durable journal.
This technical CLI deliberately has no publication operation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from scripts.run_cms_regression import logical_database_digest, stable_source_report
from .baseline import readonly_connection, sha256_file
from .config import Settings
from .db import connect, row_to_content, transaction, utc_now
from .media_library import record_media_event, refresh_content_usages, resolve_media_path
from .media_reconciliation import reconcile_media
from .migration_acceptance import execute_audit_run, queue_audit
from .migration_analysis import analyze_migration, render_markdown
from .migration_policy import POLICY_VERSION
from .migration_preparation import (
    PREPARATION_RULES_VERSION, execute_preparation_run, queue_preparation, verify_preparation,
)
from .migrations import verify_migrations
from .search import sync_content_search
from .workflow import record_audit


FORMAT_VERSION = 1
APPLY_ACTION = "migration_policy_apply"
VERIFY_ACTION = "migration_policy_verify"
ROLLBACK_ACTION = "migration_policy_rollback"
CONTENT_FIELDS = (
    "content_type", "slug", "published_slug", "title", "status", "data_json", "legacy_id", "legacy_url",
    "migration_review_required", "published_version", "published_at", "scheduled_at", "reviewed_by", "reviewed_at", "deleted_at",
)


class ProcedureError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 409, details: Any = None):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "status": self.status, "details": self.details}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _schema_gate(database: Path) -> None:
    if not database.is_file():
        raise ProcedureError("DATABASE_NOT_FOUND", "Исходная БД не найдена", status=404)
    status = verify_migrations(database)
    if not status or any(item["state"] != "applied" for item in status):
        raise ProcedureError("SCHEMA_NOT_READY", "Сначала разверните актуальную схему CMS; процедура не обновляет рабочую схему")
    with closing(readonly_connection(database)) as c:
        if c.execute("PRAGMA quick_check").fetchone()[0] != "ok" or c.execute("PRAGMA foreign_key_check").fetchone():
            raise ProcedureError("DATABASE_NOT_HEALTHY", "Не пройдена проверка целостности БД")


def _actor(c: sqlite3.Connection, username: str) -> str:
    user = c.execute("SELECT id,role,is_active FROM users WHERE username=?", (username,)).fetchone()
    if not user or not user["is_active"] or user["role"] not in {"admin", "publisher"}:
        raise ProcedureError("FORBIDDEN", "Нужен активный администратор или выпускающий", status=403)
    return user["id"]


def _source_report(settings: Settings, ignored: set[str] | None = None) -> dict[str, Any]:
    report = stable_source_report(settings.database_path, settings.media_dir, settings.media_manifest_path)
    if ignored:
        files = sorted((p for p in settings.media_dir.rglob("*") if p.is_file()
                        and p.relative_to(settings.media_dir).as_posix() not in ignored), key=lambda p: p.as_posix())
        digest = hashlib.sha256()
        for p in files:
            stat = p.stat()
            digest.update(f"{p.relative_to(settings.media_dir).as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
        report["media"] = {"path": str(settings.media_dir.resolve()), "files": len(files),
                           "size_bytes": sum(p.stat().st_size for p in files), "inventory_sha256": digest.hexdigest()}
    return report


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


@contextmanager
def _procedure_lock(settings: Settings):
    """Kernel-owned lock: process termination releases it without a stale PID."""
    with (settings.database_path.resolve().parent / ".migration-policy.lock").open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if not lock.tell():
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ProcedureError("OPERATION_BUSY", "Другая процедура уже работает с этим хранилищем") from error
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_UN)


def create_plan(settings: Settings, bundle: Path, *, check_external: bool = False,
                progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Read-only preview. All snapshots and generated images stay in bundle."""
    _schema_gate(settings.database_path)
    with closing(readonly_connection(settings.database_path)) as c:
        for table in ("migration_audit_runs", "migration_preparation_runs", "media_reindex_jobs"):
            if c.execute(f"SELECT 1 FROM {table} WHERE status IN ('queued','running') LIMIT 1").fetchone():
                raise ProcedureError("BACKGROUND_WORK_ACTIVE", "Перед планированием завершите фоновые задания CMS")
    bundle = bundle.resolve()
    protected = (settings.database_path.resolve().parent, settings.media_dir.resolve())
    if bundle.exists() or any(bundle == p or p in bundle.parents or bundle in p.parents for p in protected):
        raise ProcedureError("UNSAFE_BUNDLE_PATH", "Выберите новый каталог операции вне runtime-хранилища")
    bundle.mkdir(parents=True, exist_ok=False)
    os.chmod(bundle, 0o700)
    baseline = bundle / "baseline.sqlite3"
    with closing(readonly_connection(settings.database_path)) as source, closing(sqlite3.connect(baseline)) as destination:
        source.backup(destination)
    # Reconciliation checks the backup, never opens the runtime DB for writing.
    media_check = reconcile_media(baseline, settings.media_dir, settings.media_manifest_path,
                                 missing_report=settings.missing_report_path, derivatives_dir=settings.derivatives_dir)
    if media_check["status"] != "ok":
        raise ProcedureError("MEDIA_NOT_READY", "Сначала выполните техническую сверку медиа", details=media_check)
    projection = bundle / "projection"
    report = analyze_migration(settings, check_external=check_external, progress=progress, projection_dir=projection)
    if not report["source_unchanged"] or logical_database_digest(baseline) != report["source_before"]["database"]["logical_sha256"]:
        raise ProcedureError("SOURCE_CHANGED", "Источник изменился во время подготовки; создайте новый план")
    if report["summary"]["technical_queue"]:
        raise ProcedureError("TECHNICAL_BLOCKERS", "Сначала устраните технические причины очереди", details=report["remaining_reasons"])
    actions, assets = [], []
    with closing(readonly_connection(baseline)) as before_db, closing(readonly_connection(projection / "projected.sqlite3")) as after_db:
        for item in report["items"]:
            before = dict(before_db.execute("SELECT * FROM contents WHERE id=?", (item["id"],)).fetchone())
            after = dict(after_db.execute("SELECT * FROM contents WHERE id=?", (item["id"],)).fetchone())
            proposed = {name: before[name] for name in CONTENT_FIELDS}
            if item["outcome"] == "archive":
                proposed.update(status="archived", migration_review_required=0, published_version=None,
                                scheduled_at=None, reviewed_by=None, reviewed_at=None, deleted_at=None)
            else:
                for name in ("content_type", "slug", "title", "data_json"):
                    proposed[name] = after[name]
                if json.loads(before["data_json"]) == json.loads(proposed["data_json"]):
                    proposed["data_json"] = before["data_json"]
                proposed["migration_review_required"] = int(item["outcome"] == "remaining_queue")
            if any(proposed[name] != before[name] for name in CONTENT_FIELDS):
                if before_db.execute("""SELECT 1 FROM migration_review_batch_items bi JOIN migration_review_batches b ON b.id=bi.batch_id
                    WHERE bi.content_id=? AND b.status IN ('draft','in_review') LIMIT 1""", (item["id"],)).fetchone():
                    raise ProcedureError("ACTIVE_BATCH", "Материал входит в незавершённую редакционную партию", details={"content_id": item["id"]})
                actions.append({"id": item["id"], "title": item["title"], "outcome": item["outcome"],
                                "before_hash": _hash(before), "before": before, "proposed": proposed})
        for row in after_db.execute("SELECT * FROM media WHERE source='migration_preparation'"):
            if before_db.execute("SELECT 1 FROM media WHERE id=? OR stored_name=?", (row["id"], row["stored_name"])).fetchone():
                continue
            relative = row["stored_name"]
            if not re.fullmatch(r"generated/leaflet-covers/[a-f0-9]{64}\.webp", relative):
                raise ProcedureError("UNEXPECTED_GENERATED_MEDIA", "Неизвестный тип создаваемого файла")
            file = resolve_media_path(projection / "media", relative)
            if not file.is_file() or sha256_file(file) != row["sha256"]:
                raise ProcedureError("GENERATED_MEDIA_INVALID", "Созданный файл не соответствует реестру")
            assets.append({"row": dict(row), "relative": relative, "sha256": row["sha256"]})
    now = datetime.now(UTC)
    _write_json(bundle / "report.json", report)
    (bundle / "report.md").write_text(render_markdown(report), encoding="utf-8")
    files = {"baseline.sqlite3": sha256_file(baseline), "projection/projected.sqlite3": sha256_file(projection / "projected.sqlite3"),
             "report.json": sha256_file(bundle / "report.json")}
    for asset in assets:
        files["projection/media/" + asset["relative"]] = asset["sha256"]
    plan = {"format_version": FORMAT_VERSION, "operation_id": str(uuid.uuid4()), "policy_version": POLICY_VERSION,
            "preparation_rules_version": PREPARATION_RULES_VERSION, "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=24)).isoformat(),
            "target_database": str(settings.database_path.resolve()), "target_media": str(settings.media_dir.resolve()),
            "source": report["source_before"], "summary": report["summary"], "items": actions,
            "media": assets, "files": files, "check_external": check_external}
    plan["seal"] = _hash(plan)
    _write_json(bundle / "plan.json", plan)
    for path in bundle.rglob("*"):
        if path.is_file():
            os.chmod(path, 0o600)
    return plan


def _load_plan(settings: Settings, bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve()
    try:
        plan = json.loads((bundle / "plan.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise ProcedureError("PLAN_NOT_READY", "Завершённый план не найден или повреждён") from error
    if plan.get("seal") != _hash({k: v for k, v in plan.items() if k != "seal"}):
        raise ProcedureError("PLAN_TAMPERED", "Контрольная сумма плана не совпадает")
    if plan.get("format_version") != FORMAT_VERSION or plan.get("policy_version") != POLICY_VERSION or plan.get("preparation_rules_version") != PREPARATION_RULES_VERSION:
        raise ProcedureError("RULES_CHANGED", "Версия правил изменилась; создайте новый план")
    if plan["target_database"] != str(settings.database_path.resolve()) or plan["target_media"] != str(settings.media_dir.resolve()):
        raise ProcedureError("WRONG_TARGET", "План создан для другого runtime-хранилища")
    if len({x["id"] for x in plan["items"]}) != len(plan["items"]):
        raise ProcedureError("PLAN_INVALID", "В плане повторяются материалы")
    for name, expected in plan["files"].items():
        path = (bundle / name).resolve()
        if bundle not in path.parents or not path.is_file() or sha256_file(path) != expected:
            raise ProcedureError("BUNDLE_TAMPERED", "Нарушена целостность артефактов операции", details={"file": name})
    # Cross-check the executable row payload against the separately sealed
    # projection and report, rather than trusting editable plan fields alone.
    report = json.loads((bundle / "report.json").read_text(encoding="utf-8"))
    if report["summary"] != plan["summary"] or report["source_before"] != plan["source"] or not report["source_unchanged"]:
        raise ProcedureError("PLAN_INVALID", "План расходится с результатом анализа")
    outcomes = {item["id"]: item["outcome"] for item in report["items"]}
    action_by_id = {item["id"]: item for item in plan["items"]}
    with closing(readonly_connection(bundle / "baseline.sqlite3")) as baseline, closing(readonly_connection(bundle / "projection/projected.sqlite3")) as projected:
        expected_ids = set()
        for identifier, outcome in outcomes.items():
            original = dict(baseline.execute("SELECT * FROM contents WHERE id=?", (identifier,)).fetchone())
            proposal = dict(projected.execute("SELECT * FROM contents WHERE id=?", (identifier,)).fetchone())
            expected = {name: original[name] for name in CONTENT_FIELDS}
            if outcome == "archive":
                expected.update(status="archived", migration_review_required=0, published_version=None,
                                scheduled_at=None, reviewed_by=None, reviewed_at=None, deleted_at=None)
            else:
                expected.update({name: proposal[name] for name in ("content_type", "slug", "title", "data_json")})
                if json.loads(original["data_json"]) == json.loads(expected["data_json"]):
                    expected["data_json"] = original["data_json"]
                expected["migration_review_required"] = int(outcome == "remaining_queue")
            if any(expected[name] != original[name] for name in CONTENT_FIELDS):
                expected_ids.add(identifier)
                item = action_by_id.get(identifier)
                if not item or item["before"] != original or item["proposed"] != expected or item["outcome"] != outcome:
                    raise ProcedureError("PLAN_INVALID", "Исполняемые изменения расходятся с проверенной проекцией")
        if expected_ids != {item["id"] for item in plan["items"]}:
            raise ProcedureError("PLAN_INVALID", "Изменён состав операции")
        expected_assets = []
        for row in projected.execute("SELECT * FROM media WHERE source='migration_preparation' ORDER BY stored_name"):
            if not baseline.execute("SELECT 1 FROM media WHERE id=? OR stored_name=?", (row["id"], row["stored_name"])).fetchone():
                expected_assets.append({"row": dict(row), "relative": row["stored_name"], "sha256": row["sha256"]})
        if sorted(plan["media"], key=lambda x: x["relative"]) != expected_assets:
            raise ProcedureError("PLAN_INVALID", "Изменён состав создаваемых файлов")
    for item in plan["items"]:
        before, proposed = item["before"], item["proposed"]
        if set(proposed) != set(CONTENT_FIELDS) or _hash(before) != item["before_hash"]:
            raise ProcedureError("PLAN_INVALID", "Некорректный снимок материала")
        if proposed["status"] == "published" or proposed["published_version"] is not None:
            raise ProcedureError("PUBLICATION_FORBIDDEN", "Процедура не публикует материалы")
        if item["outcome"] == "archive" and (before["content_type"] != "news" or not before["migration_review_required"]
                                              or proposed["data_json"] != before["data_json"]):
            raise ProcedureError("PLAN_INVALID", "Нарушено правило архива новостей")
        if before["content_type"] == "gallery":
            old = json.loads(before["data_json"]).get("photos") or []
            new = json.loads(proposed["data_json"]).get("photos") or []
            images = lambda photos: [p.get("image") if isinstance(p, dict) else p for p in photos]
            if images(old) != images(new):
                raise ProcedureError("PHOTOS_CHANGED", "План изменяет состав или порядок фото")
    return plan


def _events(c: sqlite3.Connection, operation_id: str, action: str) -> list[dict[str, Any]]:
    return [json.loads(row[0]) for row in c.execute(
        "SELECT details_json FROM audit_events WHERE action=? AND json_extract(details_json,'$.operation_id')=? ORDER BY created_at,id",
        (action, operation_id))]


def operation_status(settings: Settings, operation_id: str) -> dict[str, Any]:
    with closing(readonly_connection(settings.database_path)) as c:
        applied = _events(c, operation_id, APPLY_ACTION)
        rolled = _events(c, operation_id, ROLLBACK_ACTION)
        verified = _events(c, operation_id, VERIFY_ACTION)
    return {"operation_id": operation_id, "status": "rolled_back" if rolled else "verified" if verified else "applied" if applied else "not_applied",
            "items": len(applied), "verification": verified[-1].get("verification") if verified else None}


def _write_content(c: sqlite3.Connection, current: sqlite3.Row, values: dict[str, Any], actor_id: str) -> sqlite3.Row:
    c.execute("UPDATE contents SET " + ",".join(name + "=?" for name in CONTENT_FIELDS) + ",version=version+1,updated_at=? WHERE id=?",
              tuple(values[name] for name in CONTENT_FIELDS) + (utc_now(), current["id"]))
    after = c.execute("SELECT * FROM contents WHERE id=?", (current["id"],)).fetchone()
    c.execute("INSERT INTO revisions(content_id,version,snapshot_json,actor_id,created_at) VALUES(?,?,?,?,?)",
              (after["id"], after["version"], _json(row_to_content(after)), actor_id, utc_now()))
    refresh_content_usages(c, after["id"])
    sync_content_search(c, after["id"])
    return after


def _check_after(c: sqlite3.Connection, events: list[dict[str, Any]]) -> None:
    if not events or len(events) != events[0]["item_count"]:
        raise ProcedureError("JOURNAL_INCOMPLETE", "Журнал операции неполон")
    for event in events:
        row = c.execute("SELECT * FROM contents WHERE id=?", (event["content_id"],)).fetchone()
        if not row or _hash(dict(row)) != event["after_hash"]:
            raise ProcedureError("CONTENT_CHANGED", "После операции материал изменён; чужая правка сохранена", details={"content_id": event["content_id"]})


def _check_assets(c: sqlite3.Connection, events: list[dict[str, Any]], settings: Settings) -> None:
    for event in events:
        for asset in event.get("generated_media", []):
            row = c.execute("SELECT stored_name,sha256,status FROM media WHERE id=?", (asset["row"]["id"],)).fetchone()
            path = resolve_media_path(settings.media_dir, asset["relative"])
            if (not row or row["status"] != "ready" or row["sha256"] != asset["sha256"]
                    or row["stored_name"] != asset["relative"] or not path.is_file() or sha256_file(path) != asset["sha256"]):
                raise ProcedureError("MEDIA_CHANGED", "Созданный файл изменился или исчез", details={"file": asset["relative"]})


def _audit(settings: Settings, population: str = "pending", operation_id: str | None = None) -> dict[str, Any]:
    run = queue_audit(settings.database_path, actor_id=None,
                      scope={"population": population, "check_external": False, "policy_operation_id": operation_id})
    return execute_audit_run(settings.database_path, settings.schema_path, settings.media_dir, settings.site_dir,
                             run["id"], check_external=False)


def _verify_operation(settings: Settings, operation_id: str, *, username: str) -> dict[str, Any]:
    with closing(readonly_connection(settings.database_path)) as c:
        actor_id = _actor(c, username)
        events = _events(c, operation_id, APPLY_ACTION)
        if _events(c, operation_id, ROLLBACK_ACTION):
            raise ProcedureError("ALREADY_ROLLED_BACK", "Операция уже отменена")
        _check_after(c, events)
        _check_assets(c, events, settings)
        verified = _events(c, operation_id, VERIFY_ACTION)
        if verified:
            return {"operation_id": operation_id, "status": "verified", "replayed": True, **verified[-1]["verification"]}
    # Only this operation's unfinished verification jobs may be superseded.
    # The OS lock excludes a second verifier, and planning excludes earlier jobs.
    with transaction(settings.database_path) as c:
        own_audits = [row[0] for row in c.execute(
            "SELECT id FROM migration_audit_runs WHERE json_extract(scope_json,'$.policy_operation_id')=?", (operation_id,))]
        for audit_id in own_audits:
            for table, where in (("migration_audit_runs", "id"), ("migration_preparation_runs", "audit_run_id")):
                c.execute(f"UPDATE {table} SET status='failed',error=?,finished_at=? WHERE {where}=? AND status IN ('queued','running')",
                          ("Проверка процедуры возобновлена после остановки", utc_now(), audit_id))
    active = _audit(settings, "active", operation_id)
    with closing(readonly_connection(settings.database_path)) as c:
        for event in events:
            if event["outcome"] != "ready_for_publication":
                continue
            item = c.execute("SELECT blocker_count,warning_count FROM migration_audit_items WHERE run_id=? AND content_id=?",
                             (active["id"], event["content_id"])).fetchone()
            if not item or item["blocker_count"] or item["warning_count"]:
                raise ProcedureError("READY_ITEM_FAILED_AUDIT", "Принятый черновик не прошёл повторный аудит", details={"content_id": event["content_id"]})
    # Register the remaining queue in the existing preparation/semantic gate.
    pending = _audit(settings, operation_id=operation_id)
    preview = queue_preparation(settings.database_path, actor_id=actor_id)
    preview = execute_preparation_run(settings, preview["id"])
    if preview["counts"].get("technical_blocker", 0) or preview["counts"].get("changed", 0):
        raise ProcedureError("UNPLANNED_PREPARATION", "Повторная подготовка требует изменений вне плана", details=preview["counts"])
    applying = queue_preparation(settings.database_path, actor_id=actor_id, mode="apply", preview_run_id=preview["id"])
    execute_preparation_run(settings, applying["id"])
    post = _audit(settings, operation_id=operation_id)
    semantic = verify_preparation(settings.database_path)
    media = reconcile_media(settings.database_path, settings.media_dir, settings.media_manifest_path,
                            missing_report=settings.missing_report_path, derivatives_dir=settings.derivatives_dir)
    if media["status"] != "ok":
        raise ProcedureError("MEDIA_POSTCHECK_FAILED", "Итоговая сверка медиа не пройдена", details=media)
    result = {"active_audit": active["id"], "pending_audit": pending["id"], "post_audit": post["id"],
              "preparation_run": applying["id"], "semantic_ok": semantic["ok"], "media_ok": True}
    with transaction(settings.database_path) as c:
        _actor(c, username)
        _check_after(c, events)
        _check_assets(c, events, settings)
        current = c.execute("SELECT * FROM contents WHERE id=?", (events[0]["content_id"],)).fetchone()
        record_audit(c, content_id=current["id"], actor_id=actor_id, action=VERIFY_ACTION, before=current, after=current,
                     details={"operation_id": operation_id, "verification": result})
    return {"operation_id": operation_id, "status": "verified", "replayed": False, **result}


def _apply_plan(settings: Settings, bundle: Path, *, username: str) -> dict[str, Any]:
    plan = _load_plan(settings, bundle)
    operation_id = plan["operation_id"]
    created_files: list[Path] = []
    try:
        with transaction(settings.database_path) as c:
            actor_id = _actor(c, username)
            existing = _events(c, operation_id, APPLY_ACTION)
            if existing:
                if existing[0]["seal"] != plan["seal"]:
                    raise ProcedureError("OPERATION_CONFLICT", "ID операции уже использован для другого плана")
                if _events(c, operation_id, ROLLBACK_ACTION):
                    return {**operation_status(settings, operation_id), "replayed": True}
                _check_after(c, existing)
                replayed = True
            else:
                replayed = False
                if datetime.now(UTC) > datetime.fromisoformat(plan["expires_at"]):
                    raise ProcedureError("PLAN_EXPIRED", "План старше 24 часов; создайте новый")
                ignored = set()
                for asset in plan["media"]:
                    target = resolve_media_path(settings.media_dir, asset["relative"])
                    temporary = target.with_name(f".{operation_id}.{target.name}.tmp")
                    if temporary.exists():
                        ignored.add(temporary.relative_to(settings.media_dir.resolve()).as_posix())
                    if target.exists():
                        if sha256_file(target) != asset["sha256"]:
                            raise ProcedureError("MEDIA_CONFLICT", "На месте создаваемого файла уже есть другой файл")
                        ignored.add(asset["relative"])
                if _source_report(settings, ignored) != plan["source"]:
                    raise ProcedureError("PLAN_STALE", "БД, медиа или manifest изменились; создайте новый план")
                for item in plan["items"]:
                    row = c.execute("SELECT * FROM contents WHERE id=?", (item["id"],)).fetchone()
                    if not row or _hash(dict(row)) != item["before_hash"]:
                        raise ProcedureError("PLAN_STALE", "Материал изменён после анализа", details={"content_id": item["id"]})
                    if c.execute("""SELECT 1 FROM migration_review_batch_items bi JOIN migration_review_batches b ON b.id=bi.batch_id
                        WHERE bi.content_id=? AND b.status IN ('draft','in_review') LIMIT 1""", (item["id"],)).fetchone():
                        raise ProcedureError("ACTIVE_BATCH", "Сначала завершите или отмените активную редакционную партию")
                for asset in plan["media"]:
                    target = resolve_media_path(settings.media_dir, asset["relative"])
                    temporary = target.with_name(f".{operation_id}.{target.name}.tmp")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary.unlink(missing_ok=True)
                    if not target.exists():
                        shutil.copy2(resolve_media_path(bundle.resolve() / "projection/media", asset["relative"]), temporary)
                        with temporary.open("r+b") as f:
                            os.fsync(f.fileno())
                        if sha256_file(temporary) != asset["sha256"]:
                            raise ProcedureError("MEDIA_COPY_FAILED", "Контрольная сумма копии не совпадает")
                        os.replace(temporary, target)
                        created_files.append(target)
                    row = asset["row"]
                    columns = [r["name"] for r in c.execute("PRAGMA table_info(media)")]
                    c.execute("INSERT INTO media(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")",
                              tuple(row[name] for name in columns))
                    record_media_event(c, row["id"], actor_id, "migration_policy_generate",
                                       {"operation_id": operation_id, "sha256": asset["sha256"], "source": json.loads(row["metadata_json"])})
                for index, item in enumerate(plan["items"]):
                    before = c.execute("SELECT * FROM contents WHERE id=?", (item["id"],)).fetchone()
                    after = _write_content(c, before, item["proposed"], actor_id)
                    c.execute("""UPDATE migration_review_issues SET status=?,resolved_by=?,resolved_at=?,resolution_note=?
                        WHERE content_id=? AND status IN ('open','acknowledged')""",
                        ("resolved" if item["outcome"] == "archive" else "superseded", actor_id, utc_now(),
                         f"Политика {POLICY_VERSION}; операция {operation_id}", item["id"]))
                    record_audit(c, content_id=item["id"], actor_id=actor_id, action=APPLY_ACTION, before=before, after=after,
                                 details={"operation_id": operation_id, "seal": plan["seal"], "policy_version": POLICY_VERSION,
                                          "content_id": item["id"], "item_count": len(plan["items"]), "outcome": item["outcome"],
                                          "before": dict(before), "after_hash": _hash(dict(after)),
                                          "generated_media": plan["media"] if index == 0 else []})
    except Exception:
        # A failed SQL transaction has no corresponding media registration.
        for path in created_files:
            with closing(readonly_connection(settings.database_path)) as c:
                registered = c.execute("SELECT 1 FROM media WHERE stored_name=?", (path.relative_to(settings.media_dir.resolve()).as_posix(),)).fetchone()
            if not registered:
                path.unlink(missing_ok=True)
        raise
    if not plan["items"]:
        return {"operation_id": operation_id, "status": "no_changes", "replayed": False}
    try:
        result = _verify_operation(settings, operation_id, username=username)
    except Exception as error:
        try:
            _rollback_operation(settings, operation_id, username=username)
        except Exception as rollback_error:
            raise ProcedureError("POSTCHECK_FAILED_ROLLBACK_BLOCKED", "Проверка не пройдена; откат заблокирован последующей правкой",
                                 details={"operation_id": operation_id, "error": str(error), "rollback_error": str(rollback_error)}) from error
        raise ProcedureError("POSTCHECK_FAILED_ROLLED_BACK", "Итоговая проверка не пройдена; изменения отменены", details={"error": str(error)}) from error
    result["replayed"] = replayed
    _write_json(bundle.resolve() / "receipt.json", result)
    return result


def _rollback_operation(settings: Settings, operation_id: str, *, username: str) -> dict[str, Any]:
    with transaction(settings.database_path) as c:
        actor_id = _actor(c, username)
        if _events(c, operation_id, ROLLBACK_ACTION):
            return {"operation_id": operation_id, "status": "rolled_back", "replayed": True}
        events = _events(c, operation_id, APPLY_ACTION)
        _check_after(c, events)
        latest = c.execute("SELECT details_json FROM audit_events WHERE action=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (APPLY_ACTION,)).fetchone()
        if latest and json.loads(latest[0])["operation_id"] != operation_id:
            raise ProcedureError("NOT_LATEST_OPERATION", "Сначала отмените более позднюю операцию")
        for event in events:
            before = c.execute("SELECT * FROM contents WHERE id=?", (event["content_id"],)).fetchone()
            after = _write_content(c, before, event["before"], actor_id)
            record_audit(c, content_id=before["id"], actor_id=actor_id, action=ROLLBACK_ACTION, before=before, after=after,
                         details={"operation_id": operation_id, "restored_version": event["before"]["version"]})
            for asset in event.get("generated_media", []):
                record_media_event(c, asset["row"]["id"], actor_id, "migration_policy_media_retained_on_rollback",
                                   {"operation_id": operation_id, "sha256": asset["sha256"]})
    audit = _audit(settings, operation_id=operation_id)
    return {"operation_id": operation_id, "status": "rolled_back", "replayed": False, "audit_run": audit["id"]}


def apply_plan(settings: Settings, bundle: Path, *, username: str) -> dict[str, Any]:
    _schema_gate(settings.database_path)
    with _procedure_lock(settings):
        return _apply_plan(settings, bundle, username=username)


def verify_operation(settings: Settings, operation_id: str, *, username: str) -> dict[str, Any]:
    _schema_gate(settings.database_path)
    with _procedure_lock(settings):
        return _verify_operation(settings, operation_id, username=username)


def rollback_operation(settings: Settings, operation_id: str, *, username: str) -> dict[str, Any]:
    _schema_gate(settings.database_path)
    with _procedure_lock(settings):
        return _rollback_operation(settings, operation_id, username=username)


def main() -> int:
    defaults = Settings.from_env()
    parser = argparse.ArgumentParser(description="План, применение, проверка и откат миграционной политики без публикации")
    parser.add_argument("command", choices=("plan", "apply", "verify", "rollback", "status"))
    parser.add_argument("--database", type=Path, default=defaults.database_path)
    parser.add_argument("--media", type=Path, default=defaults.media_dir)
    parser.add_argument("--manifest", type=Path, default=defaults.media_manifest_path)
    parser.add_argument("--missing-report", type=Path, default=defaults.missing_report_path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--operation-id")
    parser.add_argument("--actor-user")
    parser.add_argument("--check-external", action="store_true")
    args = parser.parse_args()
    from dataclasses import replace
    settings = replace(defaults, database_path=args.database, media_dir=args.media,
                       media_manifest_path=args.manifest, missing_media_report_path=args.missing_report)
    try:
        if args.command == "plan":
            if not args.bundle:
                parser.error("plan требует --bundle с новым каталогом")
            result = create_plan(settings, args.bundle, check_external=args.check_external,
                                 progress=lambda message: print(message, file=sys.stderr, flush=True))
            result = {key: result[key] for key in ("operation_id", "seal", "summary", "expires_at")}
        elif args.command == "status":
            if not args.operation_id:
                parser.error("status требует --operation-id")
            result = operation_status(settings, args.operation_id)
        else:
            if not args.actor_user:
                parser.error("операция требует --actor-user")
            if args.command == "apply":
                if not args.bundle:
                    parser.error("apply требует --bundle")
                result = apply_plan(settings, args.bundle, username=args.actor_user)
            else:
                if not args.operation_id:
                    parser.error("операция требует --operation-id")
                function = verify_operation if args.command == "verify" else rollback_operation
                result = function(settings, args.operation_id, username=args.actor_user)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ProcedureError as error:
        print(json.dumps(error.payload(), ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
