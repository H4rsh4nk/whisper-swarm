"""
Folder Watcher for Whisper Swarm
Monitors qBittorrent download folder and uploads completed audiobooks to master server.
"""

import os
import time
import httpx
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from dotenv import load_dotenv

load_dotenv()

# Configuration
MASTER_URL = os.environ.get("MASTER_URL", "http://localhost:8000")
WATCH_FOLDER = os.environ.get("WATCH_FOLDER", r"C:\Audiobooks")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")

# Audio file extensions to watch for
AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.m4b', '.wav', '.flac', '.ogg', '.opus', '.aac'}


class AudiobookHandler(FileSystemEventHandler):
    """Handles new audio files appearing in the watch folder."""
    
    def __init__(self):
        self.session_token = None
        self.pending_files = set()  # Files waiting for download to complete
        
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
    
    def process_file(self, file_path: Path):
        """Process a new audio file."""
        if file_path.suffix.lower() not in AUDIO_EXTENSIONS:
            return
            
        # Skip files still being downloaded
        if not self.is_file_ready(file_path):
            print(f"[WAIT] File still downloading: {file_path.name}")
            self.pending_files.add(file_path)
            return
        
        # Remove from pending if present
        self.pending_files.discard(file_path)
        
        # Upload the file
        self.upload_file(file_path)
    
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
        
        watch_path = Path(WATCH_FOLDER)
        for ext in AUDIO_EXTENSIONS:
            # Use ** for recursive glob
            for file_path in watch_path.glob(f"**/*{ext}"):
                if file_path.is_file():
                    print(f"[FOUND] {file_path.relative_to(watch_path)}")
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
    
    handler = AudiobookHandler()
    
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
