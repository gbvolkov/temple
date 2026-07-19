from __future__ import annotations

import ipaddress
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
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_security: str = "starttls"
    submission_notify_to: tuple[str, ...] = ()
    submission_ip_hash_secret: str | None = None
    submission_trusted_proxy_networks: tuple[str, ...] = ("127.0.0.0/8", "::1/128")
    submission_worker_interval_seconds: int = 60

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

    @staticmethod
    def comma_separated(value: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))

    @classmethod
    def proxy_networks(cls, value: str) -> tuple[str, ...]:
        networks = cls.comma_separated(value)
        for network in networks:
            ipaddress.ip_network(network, strict=False)
        return networks

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("CMS_ENV", "development")
        password = os.getenv("CMS_BOOTSTRAP_PASSWORD")
        if environment == "development" and password is None:
            password = "temple-demo"
        smtp_security = os.getenv("SMTP_SECURITY", "").strip().lower() or "starttls"
        if smtp_security not in {"starttls", "ssl"}:
            raise ValueError("SMTP_SECURITY должен иметь значение starttls или ssl")
        smtp_port_value = os.getenv("SMTP_PORT", "").strip()
        smtp_port = int(smtp_port_value) if smtp_port_value else 587
        submission_secret = os.getenv("SUBMISSION_IP_HASH_SECRET") or None
        if submission_secret and len(submission_secret) < 32:
            raise ValueError("SUBMISSION_IP_HASH_SECRET должен содержать не менее 32 символов")
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
            smtp_host=os.getenv("SMTP_HOST") or None,
            smtp_port=smtp_port,
            smtp_user=os.getenv("SMTP_USER") or None,
            smtp_password=os.getenv("SMTP_PASSWORD") or None,
            smtp_from=os.getenv("SMTP_FROM") or None,
            smtp_security=smtp_security,
            submission_notify_to=cls.comma_separated(os.getenv("SUBMISSION_NOTIFY_TO", "")),
            submission_ip_hash_secret=submission_secret,
            submission_trusted_proxy_networks=cls.proxy_networks(
                os.getenv("SUBMISSION_TRUSTED_PROXY_NETWORKS", "127.0.0.0/8,::1/128")
            ),
            submission_worker_interval_seconds=max(
                1, int(os.getenv("SUBMISSION_WORKER_INTERVAL_SECONDS", "60"))
            ),
        )
