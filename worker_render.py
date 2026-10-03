import sys
import glob
import math
import os
import random
import subprocess
import time
import shutil
import ctypes
import mido
from mido import MidiFile, MidiTrack
import soundfile as sf
import pyautogui
import pygetwindow as gw
import pyperclip

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

if len(sys.argv) < 4:
    print("[-] Error: specify user_id, session_id и render_id.")
    sys.exit(1)

USER_ID = sys.argv[1]
SESSION_ID = sys.argv[2]
RENDER_ID = sys.argv[3]

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
FL_EXE = r"C:\Program Files\Image-Line\FL Studio 2026\FL64.exe"

PROJECT_TEMPLATE = os.path.join(BASE_DIR, "template.flp")
MIDI_POOL_DIR = os.path.join(BASE_DIR, "MIDI")

SESSION_DIR = os.path.join(BASE_DIR, "users", str(USER_ID), str(SESSION_ID), str(RENDER_ID))
SOURCE_LOOPS_DIR = os.path.join(SESSION_DIR, "source_loops")
SOURCE_SHOTS_DIR = os.path.join(SESSION_DIR, "source_shots")
OUTPUT_DIR = os.path.join(SESSION_DIR, "rendered_output")

ACTIVE_FLP = os.path.join(SESSION_DIR, "active_render.flp")
MASTER_MIDI = os.path.join(SESSION_DIR, "active_master.mid")

LOOPS_DIR = os.path.join(BASE_DIR, "session_loops")
SHOTS_DIR = os.path.join(BASE_DIR, "session_shots")

TOTAL_SLOTS = 15

for d in [SESSION_DIR, SOURCE_LOOPS_DIR, SOURCE_SHOTS_DIR, LOOPS_DIR, SHOTS_DIR, OUTPUT_DIR]:
    os.makedirs(d, exist_ok=True)

# Pinning a FL Studio Window
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
user32 = ctypes.windll.user32

def set_window_always_on_top(hwnd, enable=True):
    target = HWND_TOPMOST if enable else HWND_NOTOPMOST
    user32.SetWindowPos(hwnd, target, 0, 0, 0, 0, FLAGS)

