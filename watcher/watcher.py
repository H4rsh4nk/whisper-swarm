"""
Folder Watcher for Whisper Swarm
Monitors qBittorrent download folder and uploads completed audiobooks to master server.
"""

import json
import os
import time
import httpx
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

# Configuration
MASTER_URL = os.environ.get("MASTER_URL", "http://localhost:8000")
WATCH_FOLDER = os.environ.get("WATCH_FOLDER", r"C:\Audiobooks")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")

# Persisted list of files we've already processed (relative paths, normalized)
PROCESSED_STATE_FILE = Path(__file__).parent / "processed_files.json"

# Audio file extensions to watch for
AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.m4b', '.wav', '.flac', '.ogg', '.opus', '.aac'}


def _normalize_rel_path(path: Path, watch_path: Path) -> str:
    """Return a normalized relative path string (forward slashes) for consistent keys."""
    try:
        rel = path.resolve().relative_to(watch_path.resolve())
        return str(rel).replace("\\", "/")
    except ValueError:
        return path.name


def load_processed_paths() -> set:
    """Load the set of already-processed relative paths from disk."""
    if not PROCESSED_STATE_FILE.exists():
        return set()
    try:
        data = json.loads(PROCESSED_STATE_FILE.read_text(encoding="utf-8"))
        return set(data.get("paths", []))
    except Exception:
        return set()


def save_processed_paths(paths: set) -> None:
    """Persist the set of processed paths to disk."""
    data = {"paths": sorted(paths)}
    PROCESSED_STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


