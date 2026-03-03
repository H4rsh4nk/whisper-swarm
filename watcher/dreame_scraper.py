"""
Dreame FM Scraper for Whisper-Swarm (Phase 2)
Downloads audiobook metadata and episode audio from Dreame FM, merges into one file,
and saves to WATCH_FOLDER (or uploads to master).

Automatic mode (default): Uses Playwright to load the page, extract metadata,
trigger play to capture audio URLs, then download, merge, and upload to master.
Default URL is hardcoded; run with no args for one-click scrape + upload.

Usage:
    python dreame_scraper.py
        # Automatic: hardcoded URL, discover + download + upload to master
    python dreame_scraper.py "https://dreamefm.com/book/47-the-unloved-mate" --upload
    python dreame_scraper.py "https://..." --no-upload   # save to WATCH_FOLDER only

Env (in watcher/.env):
    WATCH_FOLDER, MASTER_URL, ADMIN_USERNAME, ADMIN_PASSWORD (for upload)
    DREAME_EPISODE_AUDIO_API (optional) if auto-discovery fails
"""

# Default book URL (hardcoded for automatic run)
DEFAULT_BOOK_URL = "https://dreamefm.com/book/47-the-unloved-mate"

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from dotenv import load_dotenv

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

load_dotenv(Path(__file__).parent / ".env", override=True)

# Config
WATCH_FOLDER = Path(os.environ.get("WATCH_FOLDER", r"C:\Audiobooks"))
MASTER_URL = (os.environ.get("MASTER_URL", "http://localhost:8000") or "").rstrip("/")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
DREAME_EPISODE_AUDIO_API = os.environ.get("DREAME_EPISODE_AUDIO_API", "")  # optional template with {episode_id}
DREAME_DOWNLOAD_DELAY = float(os.environ.get("DREAME_DOWNLOAD_DELAY", "1.0"))  # seconds between episode downloads

# Base directory for Dreame downloads: book folders go here (e.g. downloads/dreame_47-the-unloved-mate/episodes/)
_DEFAULT_DOWNLOAD_BASE = Path(__file__).parent.parent / "downloads"
DREAME_DOWNLOAD_DIR = Path(os.environ.get("DREAME_DOWNLOAD_DIR", str(_DEFAULT_DOWNLOAD_BASE)))

# Dreame FM chapter list API (official)
DREAME_CHAPTER_API = "https://dreamefmapi.system.stary.ltd/bookShelf/getOfficialBookChapterList"

DISCOVER_NEXT_DATA_FILE = Path(__file__).parent / "dreame_next_data.json"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


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


