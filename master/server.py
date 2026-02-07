"""
Distributed STT Master Server
Handles task distribution, progress tracking, and result aggregation.
"""

import asyncio
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Cookie, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from database import Database
from audio_splitter import AudioSplitter

# Load environment variables from .env file
load_dotenv()

# ----- Auth Configuration -----
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

# Admin credentials from environment (defaults for development only)
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """Create a JWT token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[str]:
    """Verify JWT token and return username if valid."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        return username
    except JWTError:
        return None


def get_current_user(session_token: Optional[str] = Cookie(None)) -> Optional[str]:
    """Get current user from session cookie."""
    if not session_token:
        return None
    return verify_token(session_token)

# Configuration
UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
CHUNKS_DIR = Path(__file__).parent.parent / "chunks"
RESULTS_DIR = Path(__file__).parent.parent / "results"

# Ensure directories exist
UPLOAD_DIR.mkdir(exist_ok=True)
CHUNKS_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Distributed STT Master")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database and splitter
db = Database()
splitter = AudioSplitter(CHUNKS_DIR)

# WebSocket connections for progress updates
connected_clients: dict[str, WebSocket] = {}
dashboard_clients: list[WebSocket] = []


class TaskComplete(BaseModel):
    task_id: str
    worker_id: str
    transcript: dict
    processing_time: float


class WorkerRegister(BaseModel):
    worker_id: str
    hostname: str


# ----- WebSocket Management -----

def save_activity_log(data: dict):
    """Save activity log to database based on event type."""
    event_type = data.get("type")
    log_type = None
    message = None

    if event_type == "book_added":
        log_type = "book"
        message = f"New book: {data.get('filename')} ({data.get('total_chunks')} chunks)"
    elif event_type == "task_assigned":
        log_type = "task"
        message = f"Chunk {data.get('chunk_id')} assigned to {data.get('worker_id')}"
    elif event_type == "task_completed":
        log_type = "task"
        pt = data.get('processing_time', 0)
        message = f"Chunk {data.get('chunk_id')} completed by {data.get('worker_id')} ({pt:.1f}s)"
    elif event_type == "book_completed":
        log_type = "book"
        message = f"Book {data.get('book_id')} fully transcribed!"
    elif event_type == "worker_connected" or event_type == "worker_joined":
        log_type = "worker"
        message = f"Worker {data.get('worker_id') or data.get('hostname')} connected"
    elif event_type == "worker_disconnected":
        log_type = "worker"
        message = f"Worker {data.get('worker_id')} disconnected"

    if log_type and message:
        db.add_log(log_type, message)


async def broadcast_progress(data: dict):
    """Send progress update to all dashboard clients."""
    # Save to database
    save_activity_log(data)
    
    message = json.dumps(data)
    disconnected = []
    for ws in dashboard_clients:
        try:
            await ws.send_text(message)
        except:
            disconnected.append(ws)
    for ws in disconnected:
        dashboard_clients.remove(ws)


# ----- API Endpoints -----

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard."""
    dashboard_path = Path(__file__).parent / "dashboard.html"
    return dashboard_path.read_text(encoding="utf-8")


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Serve the login page."""
    login_path = Path(__file__).parent / "login.html"
    return login_path.read_text(encoding="utf-8")


@app.post("/login")
async def login(
    response: Response,
    username: str = Form(...),
    password: str = Form(...)
):
    """Authenticate and set session cookie."""
    if username != ADMIN_USERNAME or password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Create token and set cookie
    token = create_access_token(data={"sub": username})
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        max_age=TOKEN_EXPIRE_HOURS * 3600,
        samesite="lax"
    )
    return response


@app.post("/logout")
async def logout():
    """Clear session cookie."""
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_token")
    return response


@app.get("/auth/status")
async def auth_status(session_token: Optional[str] = Cookie(None)):
    """Check if user is logged in."""
    user = get_current_user(session_token)
    return {"authenticated": user is not None, "username": user}


