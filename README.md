# Distributed Speech-to-Text System

A distributed system for transcribing audiobooks using multiple computers. Split the work across friends' PCs and get results faster.

## Quick Start

### Prerequisites
- **Python 3.10+** on all machines
- **FFmpeg** on master machine (for audio splitting)
  - Windows: `winget install ffmpeg` or download from https://ffmpeg.org
  - Mac: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`

### 1. Start Master Server (Your Laptop)

**Windows:**
```batch
run_master.bat
```

**Mac/Linux:**
```bash
./run_master.sh
```

This will:
- Create a virtual environment
- Install dependencies
- Show your IP address
- Start the server at http://localhost:8000

### 2. Start Workers (Friends' Computers)

Give your friends these files:
- `worker/` folder
- `run_worker.bat` (Windows) or `run_worker.sh` (Mac/Linux)

They run:
```batch
run_worker.bat
```

When prompted, they enter your IP: `http://YOUR_IP:8000`

### 3. Upload Audiobooks

1. Open http://localhost:8000 in your browser
2. Drag & drop audio files (MP3, WAV, M4A, FLAC)
3. Watch progress on the dashboard
4. Download completed transcripts

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    YOUR LAPTOP (Master)                      │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐ │
│  │ Web Server  │  │ Task Queue  │  │ Dashboard            │ │
│  │ (FastAPI)   │  │ (SQLite)    │  │ Progress monitoring  │ │
│  └─────────────┘  └─────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│ Friend 1 PC   │  │ Friend 2 PC   │  │ Friend 3 PC   │
│ worker.py     │  │ worker.py     │  │ worker.py     │
│ + Whisper     │  │ + Whisper     │  │ + Whisper     │
└───────────────┘  └───────────────┘  └───────────────┘
```

## Project Structure

```
distributed-stt/
├── master/
│   ├── server.py           # FastAPI server
│   ├── database.py         # SQLite task queue
│   ├── audio_splitter.py   # Split audiobooks into chunks
│   ├── dashboard.html      # Web dashboard
│   └── requirements.txt
├── worker/
│   ├── worker.py           # Worker client
│   ├── build_exe.py        # PyInstaller build script
│   └── requirements.txt
├── uploads/                # Uploaded audiobooks
├── chunks/                 # Audio chunks (5-min segments)
├── results/                # Completed transcripts
├── run_master.bat/.sh      # Start master server
├── run_worker.bat/.sh      # Start worker client
└── README.md
```

## Configuration

### Whisper Model Size

Set the `WHISPER_MODEL` environment variable before running worker:

| Model | Size | Speed | Quality |
|-------|------|-------|---------|
| tiny | ~75MB | Fastest | Basic |
| **base** | ~150MB | Fast | Good (default) |
| small | ~500MB | Medium | Better |
| medium | ~1.5GB | Slow | High |
| large | ~3GB | Slowest | Best |

```batch
set WHISPER_MODEL=small
run_worker.bat
```

### Chunk Duration

Edit `master/audio_splitter.py` to change chunk size (default: 5 minutes):
```python
self.chunk_duration = 300  # seconds
```

## Network Setup

### Same Network (LAN)
Just use your local IP (e.g., `192.168.1.100`). Works out of the box.

### Different Networks (Internet)
Options:
1. **Tailscale** (recommended): Install on all machines, use Tailscale IPs
2. **ngrok**: Run `ngrok http 8000` and share the URL
3. **Port forwarding**: Forward port 8000 on your router

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard |
| `/upload` | POST | Upload audiobook |
| `/tasks` | GET | List all tasks |
| `/tasks/next?worker_id=X` | GET | Get next task for worker |
| `/tasks/complete` | POST | Submit completed transcription |
| `/chunks/{filename}` | GET | Download audio chunk |
| `/results/{book_id}` | GET | Download completed transcript |
| `/status` | GET | System status |
| `/ws/dashboard` | WS | Dashboard real-time updates |
| `/ws/worker/{id}` | WS | Worker progress updates |

## Output Format

```json
{
  "book_id": "a1b2c3d4",
  "filename": "audiobook.mp3",
  "completed_at": "2024-01-15T10:30:00",
  "total_chunks": 24,
  "segments": [
    {
      "start": 0.0,
      "end": 5.2,
      "text": "Chapter one. It was a dark and stormy night..."
    }
  ],
  "full_text": "Chapter one. It was a dark and stormy night..."
}
```

## Building Standalone Worker (Optional)

To create a single `.exe` file for friends (no Python needed):

```batch
cd worker
pip install pyinstaller
python build_exe.py
```

This creates `dist/stt_worker.exe` (~2GB with model).

## Troubleshooting

**"Connection refused" on worker**
- Check firewall allows port 8000
- Verify master IP is correct
- Make sure master is running

**FFmpeg not found**
- Install FFmpeg and add to PATH
- Restart terminal after installation

**Out of memory on worker**
- Use smaller Whisper model: `set WHISPER_MODEL=tiny`
- Close other applications

**Slow transcription**
- Use GPU if available (CUDA)
- Use smaller model
- Reduce chunk duration

## License

MIT
