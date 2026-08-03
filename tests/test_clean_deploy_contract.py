from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_source_bundle_is_fresh_complete_and_immutable():
    script = (ROOT / "scripts" / "build_public_source_bundle.ps1").read_text(encoding="utf-8")
    assert "[Parameter(Mandatory = $true)]" in script
    assert "PreprodRoot must be an absolute Windows path" in script
    assert 'Join-Path $PreprodRoot "release-checkout"' in script
    assert "never from the development checkout" in script
    assert 'Join-Path $PreprodRoot "source-bundles"' in script
    assert 'Join-Path $PreprodRoot "runtime"' in script
    assert "UV_CACHE_DIR" in script
    assert "UV_PROJECT_ENVIRONMENT" in script
    assert "PLAYWRIGHT_BROWSERS_PATH" in script
    assert 'Join-Path $ProjectDir "output\\source-bundles"' not in script
    assert "server.legacy_crawl" in script
    assert "--fresh" in script
    assert "--seed-inventory" in script and "known-public-routes.json" in script
    assert "scripts.build_structured_source" in script
    assert "--bundle-route-template" in script
    assert 'Copy-Item -LiteralPath "current-sections.json"' not in script
    assert "server.importer" in script and "--dry-run" in script
    assert "server.media_mirror" in script and "--execute" in script
    assert "server.full_import" in script
    assert "server.media_report" in script
    assert "scripts.validate_source_bundle create" in script
    assert "scripts.validate_source_bundle verify" in script
    assert "Get-FileHash -Algorithm SHA256" in script
    assert "git status --porcelain --untracked-files=normal" in script
    assert "git branch --show-current" in script
    assert "Local main must exactly match origin/main" in script
    assert "https://www.sv-innokenty.ru/" in script
    assert "At least 15 GiB of free space" in script
    crawler = (ROOT / "server" / "legacy_crawl.py").read_text(encoding="utf-8")
    assert 'service_workers="block"' in crawler
    assert 'context.route("**/*", enforce_read_only)' in crawler


def test_windows_preprod_is_documented_outside_the_development_checkout():
    guide = (ROOT / "outputs" / "cms-production-clean-migration-guide.md").read_text(encoding="utf-8")
    assert "C:\\CMS-Preprod\\" in guide
    assert "release-checkout" in guide
    assert "runtime\\" in guide
    assert "source-bundles\\" in guide
    assert "-PreprodRoot $PreprodRoot" in guide
    assert "cd C:\\Projects" not in guide
    assert "Set-Location C:\\Projects\\temple" not in guide
    assert "output/source-bundles" not in guide
    assert "output\\source-bundles" not in guide


def test_source_bundle_validator_is_invoked_as_a_module():
    windows = (ROOT / "scripts" / "build_public_source_bundle.ps1").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts" / "production_bootstrap_clean.sh").read_text(encoding="utf-8")
    verify = (ROOT / "scripts" / "production_verify_clean.sh").read_text(encoding="utf-8")
    assert "uv run python -m scripts.validate_source_bundle create" in windows
    assert "uv run python -m scripts.validate_source_bundle verify" in windows
    assert "python3 -m scripts.validate_source_bundle verify" in bootstrap
    assert "python3 -m scripts.validate_source_bundle verify-database" in bootstrap
    assert "python3 -m scripts.validate_source_bundle verify" in verify
    assert "python3 -m scripts.validate_source_bundle verify-database" in verify
    assert "python scripts/validate_source_bundle.py" not in windows
    assert "python3 scripts/validate_source_bundle.py" not in bootstrap
    assert "python3 scripts/validate_source_bundle.py" not in verify


def test_windows_source_bundle_can_resume_only_an_unsealed_bundle():
    script = (ROOT / "scripts" / "build_public_source_bundle.ps1").read_text(encoding="utf-8")
    assert "[switch]$Resume" in script
    assert "Cannot resume a sealed source bundle" in script
    assert "Resume requested but the bundle directory does not exist" in script
    assert 'if ($Resume)' in script
    assert '"Resume read-only Playwright crawl"' in script
    assert "--retries $MediaRetries" in script
    assert 'Join-Path $BundleDir "source-bundle-manifest.json"' in script


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
        "scripts.validate_source_bundle verify",
        "scripts.validate_source_bundle verify-database",
        "--post-activation",
        "PRAGMA quick_check",
        "PRAGMA foreign_key_check",
        "server.migrations verify",
        "server.search verify",
        "server.migration_preparation verify",
        "/api/public/content?limit=1",
    ):
        assert expected in script