@app.post("/upload")
async def upload_audiobook(
    file: UploadFile = File(...),
    session_token: Optional[str] = Cookie(None)
):
    """Upload an audiobook and split it into chunks. Requires admin auth."""
    # Check authentication
    user = get_current_user(session_token)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    book_id = str(uuid.uuid4())[:8]

    # Save uploaded file
    file_path = UPLOAD_DIR / f"{book_id}_{file.filename}"
    content = await file.read()
    file_path.write_bytes(content)

    # Split into chunks
    chunks = await splitter.split_audio(file_path, book_id)

    # Create tasks in database
    for chunk in chunks:
        db.create_task(
            book_id=book_id,
            chunk_id=chunk["chunk_id"],
            chunk_path=chunk["path"],
            start_time=chunk["start"],
            end_time=chunk["end"],
            original_filename=file.filename
        )

    # Broadcast update
    await broadcast_progress({
        "type": "book_added",
        "book_id": book_id,
        "filename": file.filename,
        "total_chunks": len(chunks)
    })

    return {
        "book_id": book_id,
        "filename": file.filename,
        "chunks_created": len(chunks)
    }


@app.get("/tasks")
async def list_tasks():
    """List all tasks with their status."""
    return db.get_all_tasks()


@app.get("/tasks/next")
async def get_next_task(worker_id: str):
    """Get next available task for a worker."""
    task = db.get_next_pending_task()
    if not task:
        return {"task": None}

    # Mark as in progress
    db.assign_task(task["id"], worker_id)

    await broadcast_progress({
        "type": "task_assigned",
        "task_id": task["id"],
        "worker_id": worker_id,
        "book_id": task["book_id"],
        "chunk_id": task["chunk_id"]
    })

    return {"task": task}


@app.get("/chunks/{chunk_filename}")
async def download_chunk(chunk_filename: str):
    """Download an audio chunk."""
    chunk_path = CHUNKS_DIR / chunk_filename
    if not chunk_path.exists():
        raise HTTPException(404, "Chunk not found")
    return FileResponse(chunk_path)


@app.post("/tasks/complete")
async def complete_task(data: TaskComplete):
    """Mark a task as complete and store the transcript."""
    db.complete_task(
        task_id=data.task_id,
        worker_id=data.worker_id,
        transcript=data.transcript,
        processing_time=data.processing_time
    )

    # Check if book is complete
    task = db.get_task(data.task_id)
    book_status = db.get_book_status(task["book_id"])

    await broadcast_progress({
        "type": "task_completed",
        "task_id": data.task_id,
        "worker_id": data.worker_id,
        "book_id": task["book_id"],
        "chunk_id": task["chunk_id"],
        "processing_time": data.processing_time,
        "book_progress": book_status
    })

    # If book is complete, merge results
    if book_status["completed"] == book_status["total"]:
        await merge_book_results(task["book_id"])

    return {"status": "ok"}


async def merge_book_results(book_id: str):
    """Merge all chunk transcripts into final result."""
    tasks = db.get_book_tasks(book_id)

    # Sort by start time
    tasks.sort(key=lambda t: t["start_time"])

    # Merge segments
    all_segments = []
    offset = 0
    for task in tasks:
        if task["transcript"]:
            transcript = json.loads(task["transcript"]) if isinstance(task["transcript"], str) else task["transcript"]
            for seg in transcript.get("segments", []):
                all_segments.append({
                    "start": seg["start"] + task["start_time"],
                    "end": seg["end"] + task["start_time"],
                    "text": seg["text"]
                })

    # Create final result
    result = {
        "book_id": book_id,
        "filename": tasks[0]["original_filename"] if tasks else "unknown",
        "completed_at": datetime.now().isoformat(),
        "total_chunks": len(tasks),
        "segments": all_segments,
        "full_text": " ".join(seg["text"].strip() for seg in all_segments)
    }

    # Save to results directory
    result_path = RESULTS_DIR / f"{book_id}_transcript.json"
    result_path.write_text(json.dumps(result, indent=2))

    # Cleanup: delete chunk files and original upload
    chunks_deleted = 0
    for task in tasks:
        try:
            chunk_path = Path(task["chunk_path"])
            if chunk_path.exists():
                chunk_path.unlink()
                chunks_deleted += 1
        except Exception as e:
            print(f"Failed to delete chunk {task['chunk_path']}: {e}")
    
    # Delete the original uploaded audiobook file
    uploads_deleted = 0
    try:
        for upload_file in UPLOAD_DIR.glob(f"{book_id}_*"):
            upload_file.unlink()
            uploads_deleted += 1
    except Exception as e:
        print(f"Failed to delete upload file: {e}")

    db.add_log("system", f"Cleanup: {chunks_deleted} chunks + {uploads_deleted} uploads deleted for book {book_id}")

    await broadcast_progress({
        "type": "book_completed",
        "book_id": book_id,
        "result_path": str(result_path)
    })


