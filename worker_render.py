import sys
import glob
import math
import os
import json
import random
import subprocess
import time
import shutil
import ctypes
import struct
import numpy as np
import mido
from mido import MidiFile, MidiTrack
import soundfile as sf
import pyautogui
import pygetwindow as gw
import pyperclip

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

print(f"[DEBUG ARGS RECEIVED]: {sys.argv}")

if len(sys.argv) < 4:
    print("[-] Error: specify user_id, session_id and render_id.")
    sys.exit(1)

USER_ID = sys.argv[1]
SESSION_ID = sys.argv[2]
RENDER_ID = sys.argv[3]

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
FL_EXE = r"C:\Program Files\Image-Line\FL Studio 2026\FL64.exe"

PROJECT_TEMPLATE = os.path.join(BASE_DIR, "template.flp")
MIDI_POOL_DIR = os.path.join(BASE_DIR, "MIDI")

# Директории пользователя
SESSION_BASE = os.path.join(BASE_DIR, "users", str(USER_ID), str(SESSION_ID))
RENDER_DIR = os.path.join(SESSION_BASE, str(RENDER_ID))
OUTPUT_DIR = os.path.join(RENDER_DIR, "rendered_output")

# Глобальные папки слотов плагина
LOOPS_DIR = os.path.join(BASE_DIR, "session_loops")
SHOTS_DIR = os.path.join(BASE_DIR, "session_shots")

ACTIVE_FLP = os.path.join(RENDER_DIR, "active_render.flp")
MASTER_MIDI = os.path.join(RENDER_DIR, "active_master.mid")

TOTAL_SLOTS = 15
TEMPO_OFFSET = 125

for d in [RENDER_DIR, OUTPUT_DIR, LOOPS_DIR, SHOTS_DIR]:
    os.makedirs(d, exist_ok=True)


# --- 1. ПОИСК ИСХОДНЫХ СЭМПЛОВ ---
def find_source_files(subfolder_name: str) -> list:
    candidates = [
        os.path.join(SESSION_BASE, subfolder_name),
        os.path.join(RENDER_DIR, subfolder_name)
    ]
    found = []
    for c in candidates:
        if os.path.exists(c):
            for ext in ("*.wav", "*.WAV", "*.mp3", "*.MP3", "*.aif", "*.flac"):
                found.extend(glob.glob(os.path.join(c, ext)))
            if found:
                found = sorted(list(set(found)))
                print(f"[+] Found {len(found)} samples in {c} ({subfolder_name})")
                break
    return found


# --- 2. ПАРСИНГ ПАРАМЕТРОВ ---
cfg_candidates = [
    os.path.join(RENDER_DIR, "session_config.json"),
    os.path.join(SESSION_BASE, "session_config.json"),
]

config_data = {}
for cfg_path in cfg_candidates:
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
                break
        except Exception:
            pass

def get_arg_val(index: int, key: str, default: str) -> str:
    for k in [key, key.lower(), key.upper()]:
        if k in config_data:
            return str(config_data[k])
    for i, arg in enumerate(sys.argv):
        if arg == f"--{key}" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    if len(sys.argv) > index:
        val = sys.argv[index].strip().replace('"', '').replace("'", "")
        if val != "":
            return val
    return default

RAW_ROOM = get_arg_val(4, "room", "L")
RAW_DRY_WET = get_arg_val(5, "dry_wet", "100")
RAW_BPM = get_arg_val(6, "bpm", "140")

def parse_room_value(val: str) -> tuple[float, str]:
    clean = str(val).strip().upper().replace('"', '').replace("'", "")
    if clean.startswith("L") or clean == "LARGE":
        return 95.0, "L (Large)"
    if clean.startswith("S") or clean == "SMALL":
        return 20.0, "S (Small)"
    if clean.startswith("M") or clean == "MEDIUM":
        return 55.0, "M (Medium)"
    try:
        num = float(clean)
        if num >= 70: return num, f"L ({num:.0f}%)"
        elif num < 35: return num, f"S ({num:.0f}%)"
        else: return num, f"M ({num:.0f}%)"
    except ValueError:
        return 95.0, "L (Large)"

def parse_float_safe(val: str, default: float) -> float:
    try:
        return float(str(val).strip().replace('"', '').replace("'", ""))
    except (ValueError, TypeError):
        return default

ROOM_NUM, ROOM_LABEL = parse_room_value(RAW_ROOM)
DRY_WET_PARAM = max(0.0, min(100.0, parse_float_safe(RAW_DRY_WET, 100.0)))
BPM_PARAM = int(round(parse_float_safe(RAW_BPM, 140.0)))

