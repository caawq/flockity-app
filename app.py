import os
import sys
import re
import uuid
import shutil
import asyncio
import subprocess
import time
import json
import urllib.request
from pathlib import Path
from typing import List
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI()

# Разрешаем фронтенду читать кастомные заголовки с именем файла
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Track-Filename"]
)

render_lock = asyncio.Lock()

BASE_DIR = Path(__file__).resolve().parent
GLOBAL_LOOPS_DIR = BASE_DIR / "session_loops"
GLOBAL_SHOTS_DIR = BASE_DIR / "session_shots"
TOKEN_FILE = BASE_DIR / "bot_token.txt"

GLOBAL_LOOPS_DIR.mkdir(parents=True, exist_ok=True)
GLOBAL_SHOTS_DIR.mkdir(parents=True, exist_ok=True)


def get_bot_token() -> str:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token and TOKEN_FILE.exists():
        try:
            token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return token


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", str(name)).strip()


# --- 1. SESSION MANAGEMENT ---
@app.post("/api/session/new")
@app.post("/api/session/reset")
async def create_or_reset_session(request: Request):
    try:
        user_id = None
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            try:
                body = await request.json()
                user_id = body.get("user_id")
            except Exception:
                pass
        else:
            try:
                form = await request.form()
                user_id = form.get("user_id")
            except Exception:
                pass

        if not user_id:
            user_id = request.query_params.get("user_id", "local_user")

        user_id = sanitize_filename(user_id) or "local_user"
        session_id = f"session_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        session_dir = BASE_DIR / "users" / str(user_id) / str(session_id)

        (session_dir / "source_loops").mkdir(parents=True, exist_ok=True)
        (session_dir / "source_shots").mkdir(parents=True, exist_ok=True)

        print(f"\n[SESSION INIT] User: {user_id} | Session: {session_id}\n")
        return JSONResponse({"session_id": session_id, "status": "ok"})
    except Exception as e:
        print(f"[SESSION ERROR]: {e}")
        return JSONResponse(status_code=500, content={"detail": str(e)})


# --- 2. FILE UPLOADS ---
@app.post("/api/upload")
async def upload_endpoint(
        request: Request,
        files: List[UploadFile] = File(default=[])
):
    try:
        form = await request.form()
        user_id = sanitize_filename(form.get("user_id", "local_user")) or "local_user"
        session_id = form.get("session_id", "default_session")
        file_type = str(form.get("file_type", "")).lower()

        is_shot = "shot" in file_type or "shot" in str(form.get("bay", "")).lower()
        subfolder = "source_shots" if is_shot else "source_loops"

        target_dir = BASE_DIR / "users" / str(user_id) / str(session_id) / subfolder
        target_dir.mkdir(parents=True, exist_ok=True)

        incoming = []
        if files:
            incoming.extend(files)
        for key, val in form.items():
            if hasattr(val, "filename") and val.filename and val not in incoming:
                incoming.append(val)

        saved_files = []
        for file in incoming:
            if file.filename:
                save_path = target_dir / file.filename
                with open(save_path, "wb") as f:
                    shutil.copyfileobj(file.file, f)
                saved_files.append(file.filename)

        print(f"[UPLOAD] Saved {len(saved_files)} files into {subfolder} for user {user_id}")

        return JSONResponse({
            "status": "ok",
            "count": len(saved_files),
            "location": subfolder,
            "files": saved_files
        })
    except Exception as e:
        print(f"[UPLOAD ERROR]: {e}")
        return JSONResponse(status_code=500, content={"detail": str(e)})


