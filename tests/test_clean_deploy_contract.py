from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_source_bundle_is_fresh_complete_and_immutable():
    script = (ROOT / "scripts" / "build_public_source_bundle.ps1").read_text(encoding="utf-8")
    assert "server.legacy_crawl" in script
    assert "--fresh" in script
    assert "--seed-inventory" in script and "known-public-routes.json" in script
    assert "build_structured_source.py" in script
    assert "--bundle-route-template" in script
    assert 'Copy-Item -LiteralPath "current-sections.json"' not in script
    assert "server.importer" in script and "--dry-run" in script
    assert "server.media_mirror" in script and "--execute" in script
    assert "server.full_import" in script
    assert "server.media_report" in script
    assert "validate_source_bundle.py create" in script
    assert "validate_source_bundle.py verify" in script
    assert "Get-FileHash -Algorithm SHA256" in script
    assert "git status --porcelain --untracked-files=normal" in script
    assert "git branch --show-current" in script
    assert "Local main must exactly match origin/main" in script
    assert "https://www.sv-innokenty.ru/" in script
    assert "At least 15 GiB of free space" in script
    crawler = (ROOT / "server" / "legacy_crawl.py").read_text(encoding="utf-8")
    assert 'service_workers="block"' in crawler
    assert 'context.route("**/*", enforce_read_only)' in crawler


def test_ubuntu_bootstrap_refuses_existing_data_and_activates_only_after_gates():
    script = (ROOT / "scripts" / "production_bootstrap_clean.sh").read_text(encoding="utf-8")
    empty_guard = script.index('find data -mindepth 1 ! -name .gitkeep')
    import_step = script.index("server.full_import")
    structured_import = script.index("server.importer")
    reconciliation = script.index("server.media_reconciliation")
    preparation = script.index("server.migration_preparation preview")
    semantic_verify = script.index("server.migration_preparation verify")
    activation = script.index('mv "$STAGE_DATA" "$PROJECT_DIR/data"')
    service_start = script.index('docker compose up -d "$SERVICE"')
    assert empty_guard < structured_import < import_step < reconciliation < preparation < semantic_verify < activation < service_start
    assert "sha256sum -c" in script
    assert 'docker compose ps --all -q "$SERVICE"' in script
    assert "must be on the same filesystem for atomic activation" in script
    assert "Checksum file must contain exactly one SHA-256 record" in script
    assert "Unsafe archive member" in script
    assert "source-bundle-verification.json" in script
    assert "Bundle was created from" in script
    assert "Activation rolled back" in script
    assert 'ORIGINAL_MOVED=1' in script
    assert 'docker compose rm -f -s "$SERVICE"' in script
    assert "data/ is not empty" in script


def test_post_deploy_verification_rechecks_every_persistent_gate():
    script = (ROOT / "scripts" / "production_verify_clean.sh").read_text(encoding="utf-8")
    for expected in (
        "validate_production_env.py",
        "validate_source_bundle.py verify",
        "validate_source_bundle.py verify-database",
        "--post-activation",
        "PRAGMA quick_check",
        "PRAGMA foreign_key_check",
        "server.migrations verify",
        "server.search verify",
        "server.migration_preparation verify",
        "/api/public/content?limit=1",
    ):
        assert expected in script
