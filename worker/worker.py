"""
Distributed STT Worker Client
Connects to master server, downloads audio chunks, transcribes, and uploads results.
"""

import asyncio
import json
import os
import platform
import socket
import sys
import time
import uuid
from pathlib import Path

import httpx
import websockets
from faster_whisper import WhisperModel

# Configuration
WORKER_ID = f"worker-{uuid.uuid4().hex[:6]}"
HOSTNAME = socket.gethostname()
TEMP_DIR = Path.home() / ".stt_worker" / "temp"
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "base")  # tiny, base, small, medium, large


class STTWorker:
    def __init__(self, master_url: str):
        self.master_url = master_url.rstrip("/")
        self.ws_url = self.master_url.replace("http://", "ws://").replace("https://", "wss://")
        self.model = None
        self.ws = None
        self.running = True

        TEMP_DIR.mkdir(parents=True, exist_ok=True)

    def load_model(self):
        """Load the Whisper model."""
        print(f"Loading Whisper model '{MODEL_SIZE}'...")

        # Use GPU if available (CUDA), otherwise CPU
        device = "cuda" if self._has_cuda() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"

        self.model = WhisperModel(
            MODEL_SIZE,
            device=device,
            compute_type=compute_type
        )
        print(f"Model loaded on {device}")

    def _has_cuda(self) -> bool:
        """Check if CUDA is available."""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False

    async def register(self):
        """Register with the master server."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(
                f"{self.master_url}/workers/register",
                json={"worker_id": WORKER_ID, "hostname": HOSTNAME}
            )
        print(f"Registered as {WORKER_ID} ({HOSTNAME})")

    async def connect_websocket(self):
        """Connect to master via WebSocket for progress updates."""
        try:
            self.ws = await websockets.connect(f"{self.ws_url}/ws/worker/{WORKER_ID}")
            print("WebSocket connected")
        except Exception as e:
            print(f"WebSocket connection failed: {e}")
            self.ws = None

    async def send_progress(self, task_id: str, progress: float):
        """Send progress update to master."""
        if self.ws:
            try:
                await self.ws.send(json.dumps({
                    "type": "progress",
                    "task_id": task_id,
                    "progress": progress
                }))
            except:
                pass

    async def heartbeat(self):
        """Send periodic heartbeat to master."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            while self.running:
                try:
                    await client.post(f"{self.master_url}/workers/{WORKER_ID}/heartbeat")
                except:
                    pass
                await asyncio.sleep(30)

    async def get_next_task(self) -> dict | None:
        """Get next available task from master."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.get(
                    f"{self.master_url}/tasks/next",
                    params={"worker_id": WORKER_ID}
                )
                data = resp.json()
                return data.get("task")
            except Exception as e:
                print(f"Error getting task: {e}")
                return None

    async def download_chunk(self, chunk_path: str) -> Path:
        """Download audio chunk from master."""
        chunk_filename = Path(chunk_path).name
        local_path = TEMP_DIR / chunk_filename

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(f"{self.master_url}/chunks/{chunk_filename}")
            local_path.write_bytes(resp.content)

        return local_path

    def transcribe(self, audio_path: Path, task_id: str) -> dict:
        """Transcribe audio using Whisper."""
        segments_list = []

        segments, info = self.model.transcribe(
            str(audio_path),
            beam_size=5,
            language="en",  # Change if needed or set to None for auto-detect
            vad_filter=True
        )

        for segment in segments:
            segments_list.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text
            })

        return {
            "language": info.language,
            "language_probability": info.language_probability,
            "duration": info.duration,
            "segments": segments_list
        }

    async def submit_result(self, task_id: str, transcript: dict, processing_time: float):
        """Submit transcription result to master."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            await client.post(
                f"{self.master_url}/tasks/complete",
                json={
                    "task_id": task_id,
                    "worker_id": WORKER_ID,
                    "transcript": transcript,
                    "processing_time": processing_time
                }
            )

    async def process_task(self, task: dict):
        """Process a single task."""
        task_id = task["id"]
        chunk_path = task["chunk_path"]

        print(f"\nProcessing task {task_id}")
        print(f"  Chunk: {Path(chunk_path).name}")

        # Download chunk
        print("  Downloading chunk...")
        local_path = await self.download_chunk(chunk_path)

        # Transcribe
        print("  Transcribing...")
        start_time = time.time()
        transcript = self.transcribe(local_path, task_id)
        processing_time = time.time() - start_time

        print(f"  Done in {processing_time:.1f}s")
        print(f"  Segments: {len(transcript['segments'])}")

        # Submit result
        print("  Submitting result...")
        await self.submit_result(task_id, transcript, processing_time)

        # Cleanup
        local_path.unlink(missing_ok=True)

        print(f"  Task {task_id} complete!")

    async def run(self):
        """Main worker loop."""
        print(f"\n{'='*50}")
        print(f"Distributed STT Worker")
        print(f"{'='*50}")
        print(f"Worker ID: {WORKER_ID}")
        print(f"Hostname: {HOSTNAME}")
        print(f"Master: {self.master_url}")
        print(f"Model: {MODEL_SIZE}")
        print(f"{'='*50}\n")

        # Load model
        self.load_model()

        # Register and connect
        await self.register()
        await self.connect_websocket()

        # Start heartbeat task
        heartbeat_task = asyncio.create_task(self.heartbeat())

        print("\nWaiting for tasks...")

        try:
            while self.running:
                task = await self.get_next_task()

                if task:
                    await self.process_task(task)
                else:
                    # No tasks available, wait before checking again
                    await asyncio.sleep(5)

        except KeyboardInterrupt:
            print("\nShutting down...")
            self.running = False
        finally:
            heartbeat_task.cancel()
            if self.ws:
                await self.ws.close()


def main():
    print("\n" + "="*50)
    print("  Distributed STT Worker Setup")
    print("="*50 + "\n")

    # Get master URL from argument or prompt
    if len(sys.argv) > 1:
        master_url = sys.argv[1]
    else:
        master_url = input("Enter master server URL (e.g., http://192.168.1.100:8000): ").strip()

    if not master_url.startswith("http"):
        master_url = f"http://{master_url}"

    # Only add :8000 for http:// URLs without a port (not for https/ngrok URLs)
    host_part = master_url.split("//")[1]
    if master_url.startswith("http://") and ":" not in host_part.split("/")[0]:
        master_url = f"{master_url}:8000"

    worker = STTWorker(master_url)
    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
