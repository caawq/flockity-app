"""
Flockity FastAPI Monolithic Server
Serves frontend (index.html) and handles audio rendering with queue management
"""

import os
import uuid
import asyncio
import shutil
import requests
import time
from pathlib import Path
from typing import List

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

import worker_render

# ===== CONFIGURATION =====
BASE_DIR = Path(r"C:\Users\SELIK\Desktop\flockity-app")
FRONTEND_PATH = BASE_DIR / "index.html"
USERS_DIR = BASE_DIR / "users"

# Telegram Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN", "7968711703:AAF3AHwxTYD3xnw5YSHDkC8xPLJM3fz2A6k")

os.makedirs(USERS_DIR, exist_ok=True)

app = FastAPI(title="Flockity Audio Generator")

# CORS for Telegram WebApp
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global render lock for queue management (one FL Studio instance at a time)
render_lock = asyncio.Lock()


# ===== FRONTEND DELIVERY =====
@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve monolithic frontend (index.html)"""
    if not FRONTEND_PATH.exists():
        raise HTTPException(status_code=500, detail="Frontend not found")

    with open(FRONTEND_PATH, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ===== TELEGRAM SEND AUDIO =====
def send_audio_to_telegram(chat_id: str, audio_path: str, caption: str):
    """
    Send audio file to Telegram user

    Args:
        chat_id: Telegram user ID
        audio_path: Path to WAV file
        caption: Message caption
    """
    if not chat_id or chat_id == "":
        print(f"[WARNING] No chat_id provided, skipping Telegram send")
        return

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendAudio"

        with open(audio_path, "rb") as audio_file:
            files = {"audio": audio_file}
            data = {
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": "HTML"
            }

            response = requests.post(url, data=data, files=files, timeout=60)

            if response.status_code == 200:
                print(f"[SUCCESS] Audio sent to Telegram user {chat_id}")
            else:
                print(f"[ERROR] Telegram API error: {response.text}")

    except Exception as e:
        print(f"[ERROR] Failed to send audio to Telegram: {str(e)}")


# ===== RENDER ENDPOINT =====
@app.post("/api/render")
async def start_render(
    background_tasks: BackgroundTasks,
    knob_value: int = Form(50),
    room_size: str = Form("middle"),
    bpm: int = Form(140),
    username: str = Form("guest"),
    user_id: str = Form("local_user"),
    loops: List[UploadFile] = File(default=[]),
    shots: List[UploadFile] = File(default=[])
):
    """
    Start audio render job with automatic queueing and session isolation

    Architecture:
    - Each render gets isolated directory: users/{user_id}/render_{timestamp}/
    - Inside: session_loops/, session_shots/, render_output/
    - After render: send audio to Telegram bot

    Parameters (from FormData):
    - knob_value: DRY/WET knob (0-100)
    - room_size: Room intensity ("small", "middle", "large")
    - bpm: Tempo (60-200)
    - username: Telegram username or 'guest'
    - user_id: Telegram user ID or 'local_user'
    - loops: Loop audio files (max 15)
    - shots: One-shot audio files (max 15)

    Returns:
        JSON with task_id and download_url
    """
    import datetime

    # STAGE 1: PAYLOAD VALIDATION
    stage_start = datetime.datetime.now()
    print(f"\n{'='*60}")
    print(f"[{stage_start.strftime('%H:%M:%S.%f')[:-3]}] [STAGE 1: PAYLOAD] Starting validation")
    print(f"[STAGE 1] User ID: {user_id}")
    print(f"[STAGE 1] Username: {username}")
    print(f"[STAGE 1] BPM: {bpm}, DRY/WET: {knob_value}, Room: {room_size}")
    print(f"[STAGE 1] Loops count: {len(loops)}, Shots count: {len(shots)}")

    if room_size not in ["small", "middle", "large"]:
        print(f"[STAGE 1: FAIL] Invalid room_size: {room_size}")
        raise HTTPException(status_code=400, detail=f"STAGE 1 FAILED: room_size must be 'small', 'middle', or 'large', got '{room_size}'")

    if not (60 <= bpm <= 200):
        print(f"[STAGE 1: FAIL] Invalid bpm: {bpm}")
        raise HTTPException(status_code=400, detail=f"STAGE 1 FAILED: bpm must be between 60 and 200, got {bpm}")

    if not (0 <= knob_value <= 100):
        print(f"[STAGE 1: FAIL] Invalid knob_value: {knob_value}")
        raise HTTPException(status_code=400, detail=f"STAGE 1 FAILED: knob_value must be between 0 and 100, got {knob_value}")

    print(f"[STAGE 1: OK] All parameters validated ({(datetime.datetime.now() - stage_start).total_seconds():.3f}s)")

    # Create session-isolated directory structure
    timestamp = int(time.time() * 1000)
    session_name = f"render_{timestamp}"

    # Sanitize username (remove @ if present, ensure safe for filesystem)
    safe_username = username.replace("@", "").replace(" ", "_")
    if safe_username == "" or safe_username == "guest":
        safe_username = "guest"

    # Use user_id as folder name instead of username
    user_dir = USERS_DIR / str(user_id)
    session_dir = user_dir / session_name

    session_loops_dir = session_dir / "session_loops"
    session_shots_dir = session_dir / "session_shots"
    render_output_dir = session_dir / "render_output"

    # Create directory structure
    session_loops_dir.mkdir(parents=True, exist_ok=True)
    session_shots_dir.mkdir(parents=True, exist_ok=True)
    render_output_dir.mkdir(parents=True, exist_ok=True)

    task_id = str(uuid.uuid4())

    try:
        # STAGE 2: DISK SYNC
        stage_start = datetime.datetime.now()
        print(f"\n[{stage_start.strftime('%H:%M:%S.%f')[:-3]}] [STAGE 2: DISK SYNC] Writing uploaded files")

        loop_paths = []
        for i, upload in enumerate(loops):
            if upload.filename:
                temp_path = session_loops_dir / f"loop_{i}_{upload.filename}"
                content = await upload.read()

                if len(content) == 0:
                    print(f"[STAGE 2: FAIL] Loop {i} '{upload.filename}' has 0 bytes")
                    raise HTTPException(status_code=400, detail=f"STAGE 2 FAILED: Loop file '{upload.filename}' is empty")

                with open(temp_path, "wb") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())

                file_size = os.path.getsize(temp_path)
                if file_size == 0:
                    print(f"[STAGE 2: FAIL] Loop {i} written but size is 0 bytes on disk")
                    raise HTTPException(status_code=500, detail=f"STAGE 2 FAILED: Loop file '{upload.filename}' failed disk write verification")

                loop_paths.append(str(temp_path.absolute()))
                print(f"[STAGE 2] Loop {i}: {upload.filename} -> {file_size} bytes OK")

        shot_paths = []
        for i, upload in enumerate(shots):
            if upload.filename:
                temp_path = session_shots_dir / f"shot_{i}_{upload.filename}"
                content = await upload.read()

                if len(content) == 0:
                    print(f"[STAGE 2: FAIL] Shot {i} '{upload.filename}' has 0 bytes")
                    raise HTTPException(status_code=400, detail=f"STAGE 2 FAILED: Shot file '{upload.filename}' is empty")

                with open(temp_path, "wb") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())

                file_size = os.path.getsize(temp_path)
                if file_size == 0:
                    print(f"[STAGE 2: FAIL] Shot {i} written but size is 0 bytes on disk")
                    raise HTTPException(status_code=500, detail=f"STAGE 2 FAILED: Shot file '{upload.filename}' failed disk write verification")

                shot_paths.append(str(temp_path.absolute()))
                print(f"[STAGE 2] Shot {i}: {upload.filename} -> {file_size} bytes OK")

        print(f"[STAGE 2: OK] All files synced to disk ({(datetime.datetime.now() - stage_start).total_seconds():.3f}s)")

        print(f"[STAGE 2: OK] All files synced to disk ({(datetime.datetime.now() - stage_start).total_seconds():.3f}s)")

        # STAGE 3: RENDER EXECUTION
        stage_start = datetime.datetime.now()
        print(f"\n[{stage_start.strftime('%H:%M:%S.%f')[:-3]}] [STAGE 3: RENDER] Acquiring render lock and starting worker")

        # QUEUE MANAGEMENT: Wait for render lock (automatic queueing)
        async with render_lock:
            # Execute render in thread pool (non-blocking for FastAPI event loop)
            loop = asyncio.get_event_loop()

            try:
                output_mp3 = await loop.run_in_executor(
                    None,
                    worker_render.process_render_task,
                    task_id,
                    safe_username,
                    knob_value,
                    room_size,
                    bpm,
                    loop_paths,
                    shot_paths,
                    str(render_output_dir.absolute())
                )
            except Exception as worker_error:
                print(f"[STAGE 3: FAIL] Worker raised exception: {str(worker_error)}")
                raise HTTPException(status_code=500, detail=f"STAGE 3 FAILED: {str(worker_error)}")

        print(f"[STAGE 3: OK] Render worker completed ({(datetime.datetime.now() - stage_start).total_seconds():.3f}s)")

        # STAGE 4: OUTPUT VERIFICATION
        stage_start = datetime.datetime.now()
        print(f"\n[{stage_start.strftime('%H:%M:%S.%f')[:-3]}] [STAGE 4: OUTPUT VERIFY] Checking MP3 integrity")

        if not os.path.exists(output_mp3):
            print(f"[STAGE 4: FAIL] Output file does not exist: {output_mp3}")
            raise HTTPException(status_code=500, detail=f"STAGE 4 FAILED: Render output file not found at {output_mp3}")

        output_size = os.path.getsize(output_mp3)
        if output_size < 1000:
            print(f"[STAGE 4: FAIL] Output file suspiciously small: {output_size} bytes")
            raise HTTPException(status_code=500, detail=f"STAGE 4 FAILED: Output file is only {output_size} bytes (likely corrupt or silent)")

        print(f"[STAGE 4: OK] MP3 file verified: {output_size} bytes ({(datetime.datetime.now() - stage_start).total_seconds():.3f}s)")

        # Extract filename from full path
        filename = os.path.basename(output_mp3)

        print(f"\n{'='*60}")
        print(f"[SUCCESS] Render pipeline complete: {filename}")
        print(f"{'='*60}\n")

        # Schedule Telegram send in background
        caption = f"🎵 <b>FLOCKITY Render Complete</b>\n\n" \
                  f"👤 User: @{safe_username}\n" \
                  f"🎹 BPM: {bpm}\n" \
                  f"🎛 DRY/WET: {knob_value}\n" \
                  f"🏠 Room: {room_size.upper()}"

        background_tasks.add_task(send_audio_to_telegram, user_id, output_mp3, caption)

        return {
            "status": "success",
            "task_id": task_id,
            "filename": filename,
            "download_url": f"/api/download/{user_id}/{session_name}/{filename}"
        }

    except ValueError as e:
        # Validation error (invalid file format)
        print(f"\n[RENDER FAILED] ValueError: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Audio validation failed: {str(e)}")

    except HTTPException:
        # Re-raise HTTPException (already formatted with stage info)
        raise

    except Exception as e:
        print(f"\n[RENDER FAILED] Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Render failed with unexpected error: {str(e)}")


# ===== DOWNLOAD ENDPOINT =====
@app.get("/api/download/{user_id}/{session}/{filename}")
async def download_track(user_id: str, session: str, filename: str):
    """
    Download rendered MP3 file from user's session directory

    Args:
        user_id: Telegram user ID or 'local_user'
        session: render_TIMESTAMP
        filename: MP3 filename

    Returns:
        MP3 file as FileResponse
    """
    # Security: prevent path traversal
    safe_user_id = os.path.basename(user_id)
    safe_session = os.path.basename(session)
    safe_filename = os.path.basename(filename)

    mp3_path = USERS_DIR / safe_user_id / safe_session / "render_output" / safe_filename

    if not mp3_path.exists():
        raise HTTPException(status_code=404, detail="Render output not found")

    return FileResponse(
        mp3_path,
        media_type="audio/mpeg",
        filename=safe_filename
    )


# ===== HEALTH CHECK =====
@app.get("/health")
async def health_check():
    """Simple health check endpoint"""
    return {
        "status": "ok",
        "service": "flockity",
        "fl_studio": os.path.exists(worker_render.FL_EXE),
        "template": os.path.exists(worker_render.PROJECT_TEMPLATE)
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
