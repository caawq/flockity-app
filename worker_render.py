"""
Flockity Audio Processing Core
Handles MIDI generation, VINE XML configuration, and FL Studio CLI rendering
"""

import os
import random
import subprocess
import xml.etree.ElementTree as ET
import soundfile as sf
from pathlib import Path

# ===== CONFIGURATION =====
BASE_DIR = r"C:\Users\SELIK\Desktop\flockity-app"
FL_EXE = r"C:\Program Files\Image-Line\FL Studio 2026\FL64.exe"

PROJECT_TEMPLATE = os.path.join(BASE_DIR, "template.flp")
VINE_CONFIG_PATH = os.path.join(BASE_DIR, "vine_config.xml")

# STATIC LIBRARIES (DO NOT MODIFY)
MIDI_DIR = os.path.join(BASE_DIR, "MIDI")  # Static MIDI pattern library
OSLPS_DIR = os.path.join(BASE_DIR, "OSLPS")  # Static sound library

# SESSION DIRECTORIES
SESSION_LOOPS_DIR = os.path.join(BASE_DIR, "session_loops")
SESSION_SHOTS_DIR = os.path.join(BASE_DIR, "session_shots")
SESSION_MIDI_DIR = os.path.join(BASE_DIR, "session_midi")
TASKS_DIR = os.path.join(BASE_DIR, "tasks")  # Temporary task storage

# UNIFIED OUTPUT DIRECTORY
OUTPUT_DIR = os.path.join(BASE_DIR, "rendered_output")

# Ensure directories exist
for d in [SESSION_LOOPS_DIR, SESSION_SHOTS_DIR, SESSION_MIDI_DIR, TASKS_DIR, OUTPUT_DIR]:
    os.makedirs(d, exist_ok=True)

# MIDI CC mappings for VINE plugin (0-100 range from UI)
CC_MUTATE = 10
CC_BLOOM = 11
CC_COLOR = 12
CC_SPREAD = 13
CC_TAIL = 16

ALLOWED_EXTENSIONS = {'.mp3', '.wav'}

# ROOM SIZE MAPPING (converts string to intensity multiplier)
ROOM_SIZE_MAP = {
    "small": 0.3,   # Weak reverb/stereo
    "middle": 0.6,  # Medium reverb/stereo
    "large": 0.9    # Strong reverb/stereo
}


# ===== VALIDATION =====
def validate_audio_file(file_path: str) -> bool:
    """
    Validate that uploaded file is MP3 or WAV (security check)
    Returns True if valid, False otherwise
    """
    ext = Path(file_path).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False

    # Additional check: try to read file header with soundfile
    try:
        info = sf.info(file_path)
        return info.samplerate > 0 and info.frames > 0
    except Exception:
        return False


# ===== AUDIO SANITIZATION =====
def sanitize_audio(src: str, dst: str):
    """
    Convert any audio format to 24-bit PCM WAV
    Destroys potential exploits in metadata
    """
    if not validate_audio_file(src):
        raise ValueError(f"Invalid audio file: {src}. Only MP3 and WAV are allowed.")

    data, sr = sf.read(src)
    sf.write(dst, data, sr, subtype='PCM_24')


# ===== VINE XML CONFIGURATION =====
def update_vine_xml_config(uploaded_loops: list, uploaded_shots: list):
    """
    Generate VINE plugin XML configuration:
    - 15 Loop slots (kind="1")
    - 15 One-Shot slots (kind="0")
    - enabled="1" for loaded files, enabled="0" for empty slots

    Args:
        uploaded_loops: list of sanitized WAV paths (max 15)
        uploaded_shots: list of sanitized WAV paths (max 15)
    """
    root = ET.Element("SAMPLES")

    # LOOPS (kind="1")
    for i in range(15):
        sample = ET.SubElement(root, "SAMPLE")
        sample.set("slot", str(i))
        sample.set("kind", "1")

        if i < len(uploaded_loops):
            sample.set("path", uploaded_loops[i])
            sample.set("enabled", "1")
        else:
            sample.set("path", "")
            sample.set("enabled", "0")

    # ONE-SHOTS (kind="0")
    for i in range(15):
        sample = ET.SubElement(root, "SAMPLE")
        sample.set("slot", str(i + 15))
        sample.set("kind", "0")

        if i < len(uploaded_shots):
            sample.set("path", uploaded_shots[i])
            sample.set("enabled", "1")
        else:
            sample.set("path", "")
            sample.set("enabled", "0")

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(VINE_CONFIG_PATH, encoding="utf-8", xml_declaration=True)


