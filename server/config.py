from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    root: Path
    site_dir: Path
    database_path: Path
    media_dir: Path
    schema_path: Path
    legacy_sections_path: Path
    legacy_crawl_path: Path | None
    media_manifest_path: Path
    environment: str
    bootstrap_user: str
    bootstrap_password: str | None
    session_hours: int = 12

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("CMS_ENV", "development")
        password = os.getenv("CMS_BOOTSTRAP_PASSWORD")
        if environment == "development" and password is None:
            password = "temple-demo"
        return cls(
            root=ROOT,
            site_dir=ROOT / "site",
            database_path=Path(os.getenv("CMS_DATABASE", ROOT / "data" / "cms.sqlite3")),
            media_dir=Path(os.getenv("CMS_MEDIA_DIR", ROOT / "data" / "media")),
            schema_path=ROOT / "site" / "cms-schema.json",
            legacy_sections_path=ROOT / "current-sections.json",
            legacy_crawl_path=Path(os.getenv("CMS_LEGACY_CRAWL", ROOT / "data" / "legacy-crawl-checkpoint.json")),
            media_manifest_path=Path(os.getenv("CMS_MEDIA_MANIFEST", ROOT / "data" / "legacy-media-manifest.json")),
            environment=environment,
            bootstrap_user=os.getenv("CMS_BOOTSTRAP_USER", "admin"),
            bootstrap_password=password,
            session_hours=int(os.getenv("CMS_SESSION_HOURS", "12")),
        )