def parse_book_id_from_url(url: str) -> str | None:
    """Return book id or slug from URL, e.g. 47-the-unloved-mate or 47."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    m = re.search(r"/book/([^/]+)", path)
    return m.group(1) if m else None


def normalize_metadata(next_data: dict) -> dict | None:
    """
    Extract book title, author, cover URL, and episodes from __NEXT_DATA__.
    Tries props.pageProps.bookDetail (Dreame FM), then book/audiobook/data, then pageProps.
    """
    if not next_data:
        return None
    props = next_data.get("props", {})
    page_props = props.get("pageProps", {})

    # Dreame FM: book detail is in bookDetail (no episodes in page; chapters from API)
    book = page_props.get("bookDetail") or page_props.get("book") or page_props.get("audiobook") or page_props.get("data")
    if not book or not isinstance(book, dict):
        book = page_props
    if not isinstance(book, dict):
        return None

    title = (
        book.get("title")
        or book.get("name")
        or book.get("bookTitle")
        or ""
    )
    author = (
        book.get("author")
        or book.get("authorName")
        or (book.get("authorInfo", {}) or {}).get("name")
        or ""
    )
    if isinstance(author, dict):
        author = author.get("name") or author.get("title") or ""
    cover_url = (
        book.get("cover")
        or book.get("coverUrl")
        or book.get("coverImage")
        or (book.get("coverInfo", {}) or {}).get("url")
    )
    if isinstance(cover_url, dict):
        cover_url = cover_url.get("url") or cover_url.get("src")
    if cover_url and not cover_url.startswith("http"):
        cover_url = urljoin("https://dreamefm.com/", cover_url)

    # Narrator, length, episode count, status (for book_info.json)
    narrated_by = book.get("reciterName") or book.get("narrator") or ""
    if isinstance(narrated_by, dict):
        narrated_by = narrated_by.get("name") or ""
    length_str = book.get("duration") or book.get("length") or ""
    if isinstance(length_str, (int, float)) and length_str > 0:
        h = int(length_str // 3600)
        m = int((length_str % 3600) // 60)
        length_str = f"{h}hrs {m:02d}mins"
    episode_count = book.get("chapterNum") or book.get("episodeCount") or 0
    listener_count = book.get("playCount") or 0
    status_raw = book.get("status") or book.get("upshelfStatus") or ""
    if status_raw == 2 or status_raw == "2" or (isinstance(status_raw, str) and "UPSHELF" in status_raw.upper()):
        status = "Completed"
    elif status_raw:
        status = str(status_raw)
    else:
        status = "Completed"

    # Episodes: from page or from API later (Dreame puts chapters in getOfficialBookChapterList)
    raw_episodes = (
        book.get("episodes")
        or book.get("chapters")
        or book.get("bookChapterResource")
        or book.get("tracks")
        or book.get("audios")
        or []
    )
    if not isinstance(raw_episodes, list):
        raw_episodes = []

    episodes = []
    for i, ep in enumerate(raw_episodes):
        if not isinstance(ep, dict):
            continue
        ep_id = ep.get("id") or ep.get("episodeId") or ep.get("chapterId") or str(i + 1)
        ep_title = ep.get("title") or ep.get("name") or ep.get("chapterTitle") or f"Episode {i + 1}"
        duration = ep.get("duration") or ep.get("durationSeconds") or ep.get("length") or 0
        if isinstance(duration, str) and ":" in duration:
            parts = duration.split(":")
            try:
                duration = sum(int(p) * (60 ** (len(parts) - 1 - j)) for j, p in enumerate(parts))
            except ValueError:
                duration = 0
        audio_url = (
            ep.get("audioUrl")
            or ep.get("playUrl")
            or ep.get("source")
            or ep.get("url")
            or ep.get("audio")
            or (ep.get("audioInfo", {}) or {}).get("url")
        )
        if isinstance(audio_url, dict):
            audio_url = audio_url.get("url") or audio_url.get("src")
        if audio_url and not audio_url.startswith("http"):
            audio_url = urljoin("https://dreamefm.com/", audio_url)
        episodes.append({
            "id": str(ep_id),
            "title": str(ep_title),
            "duration_seconds": int(duration) if duration else 0,
            "audio_url": audio_url or None,
        })

    return {
        "title": title.strip() or "Unknown",
        "author": author.strip() or "Unknown",
        "cover_url": cover_url,
        "book_id": book.get("id"),  # for chapter list API
        "narrated_by": (narrated_by or "").strip(),
        "length": (length_str or "").strip(),
        "episode_count": int(episode_count) if episode_count else 0,
        "listener_count": int(listener_count) if listener_count else 0,
        "status": status,
        "episodes": episodes,
    }


def fetch_chapter_list(book_id) -> list:
    """Fetch chapter list from Dreame FM API. Returns list of {id, title, duration_seconds, audio_url}."""
    if not book_id:
        return []
    try:
        with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            r = client.post(DREAME_CHAPTER_API, json={"bookResourceId": int(book_id)})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        print(f"[WARN] Chapter list API: {e}")
        return []
    chapters = data.get("bookChapterResource") or []
    out = []
    for i, ch in enumerate(chapters):
        if not isinstance(ch, dict):
            continue
        ch_id = ch.get("id") or (i + 1)
        title = ch.get("name") or ch.get("title") or f"Chapter {i + 1}"
        dur = ch.get("time") or 0
        out.append({
            "id": str(ch_id),
            "title": str(title),
            "duration_seconds": int(dur) if dur else 0,
            "audio_url": None,
        })
    return out


def fetch_metadata(book_url: str, use_saved_next_data: bool = True) -> dict | None:
    """Get normalized metadata from saved dreame_next_data.json or by fetching the page."""
    if use_saved_next_data and DISCOVER_NEXT_DATA_FILE.exists():
        try:
            next_data = json.loads(DISCOVER_NEXT_DATA_FILE.read_text(encoding="utf-8"))
            return normalize_metadata(next_data)
        except Exception as e:
            print(f"[WARN] Could not load {DISCOVER_NEXT_DATA_FILE}: {e}")
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        resp = client.get(book_url)
        resp.raise_for_status()
        next_data = extract_next_data(resp.text)
        return normalize_metadata(next_data) if next_data else None


def _extract_url_from_json(body: str) -> str | None:
    """Extract first audio-like URL from JSON string."""
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            for key in ("url", "audioUrl", "playUrl", "source", "data"):
                val = data.get(key)
                if isinstance(val, str) and (val.startswith("http") or ".mp3" in val or ".m4a" in val):
                    return val
                if isinstance(val, dict) and isinstance(val.get("url"), str):
                    return val["url"]
        if isinstance(data, list) and data and isinstance(data[0], dict):
            for key in ("url", "audioUrl", "playUrl"):
                if data[0].get(key):
                    return data[0][key]
    except Exception:
        pass
    return None


def discover_with_playwright(book_url: str):
    """
    Use Playwright to load the book page, extract metadata + full chapter list,
    then iterate through \"Chapter X\" buttons to trigger playback for each chapter
    and capture the real media URL. Returns (meta, list of audio URLs per episode).
    """
    if not sync_playwright:
        print("[WARN] Playwright not installed. pip install playwright && python -m playwright install chromium")
        return None, []

    captured_media_urls = []
    media_by_id: dict[str, str] = {}
    chapter_list_body = [None]  # mutable so inner function can set

    def on_response(response):
        try:
            url = response.url
            rtype = response.request.resource_type
            if rtype == "media":
                captured_media_urls.append(url)
                # Try to extract chapter id from media URL (e.g. /high/1020/)
                m = re.search(r"/high/(\\d+)/", url)
                if m:
                    chapter_id = m.group(1)
                    media_by_id[chapter_id] = url
                return
            if rtype in ("xhr", "fetch"):
                try:
                    body = response.text()
                except Exception:
                    return
                if not body or len(body) > 100_000:
                    return
                if "getOfficialBookChapterList" in url:
                    chapter_list_body[0] = body
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.on("response", on_response)

        page.goto(book_url, wait_until="networkidle", timeout=60000)
        # Wait a bit to allow chapter-list XHR to complete
        page.wait_for_timeout(3000)

        html = page.content()
        next_data = extract_next_data(html)
        meta = normalize_metadata(next_data) if next_data else None
        if not meta:
            browser.close()
            return None, []

        # Parse chapter list from captured API response
        episodes = meta.get("episodes") or []
        if chapter_list_body[0]:
            try:
                data = json.loads(chapter_list_body[0])
                chapters = data.get("bookChapterResource") or []
                ep_list = []
                for i, ch in enumerate(chapters):
                    if not isinstance(ch, dict):
                        continue
                    ch_id = ch.get("id") or (i + 1)
                    title = ch.get("name") or ch.get("title") or f"Chapter {i + 1}"
                    dur = ch.get("time") or 0
                    ep_list.append({
                        "id": str(ch_id),
                        "title": str(title),
                        "duration_seconds": int(dur) if dur else 0,
                        "audio_url": None,
                    })
                if ep_list:
                    episodes = ep_list
                    meta["episodes"] = ep_list
            except Exception as e:
                print(f"[WARN] Parse chapter list: {e}")

        # Helper: click a specific chapter button by its title text
        def click_chapter(title: str) -> bool:
            candidates = [
                f'text=\"{title}\"',
                f'button:has-text(\"{title}\")',
                f'a:has-text(\"{title}\")',
            ]
            for sel in candidates:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        loc.click(timeout=2000)
                        return True
                except Exception:
                    continue
            return False

        # Iterate chapters and trigger playback to capture media URLs
        for ep in episodes:
            title = ep.get("title") or ""
            if not title:
                continue
            # Dreame uses \"Chapter 01\" style names; rely on exact text
            if not click_chapter(title):
                continue
            # Allow time for the media request to fire
            page.wait_for_timeout(1200)

        # One more small wait for any trailing requests
        page.wait_for_timeout(1500)
        browser.close()

    # Build list of audio URLs aligned with episodes using captured media_by_id
    episode_urls: list[str | None] = []
    episodes = meta.get("episodes") or []
    if media_by_id:
        for ep in episodes:
            ep_id = str(ep.get("id"))
            episode_urls.append(media_by_id.get(ep_id))
    elif captured_media_urls:
        # Fallback: order-only mapping if we couldn't parse chapter ids
        for i in range(len(episodes)):
            episode_urls.append(captured_media_urls[i] if i < len(captured_media_urls) else None)

    return meta, episode_urls


def get_episode_audio_url(episode: dict, client: httpx.Client, api_template: str) -> str | None:
    """Resolve episode audio URL from episode dict or optional API template."""
    if episode.get("audio_url"):
        return episode["audio_url"]
    if not api_template or ("{episode_id}" not in api_template and "{id}" not in api_template):
        return None
    url = api_template.replace("{episode_id}", episode["id"]).replace("{id}", episode["id"])
    try:
        r = client.get(url)
        r.raise_for_status()
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else None
        if data:
            return data.get("url") or data.get("audioUrl") or data.get("playUrl") or (data.get("data") or {}).get("url")
    except Exception as e:
        print(f"[WARN] Episode API {url}: {e}")
    return None


def safe_filename(s: str) -> str:
    """Remove characters unsafe for filenames."""
    return re.sub(r'[<>:"/\\|?*]', "", s).strip() or "unknown"


def download_file(client: httpx.Client, url: str, path: Path, retries: int = 2) -> bool:
    """Stream download to path. Returns True on success."""
    for attempt in range(retries + 1):
        try:
            with client.stream("GET", url) as r:
                r.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=65536):
                        f.write(chunk)
            return True
        except Exception as e:
            print(f"[WARN] Download attempt {attempt + 1}: {e}")
            if attempt < retries:
                time.sleep(2)
    return False


def merge_audio_files(paths: list, out_path: Path) -> bool:
    """Concatenate audio files with ffmpeg. Returns True on success."""
    if not paths:
        return False
    if len(paths) == 1:
        import shutil
        shutil.copy2(paths[0], out_path)
        return True
    list_file = out_path.parent / "_concat_list.txt"
    lines = [f"file '{Path(p).resolve().as_posix()}'" for p in paths]
    list_file.write_text("\n".join(lines), encoding="utf-8")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c", "copy", str(out_path)
            ],
            check=True,
            capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"[ERROR] ffmpeg merge failed: {e}")
        return False
    finally:
        if list_file.exists():
            list_file.unlink(missing_ok=True)


def run_scraper(
    book_url: str,
    output_dir: Path | None = None,
    upload_to_master: bool = False,
    download_cover: bool = True,
    use_saved_next_data: bool = True,
    auto_discover: bool = True,
    merge_to_single: bool = False,
) -> bool:
    output_dir = output_dir or DREAME_DOWNLOAD_DIR
    book_id = parse_book_id_from_url(book_url)
    if not book_id:
        print("[ERROR] Could not parse book ID from URL")
        return False

    # Prefer fresh fetch when auto-discovering so we get latest page
    use_saved = use_saved_next_data and not auto_discover
    meta = fetch_metadata(book_url, use_saved_next_data=use_saved)
    if not meta:
        # Try Playwright to get metadata + audio URLs in one go
        if auto_discover and sync_playwright:
            print("[INFO] Fetching page with Playwright to get metadata and audio URLs...")
            meta, discovered_urls = discover_with_playwright(book_url)
            if meta and discovered_urls:
                for i, ep in enumerate(meta.get("episodes") or []):
                    if i < len(discovered_urls) and discovered_urls[i]:
                        ep["resolved_audio_url"] = discovered_urls[i]
        if not meta:
            print("[ERROR] Could not get book metadata. Check URL and network.")
            return False
    else:
        # Resolve audio URL for each episode: from meta first, then env template, then Playwright
        api_template = DREAME_EPISODE_AUDIO_API
        with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            for ep in meta.get("episodes") or []:
                url = get_episode_audio_url(ep, client, api_template)
                if url:
                    ep["resolved_audio_url"] = url

        need_discover = any(not ep.get("resolved_audio_url") for ep in (meta.get("episodes") or []))
        # If we have no episodes at all (e.g. chapter API needs session), run Playwright to get chapter list + audio
        if not meta.get("episodes") and meta.get("book_id") and auto_discover and sync_playwright:
            print("[INFO] No chapters in metadata; loading page with Playwright to get chapter list and audio...")
            meta, discovered_urls = discover_with_playwright(book_url)
            if meta and discovered_urls:
                for i, ep in enumerate(meta.get("episodes") or []):
                    if i < len(discovered_urls) and discovered_urls[i]:
                        ep["resolved_audio_url"] = discovered_urls[i]
        elif need_discover and auto_discover and sync_playwright:
            print("[INFO] Some episodes missing audio URL; discovering with Playwright...")
            _, discovered_urls = discover_with_playwright(book_url)
            if discovered_urls:
                for i, ep in enumerate(meta.get("episodes") or []):
                    if i < len(discovered_urls) and discovered_urls[i]:
                        ep["resolved_audio_url"] = discovered_urls[i]

    episodes = meta.get("episodes") or []
    if not episodes and meta.get("book_id"):
        print("[INFO] Fetching chapter list from API...")
        meta["episodes"] = fetch_chapter_list(meta["book_id"])
        episodes = meta["episodes"]
    if not episodes:
        print("[ERROR] No episodes found in metadata.")
        return False

    print(f"Book: {meta['title']} by {meta['author']} ({len(episodes)} episodes)")

    work_dir = output_dir / f"dreame_{book_id}"
    work_dir.mkdir(parents=True, exist_ok=True)
    episode_dir = work_dir / "episodes"
    episode_dir.mkdir(exist_ok=True)

    # Save book metadata JSON (title, author, narrator, length, episodes, status, listeners)
    book_info = {
        "title": meta.get("title") or "Unknown",
        "author": meta.get("author") or "Unknown",
        "narrated_by": meta.get("narrated_by") or "",
        "length": meta.get("length") or "",
        "episodes": len(episodes),
        "status": meta.get("status") or "Completed",
        "listener_count": meta.get("listener_count") or 0,
    }
    (work_dir / "book_info.json").write_text(
        json.dumps(book_info, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    episode_files = []
    chapters_for_json = []
    offset = 0.0

    with httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        for i, ep in enumerate(episodes):
            url = ep.get("resolved_audio_url")
            if not url:
                continue
            ext = "mp3"
            if ".m4a" in url.lower() or "m4a" in url.lower():
                ext = "m4a"
            # Save each chapter as a separate, human-readable file in one folder
            chap_title = safe_filename(ep.get("title") or f"Chapter {i + 1:02d}")
            filename = f"{i + 1:03d} - {chap_title}.{ext}"
            ep_path = episode_dir / filename
            print(f"Downloading {i + 1}/{len(episodes)}: {chap_title[:50]}...")
            if download_file(client, url, ep_path):
                episode_files.append(ep_path)
                dur = ep.get("duration_seconds") or 0
                chapters_for_json.append({
                    "title": ep["title"],
                    "start_time": offset,
                    "end_time": offset + dur,
                })
                offset += dur
            if DREAME_DOWNLOAD_DELAY > 0:
                time.sleep(DREAME_DOWNLOAD_DELAY)

    if not episode_files:
        print("[ERROR] No episodes downloaded.")
        return False

    # Optional: download cover
    if download_cover and meta.get("cover_url"):
        cover_path = work_dir / "cover.jpg"
        try:
            with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as c:
                if download_file(c, meta["cover_url"], cover_path):
                    print(f"Cover saved: {cover_path}")
        except Exception as e:
            print(f"[WARN] Cover download: {e}")

    # Save chapters sidecar
    if chapters_for_json:
        (work_dir / "chapters.json").write_text(
            json.dumps(chapters_for_json, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # Optional: merge to single file (for STT pipeline) or keep chapters only
    if merge_to_single:
        author_safe = safe_filename(meta["author"])
        title_safe = safe_filename(meta["title"])
        merged_name = f"{author_safe} - {title_safe}.mp3"
        merged_path = work_dir / merged_name

        if not merge_audio_files(episode_files, merged_path):
            return False
        print(f"Merged: {merged_path}")

        if upload_to_master:
            if not MASTER_URL or not ADMIN_USERNAME or not ADMIN_PASSWORD:
                print("[ERROR] Set MASTER_URL, ADMIN_USERNAME, ADMIN_PASSWORD for upload.")
                return False
            try:
                with httpx.Client(timeout=300) as http:
                    login = http.post(
                        f"{MASTER_URL}/login",
                        data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
                        follow_redirects=False,
                    )
                    if login.status_code != 303:
                        print("[ERROR] Master login failed")
                        return False
                    token = login.cookies.get("session_token")
                    with open(merged_path, "rb") as f:
                        files = {"file": (merged_name, f, "audio/mpeg")}
                        headers = {"Cookie": f"session_token={token}"}
                        up = http.post(f"{MASTER_URL}/upload", files=files, headers=headers)
                    if up.status_code != 200:
                        print(f"[ERROR] Upload failed: {up.status_code} {up.text}")
                        return False
                print(f"Uploaded to master: {merged_name}")
            except Exception as e:
                print(f"[ERROR] Upload: {e}")
                return False
        else:
            dest = WATCH_FOLDER / merged_name
            try:
                import shutil
                shutil.copy2(merged_path, dest)
                print(f"Saved to WATCH_FOLDER: {dest}")
            except Exception as e:
                print(f"[ERROR] Copy to WATCH_FOLDER: {e}")
                return False
    else:
        # Chapters-only mode: just report where we wrote them
        print(f"Chapters saved in: {episode_dir}")

    return True


def main():
    args = sys.argv[1:]
    # Determine URL and flags
    if not args:
        url = DEFAULT_BOOK_URL
        flags = []
    elif args[0].startswith("--"):
        url = DEFAULT_BOOK_URL
        flags = args
    else:
        url = args[0].strip()
        flags = args[1:]

    upload = "--upload" in flags and "--no-upload" not in flags
    merge = "--merge" in flags or upload
    download_cover = "--no-cover" not in flags
    use_saved = "--fetch" not in flags

    print(f"Using URL: {url}")
    print("Upload to master:", upload)
    print("Merge to single file:", merge)

    ok = run_scraper(
        url,
        upload_to_master=upload,
        download_cover=download_cover,
        use_saved_next_data=use_saved,
        auto_discover=True,
        merge_to_single=merge,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