class AudiobookHandler(FileSystemEventHandler):
    """Handles new audio files appearing in the watch folder."""

    def __init__(self, watch_path: Path):
        self.watch_path = watch_path
        self.session_token = None
        self.pending_files = set()  # Files waiting for download to complete
        self.processed_paths = load_processed_paths()
        
    def login(self) -> bool:
        """Login to master server and get session token."""
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{MASTER_URL}/login",
                    data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
                    follow_redirects=False
                )
                if resp.status_code == 303:
                    # Extract session token from cookies
                    self.session_token = resp.cookies.get("session_token")
                    print(f"[OK] Logged in to {MASTER_URL}")
                    return True
                else:
                    print(f"[ERROR] Login failed: {resp.status_code}")
                    return False
        except Exception as e:
            print(f"[ERROR] Login error: {e}")
            return False
    
    def upload_file(self, file_path: Path) -> bool:
        """Upload an audio file to the master server."""
        if not self.session_token:
            if not self.login():
                return False
        
        print(f"[UPLOADING] {file_path.name}")
        
        try:
            with httpx.Client(timeout=300) as client:
                with open(file_path, 'rb') as f:
                    files = {'file': (file_path.name, f, 'audio/mpeg')}
                    resp = client.post(
                        f"{MASTER_URL}/upload",
                        files=files,
                        cookies={"session_token": self.session_token}
                    )
                
                if resp.status_code == 200:
                    print(f"[OK] Uploaded: {file_path.name}")
                    return True
                elif resp.status_code == 401:
                    print("[WARN] Session expired, re-logging in...")
                    if self.login():
                        return self.upload_file(file_path)  # Retry
                    return False
                else:
                    print(f"[ERROR] Upload failed: {resp.status_code} - {resp.text}")
                    return False
                    
        except Exception as e:
            print(f"[ERROR] Upload error: {e}")
            return False
    
    def is_file_ready(self, file_path: Path) -> bool:
        """Check if file is fully downloaded (not being written to)."""
        try:
            # Try to get exclusive access
            initial_size = file_path.stat().st_size
            time.sleep(2)  # Wait a bit
            final_size = file_path.stat().st_size
            
            # If size hasn't changed, file is likely complete
            return initial_size == final_size and initial_size > 0
        except:
            return False
    
    def check_exists(self, filename: str) -> bool:
        """Check if the master server already knows about this file."""
        if not self.session_token:
            if not self.login():
                return False
                
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(
                    f"{MASTER_URL}/books/exists",
                    params={"filename": filename},
                    cookies={"session_token": self.session_token}
                )
                if resp.status_code == 200:
                    return resp.json().get("exists", False)
                elif resp.status_code == 401:
                    print("[WARN] Session expired during exists check, re-logging in...")
                    if self.login():
                        return self.check_exists(filename)
            return False
        except Exception as e:
            print(f"[ERROR] Check exists error: {e}")
            return False

    def process_file(self, file_path: Path):
        """Process a new audio file."""
        if file_path.suffix.lower() not in AUDIO_EXTENSIONS:
            return

        rel_path = _normalize_rel_path(file_path, self.watch_path)

        # Skip if we've already processed this file (persisted across restarts)
        if rel_path in self.processed_paths:
            print(f"[SKIP] Already processed: {rel_path}")
            return

        # Check if the master server already knows about this file
        if self.check_exists(file_path.name):
            print(f"[SKIP] Server already has: {file_path.name}")
            self.processed_paths.add(rel_path)
            save_processed_paths(self.processed_paths)
            return

        # Skip files still being downloaded
        if not self.is_file_ready(file_path):
            print(f"[WAIT] File still downloading: {file_path.name}")
            self.pending_files.add(file_path)
            return

        # Remove from pending if present
        self.pending_files.discard(file_path)

        # Upload the file
        if self.upload_file(file_path):
            self.processed_paths.add(rel_path)
            save_processed_paths(self.processed_paths)
    
    def on_created(self, event):
        """Handle new file creation."""
        if event.is_directory:
            return
        
        file_path = Path(event.src_path)
        
        # Schedule processing after a delay to let download complete
        try:
            rel_path = file_path.relative_to(WATCH_FOLDER)
        except ValueError:
            rel_path = file_path.name
        print(f"[NEW] Detected: {rel_path}")
        time.sleep(5)  # Initial wait
        self.process_file(file_path)
    
    def on_modified(self, event):
        """Handle file modification (download progress)."""
        if event.is_directory:
            return
            
        file_path = Path(event.src_path)
        
        # Check if this was a pending file that might now be complete
        if file_path in self.pending_files:
            self.process_file(file_path)
    
    def scan_existing(self):
        """Scan for existing audio files on startup (recursive)."""
        print(f"[SCAN] Checking {WATCH_FOLDER} for existing audio files (including subfolders)...")

        watch_path = self.watch_path
        for ext in AUDIO_EXTENSIONS:
            for file_path in watch_path.glob(f"**/*{ext}"):
                if file_path.is_file():
                    rel = _normalize_rel_path(file_path, watch_path)
                    if rel in self.processed_paths:
                        print(f"[SKIP] Already processed: {rel}")
                        continue
                    print(f"[FOUND] {rel}")
                    self.process_file(file_path)


def main():
    print("=" * 60)
    print("Whisper Swarm - Folder Watcher")
    print("=" * 60)
    print(f"Master URL: {MASTER_URL}")
    print(f"Watch Folder: {WATCH_FOLDER}")
    print(f"Audio Extensions: {', '.join(AUDIO_EXTENSIONS)}")
    print("=" * 60)
    
    # Verify watch folder exists
    watch_path = Path(WATCH_FOLDER)
    if not watch_path.exists():
        print(f"[ERROR] Watch folder does not exist: {WATCH_FOLDER}")
        return

    handler = AudiobookHandler(watch_path)
    
    # Initial login
    if not handler.login():
        print("[WARN] Initial login failed, will retry on first upload")
    
    # Scan existing files
    handler.scan_existing()
    
    # Start watching
    observer = Observer()
    observer.schedule(handler, str(watch_path), recursive=True)
    observer.start()
    
    print(f"\n[WATCHING] Monitoring {WATCH_FOLDER} for new audiobooks...")
    print("Press Ctrl+C to stop.\n")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[STOP] Shutting down watcher...")
        observer.stop()
    
    observer.join()
    print("[DONE] Watcher stopped.")


if __name__ == "__main__":
    main()
