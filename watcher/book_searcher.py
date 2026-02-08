"""
Book Searcher for Whisper Swarm
Searches for audiobooks on AudiobookBay and sends magnet links to qBittorrent.

Usage:
1. Configure .env with site credentials and URLs
2. Add book titles to books.txt (one per line)
3. Run: python book_searcher.py
"""

import os
import re
import time
import httpx
from pathlib import Path
from urllib.parse import quote_plus
from dotenv import load_dotenv
from bs4 import BeautifulSoup

# Load .env from the same directory as this script (override=True to use .env values)
ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE, override=True)

# ----- Configuration -----
# Website settings (strip trailing slash so we don't get double slashes in URLs)
SITE_URL = (os.environ.get("SITE_URL", "https://audiobookbay.lu") or "").rstrip("/")
SITE_USERNAME = os.environ.get("SITE_USERNAME", "")
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "")

# qBittorrent settings
QBITTORRENT_HOST = os.environ.get("QBITTORRENT_HOST", "http://localhost:8080")
QBITTORRENT_USER = os.environ.get("QBITTORRENT_USER", "admin")
QBITTORRENT_PASS = os.environ.get("QBITTORRENT_PASS", "adminadmin")

# File paths
BOOKS_FILE = Path(__file__).parent / "books.txt"
PROCESSED_FILE = Path(__file__).parent / "books_processed.txt"

# Delay between searches (be respectful to the server)
SEARCH_DELAY = 3  # seconds


