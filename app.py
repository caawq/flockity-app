import os
import uuid
import shutil
import asyncio
import subprocess
import time
import json
from pathlib import Path
from typing import List
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

render_lock = asyncio.Lock()


# --- 1. CREATING AND RESETTING A SESSION ---
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

        session_id = f"session_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        session_dir = Path("users") / str(user_id) / str(session_id)

        (session_dir / "source_loops").mkdir(parents=True, exist_ok=True)
        (session_dir / "source_shots").mkdir(parents=True, exist_ok=True)

        # Очищаем глобальные папки проекта
        for global_dir in [Path("session_loops"), Path("session_shots")]:
            if global_dir.exists():
                for f in global_dir.glob("*.*"):
                    try:
                        os.remove(f)
                    except Exception:
                        pass

        print("\n" + "=" * 60)
        print(f"[SESSION INIT] Fresh session: {session_id} for user: {user_id}")
        print("=" * 60 + "\n")
        return JSONResponse({"session_id": session_id, "status": "ok"})
    except Exception as e:
        print(f"[SESSION ERROR]: {e}")
        return JSONResponse(status_code=500, content={"detail": str(e)})


# --- 2. УДАЛЕНИЕ ФАЙЛА (ИЗ MANAGE) ---
@app.post("/api/delete")
async def delete_file_endpoint(request: Request):
    try:
        data = await request.json()
        user_id = data.get("user_id", "local_user")
        session_id = data.get("session_id")
        file_type = data.get("file_type", "")
        filename = data.get("filename")

        print("\n" + "-" * 60)
        print(f"[DIAGNOSTIC /api/delete] payload: {data}")

        if not session_id or not filename:
            raise HTTPException(status_code=400, detail="Missing session_id or filename")

        target_subfolder = "source_shots" if "shot" in str(file_type).lower() else "source_loops"
        target_path = Path("users") / str(user_id) / str(session_id) / target_subfolder / filename

        if target_path.exists():
            os.remove(target_path)
            print(f"[DELETE] [✓] File deleted from disk: {target_path}")
        else:
            print(f"[DELETE] [!] File NOT found on disk: {target_path}")
        print("-" * 60 + "\n")

        return JSONResponse({"status": "ok", "deleted": filename})
    except Exception as e:
        print(f"[DELETE ERROR]: {e}")
        return JSONResponse(status_code=500, content={"detail": str(e)})


# --- 3. UPLOADING SAMPLES WITH THE FULL LOGO ---
@app.post("/api/upload")
async def upload_endpoint(
        request: Request,
        files: List[UploadFile] = File(default=[])
):
    try:
        form = await request.form()

        print("\n" + "=" * 60)
        print("[DIAGNOSTIC /api/upload]")
        print(f"[*] Raw keys in form: {list(form.keys())}")
        print(f"[*] Form 'file_type': '{form.get('file_type')}'")
        print(f"[*] Form 'bay': '{form.get('bay')}'")
        print(f"[*] Form 'user_id': '{form.get('user_id')}'")
        print(f"[*] Form 'session_id': '{form.get('session_id')}'")

        user_id = form.get("user_id", "local_user")
        session_id = form.get("session_id", "default_session")
        file_type = str(form.get("file_type", "")).lower()

        # Проверка категории
        is_shot = "shot" in file_type or "shot" in str(form.get("bay", "")).lower()
        subfolder = "source_shots" if is_shot else "source_loops"

        print(f"[*] Resolution: is_shot={is_shot} -> target directory: '{subfolder}'")

        target_dir = Path("users") / str(user_id) / str(session_id) / subfolder
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
                print(f"[+] SAVED TO DISK: {save_path.resolve()}")

        print("=" * 60 + "\n")

        return JSONResponse({
            "status": "ok",
            "count": len(saved_files),
            "location": subfolder,
            "files": saved_files
        })
    except Exception as e:
        print(f"[UPLOAD ERROR]: {e}")
        return JSONResponse(status_code=500, content={"detail": str(e)})


# --- 4. RENDERING A TRACK WITH FOLDER AUDIT ---
@app.post("/api/render")
async def render_audio(request: Request):
    async with render_lock:
        try:
            form = await request.form()
            user_id = form.get("user_id", "local_user")
            session_id = form.get("session_id")

            if not session_id:
                raise HTTPException(status_code=400, detail="Missing session_id")

            render_id = f"render_{uuid.uuid4().hex[:8]}"
            session_base = Path("users") / str(user_id) / str(session_id)
            session_dir = session_base / render_id

            source_loops_dir = session_dir / "source_loops"
            source_shots_dir = session_dir / "source_shots"
            render_output_dir = session_dir / "rendered_output"

            for d in [source_loops_dir, source_shots_dir, render_output_dir]:
                d.mkdir(parents=True, exist_ok=True)

            parent_loops = session_base / "source_loops"
            parent_shots = session_base / "source_shots"

            if parent_loops.exists():
                for f in parent_loops.glob("*.*"):
                    shutil.copy(f, source_loops_dir / f.name)

            if parent_shots.exists():
                for f in parent_shots.glob("*.*"):
                    shutil.copy(f, source_shots_dir / f.name)

            active_loops = list(source_loops_dir.iterdir())
            active_shots = list(source_shots_dir.iterdir())

            print("\n" + "#" * 60)
            print(f"[DIAGNOSTIC /api/render DISPATCH]")
            print(f"[*] Session ID: {session_id}")
            print(f"[*] Active loops in staging ({len(active_loops)}): {[f.name for f in active_loops]}")
            print(f"[*] Active shots in staging ({len(active_shots)}): {[f.name for f in active_shots]}")
            print("#" * 60 + "\n")

            if not active_loops and not active_shots:
                raise HTTPException(status_code=400, detail="No active audio files in session.")

            worker_script = Path(__file__).parent / "worker_render.py"
            cmd = ["python", str(worker_script), str(user_id), str(session_id), render_id]
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

            process = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                print(f"Worker Error:\n{stderr.decode(errors='replace')}")
                raise HTTPException(status_code=500, detail="Render worker execution failed.")

            output_file = render_output_dir / "output.mp3"
            if not output_file.exists() or output_file.stat().st_size == 0:
                raise HTTPException(status_code=500, detail="Render completed but output.mp3 not found.")

            return FileResponse(
                path=output_file,
                media_type="audio/mpeg",
                filename="output.mp3"
            )

        except Exception as e:
            print(f"Render Error: {e}")
            raise HTTPException(status_code=500, detail=str(e))


app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)