# ===== MIDI GENERATION WITH BPM AND ROOM SIZE =====
def generate_master_midi(dry_wet: int, room_size: str, bpm: int):
    """
    Generate 64-bar MIDI file with Beta-distributed CC automation

    Structure:
    - BPM tempo message at start
    - 4 sections × 16 bars each
    - CC values generated at section starts using Beta distribution
    - DRY/WET controls: Mutate (CC10), Bloom (CC11), Color (CC12)
    - ROOM SIZE controls: Spread (CC13), Tail (CC16)

    Args:
        dry_wet: DRY/WET knob position (0-100)
        room_size: Room intensity ("small", "middle", "large")
        bpm: Tempo in beats per minute (60-200)
    """
    try:
        import mido
        from mido import Message, MetaMessage, MidiFile, MidiTrack
    except ImportError:
        raise RuntimeError("mido library not installed. Run: pip install mido --break-system-packages")

    # Convert room_size string to intensity multiplier
    room_intensity = ROOM_SIZE_MAP.get(room_size, 0.6)  # Default to "middle" if invalid

    mid = MidiFile()
    track = MidiTrack()
    mid.tracks.append(track)

    # Set tempo at start (microseconds per beat)
    tempo = mido.bpm2tempo(bpm)
    track.append(MetaMessage('set_tempo', tempo=tempo, time=0))

    # 4 sections, 16 bars each, 4 beats per bar
    bars_per_section = 16
    beats_per_bar = 4
    ticks_per_beat = mid.ticks_per_beat

    for section in range(4):
        bar_offset = section * bars_per_section
        time_ticks = bar_offset * beats_per_bar * ticks_per_beat

        # Normalize to 0-1 range
        dw_norm = dry_wet / 100.0

        # Beta distribution for organic variation
        # DRY/WET parameters (controls Mutate, Bloom, Color)
        alpha_dw = 1.5 + dw_norm * 3.5
        beta_dw = 5.0 - dw_norm * 3.5

        mutate_val = int(random.betavariate(alpha_dw, beta_dw) * 127)
        bloom_val = int(random.betavariate(alpha_dw, beta_dw) * 127)
        color_val = int(random.betavariate(alpha_dw, beta_dw) * 127)

        # ROOM parameters (controls Spread and Tail)
        # Use room_intensity as base, add organic variation via Beta
        alpha_room = 1.5 + room_intensity * 3.5
        beta_room = 5.0 - room_intensity * 3.5

        spread_val = int(random.betavariate(alpha_room, beta_room) * 127)
        tail_val = int(random.betavariate(alpha_room, beta_room) * 127)

        # Write CC messages (delta time from previous event)
        if section == 0:
            track.append(Message('control_change', channel=0, control=CC_MUTATE, value=mutate_val, time=time_ticks))
            track.append(Message('control_change', channel=0, control=CC_BLOOM, value=bloom_val, time=0))
            track.append(Message('control_change', channel=0, control=CC_COLOR, value=color_val, time=0))
            track.append(Message('control_change', channel=0, control=CC_SPREAD, value=spread_val, time=0))
            track.append(Message('control_change', channel=0, control=CC_TAIL, value=tail_val, time=0))
        else:
            # Delta time from last event
            prev_time = (section - 1) * bars_per_section * beats_per_bar * ticks_per_beat
            delta = time_ticks - prev_time
            track.append(Message('control_change', channel=0, control=CC_MUTATE, value=mutate_val, time=delta))
            track.append(Message('control_change', channel=0, control=CC_BLOOM, value=bloom_val, time=0))
            track.append(Message('control_change', channel=0, control=CC_COLOR, value=color_val, time=0))
            track.append(Message('control_change', channel=0, control=CC_SPREAD, value=spread_val, time=0))
            track.append(Message('control_change', channel=0, control=CC_TAIL, value=tail_val, time=0))

    output_path = os.path.join(SESSION_MIDI_DIR, "master_automation.mid")
    mid.save(output_path)

    return output_path


