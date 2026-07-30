from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _script(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_source_bundle_is_fresh_complete_and_immutable():
    script = _script("build_public_migration_bundle.sh")
    required_in_order = (
        "server.legacy_crawl",
        "--fresh",
        "server.legacy_inventory",
        "server.media_mirror",
        "--execute",
        "server.full_import",
        "server.media_report",
        "server.migration_bundle create",
        "server.migration_bundle verify",
        "server.migration_bundle verify-archive",
    )
    positions = [script.index(value) for value in required_in_order]
    assert positions == sorted(positions)
    assert "https://www.sv-innokenty.ru/" in script
    assert "git diff --quiet" in script


def test_clean_bootstrap_refuses_existing_runtime_and_gates_before_activation():
    script = _script("production_bootstrap_from_public_bundle.sh")
    assert "data/ already contains runtime files" in script
    assert "CMS container already exists" in script
    assert "Bundle code SHA" in script
    gates = (
        "server.full_import",
        "server.media_reconciliation",
        "server.migrations verify",
        "server.migration_acceptance scan",
        "server.migration_preparation preview",
        "server.migration_preparation apply",
        "server.migration_preparation verify",
        "server.migration_bundle verify-database",
        'rsync -a "$STAGE/data/" data/',
        'docker compose up -d "$SERVICE"',
    )
    positions = [script.index(value) for value in gates]
    assert positions == sorted(positions)
    assert "--skip-external" not in script


def test_clean_rollback_preserves_failed_runtime_instead_of_deleting_it():
    script = _script("production_rollback_clean_bootstrap.sh")
    assert "rm -rf" not in script
    assert "PRESERVED_FAILED_DATA" in script
    assert 'mv -- "$item" "$FAILED_ROOT/"' in script
