# AGENTS.md

## Cursor Cloud specific instructions

### Overview
Distributed Speech-to-Text system with master/worker architecture. Master runs a FastAPI web server (port 8000) with SQLite; workers connect to transcribe audio using Whisper.

### Services
| Service | Command | Notes |
|---------|---------|-------|
| Master Server | `python3 master/server.py` | Runs on port 8000. Dashboard at `http://localhost:8000`. SQLite DB auto-created at `stt_tasks.db`. |
| Worker | `python3 worker/worker.py http://localhost:8000` | Requires `faster-whisper`; downloads Whisper model on first run (~150 MB for `base`). |

### Key dev notes
- **FFmpeg** is a hard system dependency for the master (audio splitting). It is pre-installed on the VM.
- **No external database** needed — SQLite is embedded and auto-initialized at startup.
- **Default credentials**: username `admin`, password `changeme` (from env vars `ADMIN_USERNAME` / `ADMIN_PASSWORD`).
- The `av` Python package (dependency of `faster-whisper`) requires `python3-dev` headers to build from source. These are pre-installed on the VM.
- Worker uses CPU by default (no CUDA/GPU in this environment). Set `WHISPER_MODEL` env var to control model size (default `base`).
- There are no automated tests or linting tools configured in this repository.
- Refer to `README.md` for architecture details and API reference.
