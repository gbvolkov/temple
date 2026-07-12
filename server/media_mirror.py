from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .config import ROOT


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip"}
MIME_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/svg+xml": ".svg",
    "application/pdf": ".pdf", "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}


def canonical_asset_url(value: str, allowed_hosts: set[str]) -> str | None:
    parts = urlsplit(value)
    path = parts.path
    if "/assets/" in path and not path.startswith("/assets/"):
        path = path[path.index("/assets/"):]
    extension = Path(unquote(path)).suffix.lower()
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or host not in allowed_hosts or extension not in ALLOWED_EXTENSIONS:
        return None
    return urlunsplit(("https", host, path, parts.query, ""))


def asset_url(value: str, allowed_hosts: set[str]) -> bool:
    return canonical_asset_url(value, allowed_hosts) is not None


def collect_assets(source: object, allowed_hosts: set[str] | None = None) -> list[dict]:
    allowed_hosts = allowed_hosts or {"www.sv-innokenty.ru", "sv-innokenty.ru"}
    pages = source.get("pages", []) if isinstance(source, dict) else source
    found: dict[str, dict] = {}
    for page in pages if isinstance(pages, list) else []:
        for image in page.get("images", []):
            url = image.get("src", "") if isinstance(image, dict) else str(image)
            canonical = canonical_asset_url(url, allowed_hosts)
            if canonical:
                found[canonical] = {"url": canonical, "kind": "image", "alt": image.get("alt", "") if isinstance(image, dict) else ""}
        for document in page.get("documents", []):
            url = document.get("href", "") if isinstance(document, dict) else str(document)
            canonical = canonical_asset_url(url, allowed_hosts)
            if canonical:
                found[canonical] = {"url": canonical, "kind": "document", "label": document.get("text", "") if isinstance(document, dict) else ""}
    if isinstance(source, dict) and isinstance(source.get("records"), list):
        def walk(value):
            if isinstance(value, str):
                canonical = canonical_asset_url(value, allowed_hosts)
                if canonical:
                    extension = Path(unquote(urlsplit(canonical).path)).suffix.lower()
                    yield canonical, "image" if extension in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"} else "document"
            elif isinstance(value, list):
                for item in value:
                    yield from walk(item)
            elif isinstance(value, dict):
                for item in value.values():
                    yield from walk(item)
        for record in source["records"]:
            for url, kind in walk(record.get("data", {})):
                found[url] = {"url": url, "kind": kind}
    return [found[url] for url in sorted(found)]


def choose_extension(url: str, content_type: str) -> str:
    extension = Path(unquote(urlsplit(url).path)).suffix.lower()
    if extension in ALLOWED_EXTENSIONS:
        return ".jpg" if extension == ".jpeg" else extension
    return MIME_EXTENSIONS.get(content_type.split(";", 1)[0].lower(), mimetypes.guess_extension(content_type) or ".bin")


def download_asset(asset: dict, destination: Path, max_bytes: int, timeout: int = 30) -> dict:
    request = Request(asset["url"], headers={"User-Agent": "sv-innokenty-migration-mirror/1.0 (read-only)"})
    temporary = destination / (hashlib.sha256(asset["url"].encode()).hexdigest() + ".part")
    hasher = hashlib.sha256()
    size = 0
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > max_bytes:
                raise ValueError(f"File exceeds limit: {declared} > {max_bytes}")
            with temporary.open("wb") as target:
                while chunk := response.read(1024 * 256):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError(f"File exceeds limit while downloading: {size} > {max_bytes}")
                    hasher.update(chunk)
                    target.write(chunk)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    digest = hasher.hexdigest()
    extension = choose_extension(asset["url"], content_type)
    relative = Path(digest[:2]) / f"{digest}{extension}"
    final = destination / relative
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        temporary.unlink(missing_ok=True)
    else:
        temporary.replace(final)
    return {
        **asset,
        "status": "mirrored",
        "content_type": content_type,
        "size_bytes": size,
        "sha256": digest,
        "stored_path": relative.as_posix(),
        "local_url": "/media/legacy/" + relative.as_posix(),
    }


def mirror(
    source: object,
    destination: Path,
    manifest_path: Path,
    *,
    execute: bool = False,
    max_bytes: int = 50 * 1024 * 1024,
    delay_seconds: float = 0.15,
    workers: int = 4,
) -> dict:
    assets = collect_assets(source)
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"schema_version": "1.0.0", "entries": {}, "errors": []}
    asset_urls = {asset["url"] for asset in assets}
    manifest["errors"] = [item for item in manifest.get("errors", []) if item.get("url") in asset_urls]
    if not execute:
        return {"planned": len(assets), "already_mirrored": sum(asset["url"] in manifest["entries"] for asset in assets), "execute": False}

    destination.mkdir(parents=True, exist_ok=True)
    pending = [asset for asset in assets if manifest["entries"].get(asset["url"], {}).get("status") != "mirrored"]

    def task(asset):
        try:
            result = download_asset(asset, destination, max_bytes)
            if delay_seconds:
                time.sleep(delay_seconds)
            return asset, result, None
        except Exception as error:
            return asset, None, str(error)

    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        for asset, result, error in executor.map(task, pending):
            manifest["errors"] = [item for item in manifest["errors"] if item.get("url") != asset["url"]]
            if result:
                manifest["entries"][asset["url"]] = result
            else:
                manifest["errors"].append({"url": asset["url"], "error": error})
            completed += 1
            if completed % 25 == 0:
                atomic_json(manifest_path, manifest)
                print(json.dumps({"completed": completed, "pending": len(pending) - completed, "mirrored": sum(entry.get("status") == "mirrored" for entry in manifest["entries"].values()), "errors": len(manifest["errors"])}, ensure_ascii=False), flush=True)
    atomic_json(manifest_path, manifest)
    return {
        "planned": len(assets),
        "mirrored": sum(entry.get("status") == "mirrored" for entry in manifest["entries"].values()),
        "errors": len(manifest["errors"]),
        "execute": True,
    }


def main() -> None:
    full_checkpoint = ROOT / "data" / "legacy-crawl-checkpoint.json"
    parser = argparse.ArgumentParser(description="Read-only зеркалирование медиа старого сайта с checksum и resume")
    parser.add_argument("--source", type=Path, default=full_checkpoint if full_checkpoint.exists() else ROOT / "current-sections.json")
    parser.add_argument("--destination", type=Path, default=ROOT / "data" / "media" / "legacy")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data" / "legacy-media-manifest.json")
    parser.add_argument("--execute", action="store_true", help="Без флага выполняется только dry-run")
    parser.add_argument("--max-mb", type=int, default=50)
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    print(json.dumps(mirror(source, args.destination, args.manifest, execute=args.execute, max_bytes=args.max_mb * 1024 * 1024, delay_seconds=args.delay, workers=args.workers), ensure_ascii=False))


if __name__ == "__main__":
    main()