def focus_and_pin_fl():
    time.sleep(1)
    windows = [w for w in gw.getAllWindows() if "FL Studio" in w.title]
    if not windows:
        print("[-] FL Studio window was not found!")
        return None

    fl_window = windows[0]
    print("[+] Closing the windows (Win+D)...")
    pyautogui.hotkey('win', 'd')
    time.sleep(0.5)

    try:
        fl_window.restore()
    except Exception:
        pass

    fl_window.activate()
    fl_window.maximize()
    time.sleep(0.5)

    center_x = fl_window.left + (fl_window.width // 2)
    center_y = fl_window.top + (fl_window.height // 2)
    pyautogui.click(center_x, center_y)
    time.sleep(0.5)

    hwnd = fl_window._hWnd
    set_window_always_on_top(hwnd, enable=True)
    return hwnd

def convert_mp3_to_wav_in_source(folder: str):
    if not os.path.exists(folder):
        return
    for mp3_path in glob.glob(os.path.join(folder, "*.mp3")):
        wav_path = mp3_path[:-4] + ".wav"
        if not os.path.exists(wav_path):
            shutil.copy(mp3_path, wav_path)

def sanitize_and_copy_sample(src_path: str, dst_path: str):
    try:
        data, samplerate = sf.read(src_path)
        sf.write(dst_path, data, samplerate, subtype="PCM_24")
    except Exception:
        shutil.copy(src_path, dst_path)

# --- SLOT PATCHING AND DIAGNOSTICS ---
def prepare_and_patch_flp(user_loops: list, user_shots: list):
    active_loops = min(len(user_loops), TOTAL_SLOTS)
    active_shots = min(len(user_shots), TOTAL_SLOTS)

    # Clearing global folders from previous runs
    for old_file in glob.glob(os.path.join(LOOPS_DIR, "*.*")):
        try:
            os.remove(old_file)
        except Exception:
            pass

    for old_file in glob.glob(os.path.join(SHOTS_DIR, "*.*")):
        try:
            os.remove(old_file)
        except Exception:
            pass

    for i in range(active_loops):
        dst = os.path.join(LOOPS_DIR, f"loop_{i + 1:02d}.wav")
        sanitize_and_copy_sample(user_loops[i], dst)

    for i in range(active_shots):
        dst = os.path.join(SHOTS_DIR, f"shot_{i + 1:02d}.wav")
        sanitize_and_copy_sample(user_shots[i], dst)

    # DIAGNOSTIC WORKER SLOT MAPPING
    print("\n" + "=" * 60)
    print("[DIAGNOSTIC WORKER SLOT MAPPING]")
    print(f"[*] Active loops: {active_loops} | Active shots: {active_shots}")
    print("[*] Content session_loops:")
    for f in os.listdir(LOOPS_DIR):
        print(f"    -> {f}")
    if not os.listdir(LOOPS_DIR):
        print("    -> [EMPTY]")

    print("[*] Content of session_shots:")
    for f in os.listdir(SHOTS_DIR):
        print(f"    -> {f}")
    if not os.listdir(SHOTS_DIR):
        print("    -> [EMPTY]")
    print("=" * 60 + "\n")

    if not os.path.exists(PROJECT_TEMPLATE):
        raise FileNotFoundError(f"[-] Pattern {PROJECT_TEMPLATE} not found!")

    with open(PROJECT_TEMPLATE, 'rb') as f:
        data = bytearray(f.read())

    def patch_slots(prefix, active_count):
        for i in range(1, TOTAL_SLOTS + 1):
            search_path = f"{prefix}_{i:02d}.wav".encode('utf-8')
            pos = 0
            while True:
                pos = data.find(search_path, pos)
                if pos == -1:
                    break
                enabled_pos = data.find(b'enabled="', pos)
                if enabled_pos != -1 and (enabled_pos - pos) < 250:
                    data[enabled_pos + 9] = ord('1') if i <= active_count else ord('0')
                pos += len(search_path)

    patch_slots("loop", active_loops)
    patch_slots("shot", active_shots)

    with open(ACTIVE_FLP, 'wb') as f:
        f.write(data)

    print(f"[+] Patched pattern: loop={active_loops}, shot={active_shots}")
    return ACTIVE_FLP

# --- MIDI Generator---
def loop_midi_to_16_bars(file_path: str, target_tpb: int = 480) -> list:
    mid = MidiFile(file_path)
    src_tpb = mid.ticks_per_beat
    ticks_per_bar = 4 * src_tpb

    raw_events = []
    for track in mid.tracks:
        curr_time = 0
        for msg in track:
            curr_time += msg.time
            if msg.type in ("note_on", "note_off"):
                raw_events.append((curr_time, msg.copy()))

    if not raw_events:
        return []
    raw_events.sort(key=lambda x: x[0])
    last_tick = raw_events[-1][0]

    measured_bars = max(1, math.ceil(last_tick / ticks_per_bar))
    pattern_bars = 2 if measured_bars <= 2 else 4 if measured_bars <= 4 else 8 if measured_bars <= 8 else 16

    pattern_ticks = pattern_bars * ticks_per_bar
    target_ticks = 16 * ticks_per_bar
    repeats_needed = math.ceil(target_ticks / pattern_ticks)

    scale = target_tpb / src_tpb
    target_scaled_ticks = int(16 * 4 * target_tpb)

    looped = []
    for rep in range(repeats_needed):
        offset = rep * pattern_ticks
        for ev_time, msg in raw_events:
            abs_time = offset + ev_time
            if abs_time < target_ticks:
                scaled_time = int(abs_time * scale)
                if scaled_time < target_scaled_ticks:
                    looped.append((scaled_time, msg))

    return looped

def build_master_midi():
    midi_pool = glob.glob(os.path.join(MIDI_POOL_DIR, "*.mid"))
    if not midi_pool:
        print(f"[-] No files in {MIDI_POOL_DIR}!")
        return

    chosen = random.sample(midi_pool, 4) if len(midi_pool) >= 4 else random.choices(midi_pool, k=4)
    master_midi = MidiFile(type=0, ticks_per_beat=480)
    tpb = master_midi.ticks_per_beat
    ticks_per_16 = 16 * 4 * tpb

    all_events = []
    for idx, mid_path in enumerate(chosen):
        section_offset = idx * ticks_per_16
        events = loop_midi_to_16_bars(mid_path, target_tpb=tpb)
        for ev_time, msg in events:
            all_events.append((section_offset + ev_time, msg))

    all_events.sort(key=lambda x: x[0])

    track = MidiTrack()
    last_time = 0
    for abs_time, msg in all_events:
        msg.time = max(0, abs_time - last_time)
        track.append(msg)
        last_time = abs_time

    master_midi.tracks.append(track)
    master_midi.save(MASTER_MIDI)
    print(f"[+] MIDI Generated: {MASTER_MIDI}")

# --- РОДНОЙ GUI МАКРОС FL STUDIO ---
def run_render_gui(output_file: str, flp_path: str, midi_path: str):
    print(f"\n[*] Launching FL Studio...")
    subprocess.Popen([FL_EXE, os.path.abspath(flp_path)])

    print("[*] Loading...")
    time.sleep(10)
    hwnd = focus_and_pin_fl()
    time.sleep(1)

    print("[*] Import MIDI...")
    pyautogui.press('f7')
    time.sleep(1.0)
    pyautogui.hotkey('ctrl', 'm')
    time.sleep(1.5)

    midi_abs_path = os.path.abspath(midi_path)
    pyperclip.copy(midi_abs_path)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.5)
    pyautogui.press('enter')
    time.sleep(1.5)
    pyautogui.press('enter')
    time.sleep(1)

    print("[*] Export MP3...")
    pyautogui.hotkey('ctrl', 'shift', 'r')
    time.sleep(1.5)

    output_abs_path = os.path.abspath(output_file)
    pyperclip.copy(output_abs_path)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.5)
    pyautogui.press('enter')
    time.sleep(1.5)
    pyautogui.press('enter')

    print("[*] Waiting for the finish...")
    time.sleep(15)

    if hwnd:
        set_window_always_on_top(hwnd, enable=False)

    print("[*] Closing FL Studio...")
    pyautogui.hotkey('alt', 'f4')
    time.sleep(1.5)
    pyautogui.press('n')

    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        print(f"\n[+] Profit! File: {output_file}")
    else:
        print("\n[-] Export error.")

if __name__ == "__main__":
    convert_mp3_to_wav_in_source(SOURCE_LOOPS_DIR)
    convert_mp3_to_wav_in_source(SOURCE_SHOTS_DIR)

    loops = glob.glob(os.path.join(SOURCE_LOOPS_DIR, "*.wav"))
    shots = glob.glob(os.path.join(SOURCE_SHOTS_DIR, "*.wav"))

    active_project = prepare_and_patch_flp(loops, shots)
    build_master_midi()

    out_path = os.path.join(OUTPUT_DIR, "output.mp3")
    if os.path.exists(out_path):
        os.remove(out_path)

    run_render_gui(out_path, active_project, MASTER_MIDI)