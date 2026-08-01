from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit


def normalized_route_template(route_template: list[dict]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in route_template:
        if not isinstance(item, dict) or not str(item.get("path", "")).strip():
            raise ValueError("Every structured route template item must contain path")
        result.append({"path": unquote(str(item["path"]))})
    return result


def build(crawl: dict, route_template: list[dict]) -> list[dict]:
    pages_by_path: dict[str, dict] = {}
    for page in crawl.get("pages", []):
        if not isinstance(page, dict):
            continue
        url = page.get("requested_url") or page.get("url") or ""
        path = unquote(urlsplit(str(url)).path) or "/"
        pages_by_path[path] = page

    result: list[dict] = []
    missing: list[str] = []
    for template in route_template:
        path = unquote(str(template.get("path", "")))
        page = pages_by_path.get(path)
        if page is None or not 200 <= int(page.get("status") or 0) < 400:
            missing.append(path)
            continue
        result.append({
            "path": path,
            "status": int(page["status"]),
            "title": page.get("title", ""),
            "headings": page.get("headings", []),
            "text": page.get("text", ""),
            "images": page.get("images", []),
            "documents": page.get("documents", []),
            "canonical": page.get("canonical", ""),
            "captured_at": page.get("captured_at", ""),
        })
    if missing:
        raise ValueError("Structured source routes are absent from the fresh crawl: " + ", ".join(missing))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild structured public sections from a fresh Playwright crawl")
    parser.add_argument("--crawl", type=Path, required=True)
    parser.add_argument("--route-template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundle-route-template", type=Path)
    args = parser.parse_args()
    crawl = json.loads(args.crawl.read_text(encoding="utf-8"))
    routes = json.loads(args.route_template.read_text(encoding="utf-8"))
    if not isinstance(crawl, dict) or not isinstance(routes, list):
        raise SystemExit("Crawl must be an object and route template must be a list")
    result = build(crawl, routes)
    normalized_routes = normalized_route_template(routes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.bundle_route_template:
        args.bundle_route_template.write_text(
            json.dumps(normalized_routes, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({"ok": True, "sections": len(result)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