class BookSearcher:
    def __init__(self):
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        self.qbit_cookies = None
        self.logged_in = False
    
    def login_to_site(self) -> bool:
        """Login to AudiobookBay."""
        print(f"[LOGIN] Logging into {SITE_URL}...")
        
        if not SITE_USERNAME or not SITE_PASSWORD:
            print("[WARN] No credentials provided, attempting without login")
            return True
        
        try:
            # Get login page first (may need for cookies/CSRF)
            login_url = f"{SITE_URL}/member/login"
            self.client.get(login_url)
            
            # Submit login form
            resp = self.client.post(login_url, data={
                "username": SITE_USERNAME,
                "password": SITE_PASSWORD,
                "redirect": "/",
                "submit": "Login"
            })
            
            # Check if login successful by looking for logout link
            if "logout" in resp.text.lower() or "my account" in resp.text.lower():
                print("[OK] Logged in successfully")
                self.logged_in = True
                return True
            elif "invalid" in resp.text.lower() or "error" in resp.text.lower():
                print("[ERROR] Login failed - invalid credentials")
                return False
            else:
                # Might still be logged in, check by accessing a protected page
                print("[INFO] Login status unclear, proceeding...")
                self.logged_in = True
                return True
                
        except Exception as e:
            print(f"[ERROR] Login failed: {e}")
            return False
    
    def search_book(self, title: str) -> list:
        """
        Search for a book on AudiobookBay and return list of magnet links.
        """
        print(f"[SEARCH] Searching for: {title}")
        
        try:
            # Search URL format for AudiobookBay
            # Use lowercase query + cat param so the site returns actual search results (not default listing)
            search_query = quote_plus(title.lower())
            search_url = f"{SITE_URL}/?s={search_query}&cat=undefined%2Cundefined"
            print(f"[SEARCH] URL: {search_url}")
            
            resp = self.client.get(search_url)
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Check if no results
            if "not found" in resp.text.lower() or "no results" in resp.text.lower():
                print(f"[INFO] No results found for: {title}")
                return []
            
            # Find audiobook links - they're in /abss/ paths (main content first; avoid duplicates)
            book_links = []
            seen = set()
            for link in soup.find_all('a', href=True):
                href = link['href'].strip()
                if '/abss/' in href:
                    # Normalize: use full URL for dedup (strip trailing slash)
                    key = href.rstrip("/")
                    if key not in seen:
                        seen.add(key)
                        book_links.append(href)
            
            if not book_links:
                print(f"[INFO] No audiobook links found for: {title}")
                return []
            
            print(f"[INFO] Found {len(book_links)} potential matches")
            
            # Get the first result's detail page to find the magnet/torrent info
            magnets = []
            for book_url in book_links[:3]:  # Check up to 3 results
                if not book_url.startswith('http'):
                    book_url = f"{SITE_URL}{book_url}"
                
                magnet = self.get_magnet_from_page(book_url, title)
                if magnet:
                    magnets.append(magnet)
                    break  # Got one, that's enough
                
                time.sleep(1)  # Small delay between page requests
            
            return magnets
            
        except Exception as e:
            print(f"[ERROR] Search failed: {e}")
            return []
    
    def get_magnet_from_page(self, page_url: str, search_title: str) -> str:
        """Extract magnet link from audiobook detail page."""
        try:
            resp = self.client.get(page_url)
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Look for info hash in the page
            # AudiobookBay displays it as "Info Hash: <hash>"
            page_text = resp.text
            
            # Method 1: Look for magnet links directly
            for link in soup.find_all('a', href=True):
                href = link['href']
                if href.startswith('magnet:'):
                    print(f"[FOUND] Direct magnet link")
                    return href
            
            # Method 2: Find info hash and construct magnet link
            # Pattern: Info Hash: followed by 40 hex characters
            hash_match = re.search(r'Info\s*Hash[:\s]+([a-fA-F0-9]{40})', page_text)
            if hash_match:
                info_hash = hash_match.group(1)
                # Construct magnet link
                magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={quote_plus(search_title)}"
                
                # Try to find trackers on the page
                trackers = self.extract_trackers(soup, page_text)
                for tracker in trackers:
                    magnet += f"&tr={quote_plus(tracker)}"
                
                print(f"[FOUND] Constructed magnet from hash: {info_hash[:8]}...")
                return magnet
            
            # Method 3: Look for hash in specific divs
            for div in soup.find_all(['div', 'span', 'td'], string=re.compile(r'[a-fA-F0-9]{40}')):
                text = div.get_text()
                hash_match = re.search(r'([a-fA-F0-9]{40})', text)
                if hash_match:
                    info_hash = hash_match.group(1)
                    magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={quote_plus(search_title)}"
                    print(f"[FOUND] Hash in element: {info_hash[:8]}...")
                    return magnet
            
            print(f"[WARN] No magnet/hash found on page")
            return ""
            
        except Exception as e:
            print(f"[ERROR] Failed to get magnet from page: {e}")
            return ""
    
    def extract_trackers(self, soup: BeautifulSoup, page_text: str) -> list:
        """Extract tracker URLs from the page."""
        trackers = []
        
        # Common tracker patterns
        tracker_pattern = r'(udp://[^\s<>"]+|http://[^\s<>"]*announce[^\s<>"]*)'
        matches = re.findall(tracker_pattern, page_text)
        
        for match in matches:
            if match not in trackers:
                trackers.append(match)
        
        # Add some common public trackers as fallback
        default_trackers = [
            "udp://tracker.opentrackr.org:1337/announce",
            "udp://open.stealth.si:80/announce",
            "udp://tracker.torrent.eu.org:451/announce",
            "udp://exodus.desync.com:6969/announce"
        ]
        
        for t in default_trackers:
            if t not in trackers:
                trackers.append(t)
        
        return trackers[:10]  # Limit to 10 trackers
    
    def login_to_qbittorrent(self) -> bool:
        """Login to qBittorrent Web UI."""
        try:
            resp = self.client.post(
                f"{QBITTORRENT_HOST}/api/v2/auth/login",
                data={"username": QBITTORRENT_USER, "password": QBITTORRENT_PASS}
            )
            if resp.text == "Ok.":
                self.qbit_cookies = resp.cookies
                print("[OK] Connected to qBittorrent")
                return True
            else:
                print(f"[ERROR] qBittorrent login failed: {resp.text}")
                return False
        except Exception as e:
            print(f"[ERROR] qBittorrent connection error: {e}")
            return False
    
    def add_magnet(self, magnet: str, title: str) -> bool:
        """Add a magnet link to qBittorrent."""
        if not self.qbit_cookies:
            if not self.login_to_qbittorrent():
                return False
        
        try:
            resp = self.client.post(
                f"{QBITTORRENT_HOST}/api/v2/torrents/add",
                data={"urls": magnet, "category": "audiobooks"},
                cookies=self.qbit_cookies
            )
            if resp.status_code == 200:
                print(f"[OK] Added torrent: {title}")
                return True
            else:
                print(f"[ERROR] Failed to add torrent: {resp.status_code}")
                return False
        except Exception as e:
            print(f"[ERROR] Add torrent error: {e}")
            return False
    
    def load_books(self) -> list:
        """Load book titles from books.txt."""
        if not BOOKS_FILE.exists():
            print(f"[ERROR] Books file not found: {BOOKS_FILE}")
            return []
        
        raw = BOOKS_FILE.read_text(encoding='utf-8-sig')  # utf-8-sig strips BOM if present
        books = []
        for line in raw.replace("\r\n", "\n").replace("\r", "\n").splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                books.append(line)
        
        print(f"[INFO] Loaded {len(books)} books from {BOOKS_FILE.name}")
        return books
    
    def load_processed(self) -> set:
        """Load already processed book titles."""
        if not PROCESSED_FILE.exists():
            return set()
        
        processed = set()
        for line in PROCESSED_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line:
                processed.add(line.lower())
        
        return processed
    
    def mark_processed(self, title: str):
        """Mark a book as processed."""
        with open(PROCESSED_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{title}\n")
    
    def run(self, dry_run: bool = False):
        """Main processing loop. If dry_run=True, only search and print magnets (no qBittorrent)."""
        print("=" * 60)
        print("Whisper Swarm - Book Searcher (AudiobookBay)")
        print("=" * 60)
        print(f"Using site: {SITE_URL}")
        print(f"  (open in browser to verify it's the right site)")
        print(f"Username: {SITE_USERNAME}")
        print(f"qBittorrent: {QBITTORRENT_HOST}")
        if dry_run:
            print("Mode: DRY RUN (search only, no qBittorrent)")
        print("=" * 60)
        
        # Load books
        books = self.load_books()
        if not books:
            print("[DONE] No books to process")
            return
        
        processed = self.load_processed()
        
        # Filter out already processed
        pending = [b for b in books if b.lower() not in processed]
        print(f"[INFO] {len(pending)} books pending ({len(processed)} already processed)")
        
        if not pending:
            print("[DONE] All books already processed")
            return
        
        # Login to site (optional for search - many sites show results without login)
        self.login_to_site()
        
        # Login to qBittorrent (skip in dry run)
        if not dry_run and not self.login_to_qbittorrent():
            print("[ERROR] Cannot proceed without qBittorrent connection")
            return
        
        # Process each book
        success_count = 0
        for i, title in enumerate(pending, 1):
            print(f"\n[{i}/{len(pending)}] Processing: {title}")
            
            # Search for the book
            magnets = self.search_book(title)
            
            if magnets:
                magnet = magnets[0]
                if dry_run:
                    print(f"[DRY RUN] Would add magnet: {magnet[:80]}...")
                    success_count += 1
                elif self.add_magnet(magnet, title):
                    self.mark_processed(title)
                    success_count += 1
            else:
                print(f"[NOT FOUND] No results for: {title}")
            
            # Delay between searches
            if i < len(pending):
                time.sleep(SEARCH_DELAY)
        
        print(f"\n[DONE] Processed {success_count}/{len(pending)} books successfully")


def main():
    import sys
    dry_run = "--dry-run" in sys.argv
    searcher = BookSearcher()
    searcher.run(dry_run=dry_run)


if __name__ == "__main__":
    main()
