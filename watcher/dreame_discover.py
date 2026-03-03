"""
Dreame FM Discovery Script (Phase 1)
Loads a book page with Playwright, dumps __NEXT_DATA__ and logs XHR/fetch
requests so you can identify API endpoints for metadata and episode audio.

Usage:
    pip install playwright
    python -m playwright install chromium
    python dreame_discover.py "https://dreamefm.com/book/47-the-unloved-mate"
    # Then click "Listen" / play an episode in the opened browser if needed.
    # Output: dreame_next_data.json, dreame_network_log.json
"""

import json
import sys
from pathlib import Path

# Optional: allow running without playwright if only parsing is needed
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


OUTPUT_DIR = Path(__file__).parent
NEXT_DATA_FILE = OUTPUT_DIR / "dreame_next_data.json"
NETWORK_LOG_FILE = OUTPUT_DIR / "dreame_network_log.json"


def extract_next_data(html: str) -> dict | None:
    """Extract __NEXT_DATA__ JSON from HTML."""
    start_marker = '<script id="__NEXT_DATA__" type="application/json">'
    end_marker = "</script>"
    i = html.find(start_marker)
    if i == -1:
        return None
    i += len(start_marker)
    j = html.find(end_marker, i)
    if j == -1:
        return None
    raw = html[i:j].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def run_discovery(book_url: str, headless: bool = False) -> None:
    if not sync_playwright:
        print("Install playwright: pip install playwright && playwright install chromium")
        sys.exit(1)

    requests_log = []

    def handle_route(route):
        route.continue_()

    def handle_response(response):
        url = response.url
        try:
            if response.request.resource_type in ("xhr", "fetch", "media"):
                try:
                    body = response.text()
                except Exception:
                    body = None
                requests_log.append({
                    "url": url,
                    "status": response.status,
                    "resource_type": response.request.resource_type,
                    "method": response.request.method,
                    "body_preview": body[:2000] if body else None,
                    "body_length": len(body) if body else 0,
                })
        except Exception as e:
            requests_log.append({"url": url, "error": str(e)})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context.route("**/*", handle_route)
        page = context.new_page()
        page.on("response", handle_response)

        print(f"Loading: {book_url}")
        page.goto(book_url, wait_until="networkidle", timeout=60000)

        # Dump __NEXT_DATA__
        html = page.content()
        next_data = extract_next_data(html)
        if next_data:
            NEXT_DATA_FILE.write_text(json.dumps(next_data, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Saved: {NEXT_DATA_FILE}")
        else:
            print("No __NEXT_DATA__ found in page.")

        # Wait for user to interact (play audio) if not headless
        if not headless:
            print("Browser open. Click 'Listen' / play an episode to capture audio requests, then close the window.")
            page.wait_for_close(timeout=300_000)  # 5 min max
        else:
            page.wait_for_timeout(3000)

        browser.close()

    # Save network log
    NETWORK_LOG_FILE.write_text(json.dumps(requests_log, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(requests_log)} requests to: {NETWORK_LOG_FILE}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python dreame_discover.py <book_url> [--headless]")
        print("Example: python dreame_discover.py https://dreamefm.com/book/47-the-unloved-mate")
        sys.exit(1)
    url = sys.argv[1].strip()
    headless = "--headless" in sys.argv
    run_discovery(url, headless=headless)


if __name__ == "__main__":
    main()