print(f"[+] Active parsed params: Room={ROOM_LABEL}, Dry/Wet={DRY_WET_PARAM}%, BPM={BPM_PARAM}")


# --- 3. WIN32 ОКНА И РАСКЛАДКА ---
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
WM_INPUTLANGCHANGEREQUEST = 0x0050

user32 = ctypes.windll.user32

def set_window_always_on_top(hwnd, enable=True):
    target = HWND_TOPMOST if enable else HWND_NOTOPMOST
    user32.SetWindowPos(hwnd, target, 0, 0, 0, 0, FLAGS)

def set_english_layout(hwnd):
    user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, 0x0409)
    time.sleep(0.3)

def focus_and_pin_fl():
    time.sleep(1)
    windows = [w for w in gw.getAllWindows() if "FL Studio" in w.title]
    if not windows:
        print("[-] FL Studio window was not found!")
        return None

    fl_window = windows[0]
    pyautogui.hotkey('win', 'd')
    time.sleep(0.5)

    try:
        fl_window.restore()
    except Exception:
        pass

    fl_window.activate()
    fl_window.maximize()
    time.sleep(0.5)

    click_x = fl_window.left + (fl_window.width // 2)
    click_y = fl_window.top + int(fl_window.height * 0.4)
    pyautogui.moveTo(click_x, click_y)
    time.sleep(0.2)
    pyautogui.click()
    time.sleep(0.5)

    hwnd = fl_window._hWnd
    set_window_always_on_top(hwnd, enable=True)
    return hwnd


# --- 4. РАБОТА С АУДИО И DSP РАСЧЁТЫ ---
def sanitize_and_copy_sample(src_path: str, dst_path: str):
    """Конвертирует в 24-bit PCM WAV"""
    try:
        data, samplerate = sf.read(src_path)
        sf.write(dst_path, data, samplerate, subtype="PCM_24")
    except Exception as e:
        print(f"[!] Warning soundfile convert ({e}), direct copy...")
        shutil.copy(src_path, dst_path)

def ensure_all_15_slots_populated(target_dir: str, prefix: str, user_files: list):
    """Гарантирует физическое наличие prefix_01.wav .. prefix_15.wav во избежание Missing sample"""
    os.makedirs(target_dir, exist_ok=True)

    if not user_files:
        print(f"[!] Warning: No user files for {prefix}, generating fallback...")
        existing = glob.glob(os.path.join(target_dir, "*.wav"))
        if not existing:
            dummy_path = os.path.join(target_dir, f"{prefix}_dummy.wav")
            sf.write(dummy_path, np.zeros(4410, dtype=np.float32), 44100, subtype="PCM_24")
            user_files = [dummy_path]
        else:
            user_files = existing

    for i in range(1, TOTAL_SLOTS + 1):
        dst = os.path.join(target_dir, f"{prefix}_{i:02d}.wav")
        src = user_files[i - 1] if (i - 1) < len(user_files) else user_files[0]
        sanitize_and_copy_sample(src, dst)

    print(f"[+] All 15 {prefix} slots verified and populated.")

def calculate_sub_parameters(room_val: float, room_label: str, dry_wet: float, bpm: int):
    dw_ratio = dry_wet / 100.0
    base_intensity = 0.50 + (dw_ratio * 0.45)

    # Старший брат: BLOOM (botanical_mix)
    bloom_raw = base_intensity + random.uniform(-0.06, 0.04)
    if dry_wet >= 95.0:
        bloom_raw = max(0.92, bloom_raw)
    bloom_val = round(max(0.40, min(1.0, bloom_raw)), 2)

    # Младший брат: COLOR (stability) привязан к BLOOM
    color_ratio = random.uniform(0.75, 0.85)
    color_raw = (bloom_val * color_ratio) + random.uniform(-0.03, 0.03)
    color_val = round(max(0.15, min(bloom_val - 0.02, color_raw)), 2)

    # MUTATE
    mutate_raw = base_intensity + random.uniform(-0.05, 0.05)
    if dry_wet >= 95.0:
        mutate_raw = max(0.90, mutate_raw)
    mutate_val = round(max(0.40, min(1.0, mutate_raw)), 2)

    # SPREAD
    spread_raw = base_intensity + random.uniform(-0.04, 0.06)
    if dry_wet >= 95.0:
        spread_raw = max(0.88, spread_raw)
    spread_val = round(max(0.40, min(1.0, spread_raw)), 2)

    # TAIL (Reverb)
    room_ratio = room_val / 100.0
    tail_target = (room_ratio * 0.70) + (dw_ratio * 0.25) + random.uniform(-0.03, 0.03)
    tail_val = round(max(0.15, min(spread_val - 0.05, tail_target)), 2)
    if tail_val >= spread_val:
        tail_val = round(max(0.15, spread_val - 0.04), 2)

    envelope_decay = round(max(0.03, 0.12 - (dw_ratio * 0.08)), 4)

    dsp_params = {
        "botanical_mix": bloom_val,
        "mutation": mutate_val,
        "reverb_mix": tail_val,
        "stereo_width": spread_val,
        "stability": color_val,
        "envelope_decay": envelope_decay
    }

    display_metrics = {
        "BPM": bpm,
        "ROOM_DISPLAY": room_label,
        "DRY_WET": dry_wet,
        "MUTATE": int(round(mutate_val * 100)),
        "BLOOM": int(round(bloom_val * 100)),
        "SPREAD": int(round(spread_val * 100)),
        "TAIL": int(round(tail_val * 100)),
        "COLOR": int(round(color_val * 100))
    }

    return dsp_params, display_metrics

def write_render_info_passport(session_dir: str, metrics: dict, midis: list):
    info_path = os.path.join(session_dir, "render_info.txt")
    midi_lines = "\n".join([f"{i + 1}. {m}" for i, m in enumerate(midis)]) if midis else "No MIDI found"
    content = (
        "========================================\n"
        "FLOCKITY RENDER ENGINE - SESSION PASSPORT\n"
        "========================================\n"
        f"Global Tempo:     {metrics['BPM']} BPM\n"
        f"Dry/Wet Level:    {metrics['DRY_WET']}%\n"
        f"Room Size:        {metrics['ROOM_DISPLAY']}\n\n"
        "--- Source MIDI Files ---\n"
        f"{midi_lines}\n\n"
        "--- Detailed Sub-Parameters ---\n"
        f"MUTATE:           {metrics['MUTATE']}%\n"
        f"BLOOM:            {metrics['BLOOM']}%\n"
        f"COLOR (Junior):   {metrics['COLOR']}%\n"
        f"SPREAD:           {metrics['SPREAD']}%\n"
        f"TAIL:             {metrics['TAIL']}%\n"
        "STATUS:           Active session preserved\n"
        "========================================\n"
    )
    with open(info_path, "w", encoding="utf-8") as f:
        f.write(content)

def patch_project_tempo(data: bytearray, bpm: int):
    tempo_val = int(bpm * 1000)
    tempo_bytes = struct.pack("<I", tempo_val)
    data[TEMPO_OFFSET:TEMPO_OFFSET + 4] = tempo_bytes
    print(f"[+] Patched static FLP Tempo: {bpm} BPM ({tempo_val}) at offset {TEMPO_OFFSET}")

def prepare_and_patch_flp(user_loops: list, user_shots: list, chosen_midis: list):
    # Физически заполняем файлы на диске, чтобы VINE не ругался на missing sample
    ensure_all_15_slots_populated(LOOPS_DIR, "loop", user_loops)
    ensure_all_15_slots_populated(SHOTS_DIR, "shot", user_shots)

    # Активными делаем СТРОГО то количество, которое РЕАЛЬНО загрузил пользователь
    active_loops = min(len(user_loops), TOTAL_SLOTS)
    active_shots = min(len(user_shots), TOTAL_SLOTS)

    if not os.path.exists(PROJECT_TEMPLATE):
        raise FileNotFoundError(f"[-] Template {PROJECT_TEMPLATE} not found!")

    with open(PROJECT_TEMPLATE, 'rb') as f:
        data = bytearray(f.read())

    def patch_slots(prefix, active_count):
        for i in range(1, TOTAL_SLOTS + 1):
            search_path = f"{prefix}_{i:02d}.wav".encode('utf-8')
            pos = 0
            while True:
                pos = data.find(search_path, pos)
                if pos == -1: break
                enabled_pos = data.find(b'enabled="', pos)
                if enabled_pos != -1 and (enabled_pos - pos) < 250:
                    # Если active_count == 0, абсолютно все слоты получат ord('0') (OFF)
                    data[enabled_pos + 9] = ord('1') if i <= active_count else ord('0')
                pos += len(search_path)

    patch_slots("loop", active_loops)
    patch_slots("shot", active_shots)
    patch_project_tempo(data, BPM_PARAM)

    dsp_params, display_metrics = calculate_sub_parameters(
        ROOM_NUM, ROOM_LABEL, DRY_WET_PARAM, BPM_PARAM
    )
    write_render_info_passport(RENDER_DIR, display_metrics, chosen_midis)

    def patch_dsp_param_exact_bytes(param_id: str, new_value: float):
        search_key = f'<PARAM id="{param_id}" value="'.encode('utf-8')
        pos = data.find(search_key)
        if pos == -1: return

        val_start = pos + len(search_key)
        val_end = data.find(b'"', val_start)
        if val_end == -1: return

        target_len = val_end - val_start
        val_str = f"{new_value:.18f}"
        if len(val_str) > target_len:
            val_str = val_str[:target_len]
        else:
            val_str = val_str.ljust(target_len, "0")

        data[val_start:val_end] = val_str.encode('utf-8')

    for pid, pval in dsp_params.items():
        patch_dsp_param_exact_bytes(pid, pval)

    with open(ACTIVE_FLP, 'wb') as f:
        f.write(data)

    print(f"[+] Patched project template: loop={active_loops}, shot={active_shots}")
    return ACTIVE_FLP

# --- 5. MIDI GENERATOR ---
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

    if not raw_events: return []
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

def build_master_midi() -> list:
    midi_pool = glob.glob(os.path.join(MIDI_POOL_DIR, "*.mid"))
    if not midi_pool:
        print(f"[-] No files in {MIDI_POOL_DIR}!")
        return []

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
    return [os.path.basename(m) for m in chosen]


# --- 6. ЭМУЛЯЦИЯ И РЕНДЕР В FL STUDIO ---
def hardcore_hotkey(*keys):
    for k in keys[:-1]:
        pyautogui.keyDown(k)
        time.sleep(0.15)
    pyautogui.keyDown(keys[-1])
    time.sleep(0.15)
    pyautogui.keyUp(keys[-1])
    for k in reversed(keys[:-1]):
        pyautogui.keyUp(k)
        time.sleep(0.15)

def run_render_gui(output_file: str, flp_path: str, midi_path: str):
    print("\n[*] Launching FL Studio...")
    subprocess.Popen([FL_EXE, os.path.abspath(flp_path)])

    print("[*] Loading workspace (waiting 8s)...")
    time.sleep(8)
    hwnd = focus_and_pin_fl()
    time.sleep(1)

    if hwnd:
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        set_english_layout(hwnd)
        time.sleep(0.5)

    print("[*] Opening Piano Roll & Importing MIDI...")
    pyautogui.press('f7')
    time.sleep(0.8)

    hardcore_hotkey('ctrl', 'm')
    time.sleep(1.5)

    midi_abs_path = os.path.abspath(midi_path)
    pyperclip.copy(midi_abs_path)
    time.sleep(0.2)
    hardcore_hotkey('ctrl', 'v')
    time.sleep(0.5)
    pyautogui.press('enter')
    time.sleep(1.5)
    pyautogui.press('enter')
    time.sleep(1.0)

    print("[*] Exporting MP3 stream...")
    hardcore_hotkey('ctrl', 'shift', 'r')
    time.sleep(2.0)

    output_abs_path = os.path.abspath(output_file)
    pyperclip.copy(output_abs_path)
    time.sleep(0.2)
    hardcore_hotkey('ctrl', 'v')
    time.sleep(0.5)
    pyautogui.press('enter')
    time.sleep(1.5)
    pyautogui.press('enter')

    print("[*] Rendering audio stream...")
    time.sleep(8)

    if hwnd:
        set_window_always_on_top(hwnd, enable=False)

    print("[*] Terminating FL Studio session...")
    hardcore_hotkey('alt', 'f4')
    time.sleep(1.5)
    pyautogui.press('n')

    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        print(f"\n[+] Render completed successfully: {output_file}")
    else:
        print("\n[-] Export failed.")


if __name__ == "__main__":
    loops = find_source_files("source_loops")
    shots = find_source_files("source_shots")

    chosen_midis = build_master_midi()
    active_project = prepare_and_patch_flp(loops, shots, chosen_midis)

    for old_out in glob.glob(os.path.join(OUTPUT_DIR, "*.mp3")):
        try: os.remove(old_out)
        except Exception: pass

    safe_user = str(USER_ID) if str(USER_ID).startswith("@") else f"@{USER_ID}"
    rand_id = random.randint(1000, 9999)
    final_filename = f"{safe_user}_{BPM_PARAM}bpm_{rand_id}_{int(DRY_WET_PARAM)}flockity.mp3"
    out_path = os.path.join(OUTPUT_DIR, final_filename)

    run_render_gui(out_path, active_project, MASTER_MIDI)