from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from .config import ROOT


DOCUMENT_RE = re.compile(r"\.(?:pdf|docx?|xlsx?|pptx?|zip)(?:$|\?)", re.I)
PAGE_RE = re.compile(r"(?:/|\.html?|\.php)$", re.I)


def normalize_url(value: str, base_url: str, allowed_host: str) -> str | None:
    absolute = urldefrag(urljoin(base_url, value))[0]
    parts = urlsplit(absolute)
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or host != allowed_host:
        return None
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path.startswith("/manager/") or DOCUMENT_RE.search(path):
        return None
    if Path(path).suffix and not PAGE_RE.search(path):
        return None
    return urlunsplit(("https", allowed_host, path, "", ""))


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


async def extract_page(page, url: str) -> dict:
    response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(120)
    content = await page.evaluate(
        r"""() => {
          const clone = document.body.cloneNode(true);
          clone.querySelectorAll('script,style,noscript,nav,header,footer,aside,form').forEach(node => node.remove());
          const text = (clone.innerText || '').replace(/\n{3,}/g, '\n\n').trim();
          const links = [...document.querySelectorAll('a[href]')].map(a => ({ text: (a.textContent || '').trim(), href: a.href }));
          const images = [...document.querySelectorAll('img')].map(img => ({ src: img.currentSrc || img.src || '', alt: img.alt || '' })).filter(item => item.src);
          const documents = links.filter(item => /\.(pdf|docx?|xlsx?|pptx?|zip)(\?|$)/i.test(item.href));
          return {
            title: document.title || '',
            headings: [...document.querySelectorAll('h1,h2')].map(h => (h.textContent || '').trim()).filter(Boolean),
            text,
            links,
            images,
            documents,
            canonical: document.querySelector('link[rel="canonical"]')?.href || '',
          };
        }"""
    )
    return {
        "url": page.url,
        "requested_url": url,
        "status": response.status if response else 0,
        "title": content["title"],
        "headings": content["headings"],
        "text": content["text"],
        "images": content["images"],
        "documents": content["documents"],
        "canonical": content["canonical"],
        "outgoing": [item["href"] for item in content["links"]],
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


async def crawl(
    base_url: str,
    output: Path,
    *,
    max_pages: int = 0,
    delay_seconds: float = 0.25,
    resume: bool = True,
    headless: bool = True,
    browser_executable: Path | None = None,
    checkpoint_every: int = 25,
) -> dict:
    from playwright.async_api import async_playwright

    base_parts = urlsplit(base_url)
    allowed_host = (base_parts.hostname or "").lower()
    normalized_base = normalize_url(base_url, base_url, allowed_host)
    if not normalized_base:
        raise ValueError("Base URL must be an HTTP(S) page")

    if resume and output.exists():
        state = json.loads(output.read_text(encoding="utf-8"))
        if state.get("base_url") != normalized_base:
            raise ValueError("Existing checkpoint belongs to another base URL")
    else:
        state = {"schema_version": "1.0.0", "base_url": normalized_base, "queue": [normalized_base], "visited": [], "pages": [], "failures": []}

    visited = set(state.get("visited", []))
    queued = set(state.get("queue", []))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=headless,
            executable_path=str(browser_executable) if browser_executable else None,
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="sv-innokenty-migration-audit/1.0 (read-only; contact parish administrator)",
        )
        page = await context.new_page()
        while state["queue"] and (max_pages <= 0 or len(state["pages"]) < max_pages):
            url = state["queue"].pop(0)
            queued.discard(url)
            if url in visited:
                continue
            try:
                record = await extract_page(page, url)
                state["pages"].append(record)
                for raw_link in record["outgoing"]:
                    candidate = normalize_url(raw_link, record["url"], allowed_host)
                    if candidate and candidate not in visited and candidate not in queued:
                        state["queue"].append(candidate)
                        queued.add(candidate)
            except Exception as error:
                state["failures"].append({"url": url, "error": str(error), "captured_at": datetime.now(UTC).isoformat(timespec="seconds")})
            visited.add(url)
            state["visited"] = sorted(visited)
            state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            state["remaining_queue"] = len(state["queue"])
            if len(state["pages"]) % max(checkpoint_every, 1) == 0 or not state["queue"]:
                atomic_json(output, state)
                print(json.dumps({"pages": len(state["pages"]), "failures": len(state["failures"]), "remaining": len(state["queue"])}, ensure_ascii=False), flush=True)
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
        await context.close()
        await browser.close()
    atomic_json(output, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="Возобновляемый read-only Playwright-краулер старого сайта")
    parser.add_argument("--base-url", default="https://www.sv-innokenty.ru/")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "legacy-crawl-checkpoint.json")
    parser.add_argument("--max-pages", type=int, default=0, help="0 — без ограничения")
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--fresh", action="store_true", help="Не использовать существующий checkpoint")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--executable", type=Path, help="Путь к уже установленному Chromium")
    parser.add_argument("--checkpoint-every", type=int, default=25)
    args = parser.parse_args()
    state = asyncio.run(crawl(
        args.base_url,
        args.output,
        max_pages=args.max_pages,
        delay_seconds=args.delay,
        resume=not args.fresh,
        headless=not args.headed,
        browser_executable=args.executable,
        checkpoint_every=args.checkpoint_every,
    ))
    print(json.dumps({"pages": len(state["pages"]), "failures": len(state["failures"]), "remaining": len(state["queue"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