# --- 3. RENDER ENDPOINT ---
@app.post("/api/render")
async def render_audio(request: Request):
    async with render_lock:
        try:
            content_type = request.headers.get("content-type", "")
            payload = {}

            if "application/json" in content_type:
                try:
                    payload = await request.json()
                except Exception:
                    pass
            else:
                try:
                    form_data = await request.form()
                    payload = dict(form_data)
                except Exception:
                    pass

            raw_user = payload.get("user_id") or request.query_params.get("user_id", "local_user")
            chat_id = payload.get("chat_id") or request.query_params.get("chat_id")

            # Если user_id - это числовой ID Telegram, сохраняем его для отправки
            if str(raw_user).isdigit() and not chat_id:
                chat_id = str(raw_user)

            user_id = sanitize_filename(raw_user) or "local_user"
            session_id = payload.get("session_id") or request.query_params.get("session_id")

            if not session_id:
                raise HTTPException(status_code=400, detail="Missing session_id")

            room = payload.get("room") or request.query_params.get("room", "L")
            dry_wet = payload.get("dry_wet") or payload.get("flockity") or request.query_params.get("dry_wet", "100")
            bpm = payload.get("bpm") or request.query_params.get("bpm", "140")

            render_id = f"render_{uuid.uuid4().hex[:8]}"
            session_base = BASE_DIR / "users" / str(user_id) / str(session_id)
            session_dir = session_base / render_id

            source_loops_dir = session_dir / "source_loops"
            source_shots_dir = session_dir / "source_shots"
            render_output_dir = session_dir / "rendered_output"

            for d in [source_loops_dir, source_shots_dir, render_output_dir]:
                d.mkdir(parents=True, exist_ok=True)

            config_payload = {
                "user_id": str(user_id),
                "session_id": str(session_id),
                "render_id": str(render_id),
                "room": str(room),
                "dry_wet": str(dry_wet),
                "bpm": str(bpm)
            }
            with open(session_dir / "session_config.json", "w", encoding="utf-8") as f:
                json.dump(config_payload, f, indent=4)
            with open(session_base / "session_config.json", "w", encoding="utf-8") as f:
                json.dump(config_payload, f, indent=4)

            # Переносим файлы в папку рендера
            parent_loops = session_base / "source_loops"
            parent_shots = session_base / "source_shots"

            if parent_loops.exists():
                for f in parent_loops.glob("*.*"):
                    shutil.copy(f, source_loops_dir / f.name)

            if parent_shots.exists():
                for f in parent_shots.glob("*.*"):
                    shutil.copy(f, source_shots_dir / f.name)

            print(f"[*] Starting worker: user='{user_id}' | session='{session_id}'")

            worker_script = BASE_DIR / "worker_render.py"
            cmd = [
                sys.executable, str(worker_script),
                str(user_id), str(session_id), str(render_id),
                str(room), str(dry_wet), str(bpm)
            ]
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

            process = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                err_text = stderr.decode(errors="replace")
                print(f"[WORKER ERROR]:\n{err_text}")
                with open(session_dir / "worker_error.log", "w", encoding="utf-8") as ef:
                    ef.write(err_text)
                raise HTTPException(status_code=500, detail=f"Render worker failed: {err_text[-200:]}")

            mp3_files = list(render_output_dir.glob("*.mp3"))
            if not mp3_files or mp3_files[0].stat().st_size == 0:
                raise HTTPException(status_code=500, detail="Render completed but output MP3 not found.")

            output_file = mp3_files[0]
            real_filename = output_file.name

            # Паспорт для Telegram
            caption_text = f"🎧 <b>Track:</b> <code>{real_filename}</code>\n⚡ <b>BPM:</b> {bpm} | <b>Room:</b> {room} | <b>Dry/Wet:</b> {dry_wet}%"
            passport_file = session_dir / "render_info.txt"
            if passport_file.exists():
                try:
                    pcontent = passport_file.read_text(encoding="utf-8")
                    if "--- Detailed Sub-Parameters ---" in pcontent:
                        sub = pcontent.split("--- Detailed Sub-Parameters ---")[1].split("STATUS:")[0].strip()
                        caption_text += f"\n\n⚙️ <b>DSP Metrics:</b>\n<pre>{sub}</pre>"
                except Exception:
                    pass

            # Отправка в Telegram бот
            bot_token = get_bot_token()
            target_chat = chat_id or (user_id if str(user_id).isdigit() else None)

            if bot_token and target_chat:
                try:
                    telegram_url = f"https://api.telegram.org/bot{bot_token}/sendAudio"
                    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
                    body = bytearray()

                    body.extend(
                        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{target_chat}\r\n".encode(
                            "utf-8"))
                    body.extend(
                        f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nHTML\r\n".encode(
                            "utf-8"))
                    body.extend(
                        f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption_text}\r\n".encode(
                            "utf-8"))
                    body.extend(
                        f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"{real_filename}\"\r\nContent-Type: audio/mpeg\r\n\r\n".encode(
                            "utf-8"))

                    with open(output_file, "rb") as af:
                        body.extend(af.read())
                    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

                    req = urllib.request.Request(telegram_url, data=body)
                    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        print(f"[+] Telegram audio sent successfully to chat_id: {target_chat}")
                except Exception as tg_err:
                    print(f"[-] Telegram send error: {tg_err}")
            else:
                print(
                    f"[i] Skipped Telegram send: bot_token={'set' if bot_token else 'missing'}, target_chat={target_chat}")

            # Заголовки для правильного имени при скачивании
            headers = {
                "Content-Disposition": f'attachment; filename="{real_filename}"',
                "X-Track-Filename": real_filename
            }

            return FileResponse(
                path=output_file,
                media_type="audio/mpeg",
                filename=real_filename,
                headers=headers
            )

        except HTTPException:
            raise
        except Exception as e:
            print(f"[RENDER ERROR]: {e}")
            raise HTTPException(status_code=500, detail=str(e))


app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)