# ===== FL STUDIO CLI RENDERING =====
def execute_fl_render(out_path: str):
    """
    Execute FL Studio headless render via CLI

    CRITICAL: /R flag syntax must be WITHOUT SPACE: /R"path"
    Now renders to MP3 format instead of WAV

    Args:
        out_path: Absolute path where FL Studio will save rendered MP3
    """
    import datetime

    abs_out_path = os.path.abspath(out_path)
    abs_template = os.path.abspath(PROJECT_TEMPLATE)

    # Pre-flight validation
    if not os.path.exists(abs_template):
        raise RuntimeError(f"FL Studio template not found: {abs_template}")

    if not os.path.exists(FL_EXE):
        raise RuntimeError(f"FL Studio executable not found: {FL_EXE}")

    cmd = [
        FL_EXE,
        f'/R{abs_out_path}',  # NO SPACE between /R and path
        "/Emp3",  # Export as MP3
        abs_template
    ]

    stage_start = datetime.datetime.now()
    print(f"\n[{stage_start.strftime('%H:%M:%S.%f')[:-3]}] [FL STUDIO] Starting CLI render")
    print(f"[FL STUDIO] Command: {' '.join(cmd)}")
    print(f"[FL STUDIO] Output path: {abs_out_path}")
    print(f"[FL STUDIO] Template: {abs_template}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    render_duration = (datetime.datetime.now() - stage_start).total_seconds()

    if result.returncode != 0:
        print(f"[FL STUDIO: FAIL] Process exited with code {result.returncode}")
        print(f"[FL STUDIO: FAIL] STDOUT: {result.stdout[-500:]}")
        print(f"[FL STUDIO: FAIL] STDERR: {result.stderr[-500:]}")
        raise RuntimeError(f"FL Studio CLI exited with code {result.returncode}: {result.stderr[-200:]}")

    if not os.path.exists(abs_out_path):
        print(f"[FL STUDIO: FAIL] Output file not created: {abs_out_path}")
        raise RuntimeError(f"FL Studio did not create output file: {abs_out_path}")

    file_size = os.path.getsize(abs_out_path)

    if file_size < 1000:
        print(f"[FL STUDIO: FAIL] Output file too small: {file_size} bytes (likely silent or corrupt)")
        raise RuntimeError(f"FL Studio produced suspiciously small file ({file_size} bytes)")

    print(f"[FL STUDIO: OK] Render complete: {file_size} bytes in {render_duration:.2f}s")


# ===== ORCHESTRATION =====
def process_render_task(
    task_id: str,
    username: str,
    knob_value: int,
    room_size: str,
    bpm: int,
    uploaded_loop_paths: list,
    uploaded_shot_paths: list,
    render_output_dir: str
):
    """
    Full render pipeline orchestration:
    1. Validate and sanitize uploaded audio files (in-place in session directories)
    2. Update VINE XML configuration
    3. Generate MIDI automation with BPM and room size
    4. Execute FL Studio CLI render to MP3
    5. Return path to rendered MP3 file

    Args:
        task_id: Unique task identifier
        username: Telegram username (or 'guest')
        knob_value: DRY/WET knob position (0-100)
        room_size: Room intensity ("small", "middle", "large")
        bpm: Tempo (60-200)
        uploaded_loop_paths: List of uploaded loop file paths (already in session_loops/)
        uploaded_shot_paths: List of uploaded one-shot file paths (already in session_shots/)
        render_output_dir: Path to session-specific render_output directory

    Returns:
        Path to rendered MP3 file in render_output_dir
    """
    # Convert all paths to absolute
    render_output_dir = os.path.abspath(render_output_dir)
    uploaded_loop_paths = [os.path.abspath(p) for p in uploaded_loop_paths]
    uploaded_shot_paths = [os.path.abspath(p) for p in uploaded_shot_paths]

    # Temporary task directory for FL Studio intermediate files
    task_dir = os.path.join(TASKS_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    print(f"\n[RENDER START] Task {task_id}")
    print(f"[PARAMS] Username: {username}, BPM: {bpm}, DRY/WET: {knob_value}, Room: {room_size}")
    print(f"[OUTPUT] Target directory: {render_output_dir}")

    try:
        # 1. Verify uploaded files and sanitize audio files in-place
        print(f"\n[WORKER] Step 1: Audio file sanitization")
        sanitized_loops = []
        for i, src in enumerate(uploaded_loop_paths):
            if not os.path.exists(src):
                raise RuntimeError(f"Loop file {i} does not exist: {src}")

            file_size = os.path.getsize(src)
            if file_size == 0:
                raise RuntimeError(f"Loop file {i} is empty (0 bytes): {src}")

            print(f"[WORKER] Loop {i}: {file_size} bytes")

            # Create sanitized version in same directory
            src_path = Path(src)
            dst = src_path.parent / f"sanitized_{src_path.name}"

            try:
                sanitize_audio(src, str(dst))
            except Exception as e:
                raise RuntimeError(f"Failed to sanitize loop {i}: {str(e)}")

            if not os.path.exists(dst) or os.path.getsize(dst) == 0:
                raise RuntimeError(f"Sanitized loop {i} is invalid or empty")

            sanitized_loops.append(str(dst.absolute()))
            print(f"[WORKER] Loop {i} sanitized: {os.path.getsize(dst)} bytes")

        sanitized_shots = []
        for i, src in enumerate(uploaded_shot_paths):
            if not os.path.exists(src):
                raise RuntimeError(f"Shot file {i} does not exist: {src}")

            file_size = os.path.getsize(src)
            if file_size == 0:
                raise RuntimeError(f"Shot file {i} is empty (0 bytes): {src}")

            print(f"[WORKER] Shot {i}: {file_size} bytes")

            # Create sanitized version in same directory
            src_path = Path(src)
            dst = src_path.parent / f"sanitized_{src_path.name}"

            try:
                sanitize_audio(src, str(dst))
            except Exception as e:
                raise RuntimeError(f"Failed to sanitize shot {i}: {str(e)}")

            if not os.path.exists(dst) or os.path.getsize(dst) == 0:
                raise RuntimeError(f"Sanitized shot {i} is invalid or empty")

            sanitized_shots.append(str(dst.absolute()))
            print(f"[WORKER] Shot {i} sanitized: {os.path.getsize(dst)} bytes")

        # 2. Update VINE config
        print(f"\n[WORKER] Step 2: VINE XML configuration")
        update_vine_xml_config(sanitized_loops, sanitized_shots)
        print(f"[WORKER] VINE config updated: {len(sanitized_loops)} loops, {len(sanitized_shots)} shots")

        # 3. Generate MIDI with BPM and room size
        print(f"\n[WORKER] Step 3: MIDI automation generation")
        midi_path = generate_master_midi(knob_value, room_size, bpm)
        if not os.path.exists(midi_path):
            raise RuntimeError(f"MIDI file not created: {midi_path}")
        print(f"[WORKER] MIDI generated: {midi_path}")

        # 4. Render in FL Studio directly to session render_output directory
        print(f"\n[WORKER] Step 4: FL Studio render execution")
        random_int = random.randint(1000, 10000)
        final_filename = f"@{username}_{bpm}_{knob_value}_{random_int}_flockityapp.mp3"
        final_output = os.path.join(render_output_dir, final_filename)

        print(f"[WORKER] Target output: {final_filename}")
        execute_fl_render(final_output)

        # 5. Final verification
        print(f"\n[WORKER] Step 5: Final output verification")
        if not os.path.exists(final_output):
            raise RuntimeError(f"Final output missing after render: {final_output}")

        final_size = os.path.getsize(final_output)
        if final_size < 1000:
            raise RuntimeError(f"Final output too small ({final_size} bytes), likely corrupt")

        print(f"[WORKER] Final MP3: {final_size} bytes")
        print(f"[WORKER] Render task {task_id} completed successfully\n")

        return final_output

    finally:
        # Cleanup temporary task directory
        import shutil
        if os.path.exists(task_dir):
            shutil.rmtree(task_dir, ignore_errors=True)
