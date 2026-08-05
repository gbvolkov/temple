from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def script(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_stage13_wrapper_accepts_only_schema_11_or_12_and_targets_13() -> None:
    wrapper = script("production_apply_stage13.sh")
    assert "export DEPLOYMENT_STAGE=13" in wrapper
    assert '11|12)' in wrapper
    assert "export EXPECTED_SCHEMA=13" in wrapper
    assert "production_apply_stage4.sh" in wrapper


def test_stage14_wrapper_accepts_schema_13_and_targets_14() -> None:
    wrapper = script("production_apply_stage14.sh")
    rollout = script("production_apply_stage4.sh")
    assert "export DEPLOYMENT_STAGE=14" in wrapper
    assert '13)' in wrapper
    assert "export EXPECTED_SCHEMA=14" in wrapper
    assert "export EXPECTED_CONTENT_SCHEMA=1.7.0" in wrapper
    assert "production_apply_stage4.sh" in wrapper
    assert "/schedule /treby /about" in rollout
    assert "production treby legacy status" in rollout
    assert "external treby legacy status" in rollout


def test_cms_30_rollout_supports_direct_docker_without_passwordless_sudo() -> None:
    baseline = script("production_baseline.sh")
    rollout = script("production_apply_stage4.sh")
    for deployment_script in (baseline, rollout):
        assert "select_docker_command" in deployment_script
        assert "DOCKER=(docker)" in deployment_script
        assert "DOCKER=(sudo -n docker)" in deployment_script
        assert "sudo docker" not in deployment_script
    assert "sudo rm -rf" not in rollout
    assert '\"${DOCKER[@]}\" run --rm' in rollout


def test_stage13_restore_and_semantic_gate_precede_production_workflow() -> None:
    rollout = script("production_apply_stage4.sh")
    restore_gate = rollout.index(
        'raise SystemExit(0 if data.get("ok") is True else 1)'
    )
    disposable_apply = rollout.index(
        'python -m server.migration_preparation apply',
        restore_gate,
    )
    disposable_post_audit = rollout.index(
        'python -m server.migration_acceptance scan',
        disposable_apply,
    )
    disposable_verify = rollout.index(
        'python -m server.migration_preparation verify',
        disposable_post_audit,
    )
    production_apply = rollout.rindex(
        'python -m server.migration_preparation apply'
    )
    production_post_audit = rollout.rindex(
        'python -m server.migration_acceptance scan'
    )
    production_verify = rollout.rindex(
        'python -m server.migration_preparation verify'
    )
    assert restore_gate < disposable_apply < disposable_post_audit < disposable_verify
    assert disposable_verify < production_apply < production_post_audit < production_verify
    assert "set -Eeuo pipefail" in rollout


def test_stage13_validates_provenance_and_preserves_business_invariants() -> None:
    rollout = script("production_apply_stage4.sh")
    assert "migration_generated_media_events" in rollout
    assert "for column in identity_hash recovered recovery_reason error_json" in rollout
    assert "stage 13 preparation column $column" in rollout
    assert "review flags" in rollout
    assert "workflow states" in rollout
    assert "PRAGMA foreign_key_check" in rollout
    assert "PRAGMA quick_check" in rollout
