from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


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
    media_derivatives_dir: Path | None = None
    session_hours: int = 12
    public_base_url: str = "https://temple.gbvolkoff.name:8443"
    max_image_bytes: int = 15 * 1024 * 1024
    max_video_bytes: int = 200 * 1024 * 1024
    max_document_bytes: int = 50 * 1024 * 1024

    @property
    def derivatives_dir(self) -> Path:
        return self.media_derivatives_dir or self.media_dir.parent / "media-derivatives"

    @staticmethod
    def normalize_public_base_url(value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("PUBLIC_BASE_URL должен быть абсолютным http/https URL без пути")
        return normalized

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
            media_derivatives_dir=Path(
                os.getenv("CMS_MEDIA_DERIVATIVES_DIR", ROOT / "data" / "media-derivatives")
            ),
            schema_path=ROOT / "site" / "cms-schema.json",
            legacy_sections_path=ROOT / "current-sections.json",
            legacy_crawl_path=Path(os.getenv("CMS_LEGACY_CRAWL", ROOT / "data" / "legacy-crawl-checkpoint.json")),
            media_manifest_path=Path(os.getenv("CMS_MEDIA_MANIFEST", ROOT / "data" / "legacy-media-manifest.json")),
            environment=environment,
            bootstrap_user=os.getenv("CMS_BOOTSTRAP_USER", "admin"),
            bootstrap_password=password,
            session_hours=int(os.getenv("CMS_SESSION_HOURS", "12")),
            public_base_url=cls.normalize_public_base_url(
                os.getenv("PUBLIC_BASE_URL", "https://temple.gbvolkoff.name:8443")
            ),
            max_image_bytes=int(os.getenv("CMS_MAX_IMAGE_BYTES", str(15 * 1024 * 1024))),
            max_video_bytes=int(os.getenv("CMS_MAX_VIDEO_BYTES", str(200 * 1024 * 1024))),
            max_document_bytes=int(os.getenv("CMS_MAX_DOCUMENT_BYTES", str(50 * 1024 * 1024))),
        )
