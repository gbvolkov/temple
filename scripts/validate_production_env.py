from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


REQUIRED_PATHS = {
    "CMS_DATABASE": "/data/cms.sqlite3",
    "CMS_MEDIA_DIR": "/data/media",
    "CMS_MEDIA_DERIVATIVES_DIR": "/data/media-derivatives",
    "CMS_LEGACY_CRAWL": "/data/legacy-crawl-checkpoint.json",
    "CMS_MEDIA_MANIFEST": "/data/legacy-media-manifest.json",
    "CMS_MISSING_MEDIA_REPORT": "/data/missing-legacy-media.csv",
}
SMTP_FIELDS = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "SMTP_SECURITY",
    "SUBMISSION_NOTIFY_TO",
)


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def validate(path: Path) -> dict:
    values = parse_env(path)
    errors: list[str] = []
    if os.name == "posix" and path.stat().st_mode & 0o077:
        errors.append("Production .env must not be readable or writable by group/others (use chmod 600)")
    if values.get("CMS_ENV") != "production":
        errors.append("CMS_ENV must be production")
    for name, expected in REQUIRED_PATHS.items():
        if values.get(name) != expected:
            errors.append(f"{name} must be {expected}")
    user = values.get("CMS_BOOTSTRAP_USER", "")
    password = values.get("CMS_BOOTSTRAP_PASSWORD", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", user) or "replace-with" in user.lower():
        errors.append("CMS_BOOTSTRAP_USER must be a non-placeholder 3-80 character login")
    if len(password) < 16 or "replace-with" in password.lower():
        errors.append("CMS_BOOTSTRAP_PASSWORD must be a non-placeholder value of at least 16 characters")
    public_url = values.get("PUBLIC_BASE_URL", "")
    parsed = urlsplit(public_url)
    try:
        parsed_port_valid = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        parsed_port_valid = False
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or not parsed_port_valid
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        errors.append("PUBLIC_BASE_URL must be an absolute HTTPS origin without path/query/fragment")
    elif (parsed.hostname or "").lower() in {"example.com", "example.org", "example.net"}:
        errors.append("PUBLIC_BASE_URL must not use a reserved example domain")
    secret = values.get("SUBMISSION_IP_HASH_SECRET", "")
    if len(secret) < 32 or "replace-with" in secret.lower():
        errors.append("SUBMISSION_IP_HASH_SECRET must be a non-placeholder value of at least 32 characters")
    networks = values.get("SUBMISSION_TRUSTED_PROXY_NETWORKS", "")
    try:
        parsed_networks = [
            str(ipaddress.ip_network(item.strip(), strict=False))
            for item in networks.split(",")
            if item.strip()
        ]
        if not parsed_networks:
            raise ValueError
    except ValueError:
        errors.append("SUBMISSION_TRUSTED_PROXY_NETWORKS must contain valid CIDR networks")
        parsed_networks = []

    smtp_configured = [bool(values.get(name)) for name in SMTP_FIELDS]
    if any(smtp_configured) and not all(smtp_configured):
        errors.append("SMTP settings must be either completely configured or completely empty")
    elif all(smtp_configured):
        placeholder_smtp = [
            name
            for name in SMTP_FIELDS
            if "replace-with" in values[name].lower()
            or "example.com" in values[name].lower()
            or "example.org" in values[name].lower()
            or "example.net" in values[name].lower()
        ]
        if placeholder_smtp:
            errors.append("SMTP settings contain placeholder values: " + ", ".join(placeholder_smtp))
        try:
            port = int(values["SMTP_PORT"])
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            errors.append("SMTP_PORT must be between 1 and 65535")
        if values["SMTP_SECURITY"].lower() not in {"starttls", "ssl"}:
            errors.append("SMTP_SECURITY must be starttls or ssl")
        if "@" not in values["SMTP_FROM"]:
            errors.append("SMTP_FROM must be an email address")
        recipients = [item.strip() for item in values["SUBMISSION_NOTIFY_TO"].split(",") if item.strip()]
        if not recipients or any("@" not in item for item in recipients):
            errors.append("SUBMISSION_NOTIFY_TO must contain email addresses")

    if errors:
        return {"ok": False, "errors": errors}
    return {
        "ok": True,
        "public_base_url": public_url.rstrip("/"),
        "bootstrap_user": user,
        "smtp_enabled": all(smtp_configured),
        "trusted_proxy_networks": parsed_networks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate clean production deployment environment without printing secrets")
    parser.add_argument("env_file", type=Path)
    args = parser.parse_args()
    try:
        result = validate(args.env_file)
    except OSError as error:
        result = {"ok": False, "errors": [str(error)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