@app.get("/results/{book_id}")
async def get_result(book_id: str):
    """Download completed transcript."""
    result_path = RESULTS_DIR / f"{book_id}_transcript.json"
    if not result_path.exists():
        raise HTTPException(404, "Result not found")
    return FileResponse(result_path, filename=f"{book_id}_transcript.json")


@app.get("/status")
async def get_status():
    """Get overall system status."""
    return {
        "workers": list(connected_clients.keys()),
        "tasks": db.get_status_summary(),
        "books": db.get_all_books()
    }


# ----- Book Control Endpoints -----

@app.post("/books/{book_id}/pause")
async def pause_book(book_id: str, session_token: Optional[str] = Cookie(None)):
    """Pause processing of a specific book. Requires admin auth."""
    user = get_current_user(session_token)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    db.pause_book(book_id)
    await broadcast_progress({"type": "book_paused", "book_id": book_id})
    db.add_log("book", f"Book {book_id} paused")
    return {"status": "paused", "book_id": book_id}


@app.post("/books/{book_id}/resume")
async def resume_book(book_id: str, session_token: Optional[str] = Cookie(None)):
    """Resume processing of a specific book. Requires admin auth."""
    user = get_current_user(session_token)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    db.resume_book(book_id)
    await broadcast_progress({"type": "book_resumed", "book_id": book_id})
    db.add_log("book", f"Book {book_id} resumed")
    return {"status": "resumed", "book_id": book_id}


@app.delete("/books/{book_id}")
async def delete_book(book_id: str, session_token: Optional[str] = Cookie(None)):
    """Delete a book and all its tasks/chunks. Requires admin auth."""
    user = get_current_user(session_token)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    # Get chunk paths and delete from database
    chunk_paths = db.delete_book(book_id)
    
    # Delete chunk files
    for chunk_path in chunk_paths:
        try:
            Path(chunk_path).unlink(missing_ok=True)
        except:
            pass
    
    await broadcast_progress({"type": "book_deleted", "book_id": book_id})
    db.add_log("book", f"Book {book_id} deleted")
    return {"status": "deleted", "book_id": book_id}


@app.post("/workers/register")
async def register_worker(data: WorkerRegister):
    """Register a new worker."""
    db.register_worker(data.worker_id, data.hostname)
    await broadcast_progress({
        "type": "worker_joined",
        "worker_id": data.worker_id,
        "hostname": data.hostname
    })
    return {"status": "registered"}


@app.post("/workers/{worker_id}/heartbeat")
async def worker_heartbeat(worker_id: str):
    """Worker heartbeat to track active workers."""
    db.worker_heartbeat(worker_id)
    return {"status": "ok"}


@app.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    """WebSocket endpoint for dashboard real-time updates."""
    await websocket.accept()
    dashboard_clients.append(websocket)

    # Send current status including recent logs
    await websocket.send_text(json.dumps({
        "type": "init",
        "status": db.get_status_summary(),
        "books": db.get_all_books(),
        "workers": db.get_active_workers(),
        "logs": db.get_recent_logs(100)
    }))

    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        dashboard_clients.remove(websocket)


@app.websocket("/ws/worker/{worker_id}")
async def worker_websocket(websocket: WebSocket, worker_id: str):
    """WebSocket endpoint for worker communication."""
    await websocket.accept()
    connected_clients[worker_id] = websocket

    await broadcast_progress({
        "type": "worker_connected",
        "worker_id": worker_id
    })

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "progress":
                await broadcast_progress({
                    "type": "chunk_progress",
                    "worker_id": worker_id,
                    "task_id": data.get("task_id"),
                    "progress": data.get("progress")
                })
    except WebSocketDisconnect:
        del connected_clients[worker_id]
        await broadcast_progress({
            "type": "worker_disconnected",
            "worker_id": worker_id
        })


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print("Starting Distributed STT Master Server...")
    print(f"Dashboard: http://localhost:{port}")
    print("Workers should connect to: http://<your-ip>:8000")
    uvicorn.run(app, host="0.0.0.0", port=port)
