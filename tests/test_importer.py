import json
from pathlib import Path

from server.db import connect
from server.importer import build_records, document_number, run_import


ROOT = Path(__file__).resolve().parents[1]


def test_leaflet_number_parser_does_not_treat_year_as_issue_number():
    assert document_number("№ 148 Май — июль 2026") == 148
    assert document_number("15 СЕНТЯБРЬ 2007") == 15
    assert document_number("СТРАСТНАЯ СЕДМИЦА 2011") is None


def test_readonly_snapshot_keeps_every_document(tmp_path):
    sections = json.loads((ROOT / "current-sections.json").read_text(encoding="utf-8"))
    leaflet_section = next(section for section in sections if "prihodskoy-listok" in section["path"])
    source_documents = len(leaflet_section.get("documents", []))
    records, rejected = build_records(sections)
    issues = [item for item in records if item["content_type"] == "leaflet_issue"]
    specials = [item for item in records if item["data"].get("migration_note", "").startswith("Специальный")]
    preserved_documents = sum(1 + len(item["data"].get("attachments", [])) for item in issues) + len(specials)

    assert rejected == []
    assert len(issues) == 148
    assert preserved_documents == source_documents


def test_import_is_idempotent_and_creates_redirects(tmp_path):
    database = tmp_path / "cms.sqlite3"
    source = ROOT / "current-sections.json"

    first = run_import(database, source)
    second = run_import(database, source)

    assert first["records_found"] == 167
    assert first["imported"] == 167
    assert first["errors"] == 0
    assert second["imported"] == 0
    assert second["updated"] == 0
    assert second["skipped"] == 167

    with connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM contents").fetchone()[0] == 167
        assert connection.execute("SELECT COUNT(*) FROM redirects").fetchone()[0] == 16
        assert connection.execute("SELECT COUNT(*) FROM contents WHERE status='draft'").fetchone()[0] == 167
        assert connection.execute("SELECT COUNT(*) FROM contents WHERE migration_review_required=1").fetchone()[0] == 167
        assert connection.execute("SELECT new_path FROM redirects WHERE old_path='/o-hrame/raspisanie-bogosluzheniy.html'").fetchone()[0] == "/schedule"


def test_reimport_never_overwrites_editor_review(tmp_path):
    database = tmp_path / "cms.sqlite3"
    source = ROOT / "current-sections.json"
    run_import(database, source)
    with connect(database) as connection:
        connection.execute(
            "UPDATE contents SET title='Проверено редактором', migration_review_required=0 WHERE legacy_url='/o-hrame/istoriya.html'"
        )
        connection.commit()

    result = run_import(database, source)

    assert result["updated"] == 0
    with connect(database) as connection:
        row = connection.execute("SELECT title,migration_review_required FROM contents WHERE legacy_url='/o-hrame/istoriya.html'").fetchone()
        assert row["title"] == "Проверено редактором"
        assert row["migration_review_required"] == 0
