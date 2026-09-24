"""
Flockity Production Orchestrator
Manages FastAPI server, Cloudflare tunnel, and Telegram bot lifecycle
"""

import subprocess
import re
import sys
import os
import time
import signal
import psutil
from pathlib import Path

# ===== CONFIGURATION =====
BASE_DIR = Path(__file__).parent.absolute()
CLOUDFLARED_PATH = r"C:\cloudflared.exe"
TUNNEL_TIMEOUT = 30  # seconds to wait for tunnel URL
HEALTH_CHECK_INTERVAL = 5  # seconds between health checks

class ProcessManager:
    """Manages lifecycle of all child processes with graceful shutdown"""

    def __init__(self):
        self.processes = {}
        self.tunnel_url = None
        self.shutdown_requested = False

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle Ctrl+C and termination signals"""
        if not self.shutdown_requested:
            print("\n🛑 Shutdown signal received. Cleaning up...")
            self.shutdown_requested = True
            self.cleanup()
            sys.exit(0)

    def kill_orphans(self):
        """Kill any lingering processes from previous runs"""
        print("[CLEANUP] Scanning for orphan processes...")

        killed = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info['cmdline']
                if not cmdline:
                    continue

                cmdline_str = ' '.join(cmdline).lower()

                # Kill orphan app.py, bot.py processes
                if any(x in cmdline_str for x in ['app.py', 'bot.py', 'worker_render.py']):
                    if proc.info['pid'] != os.getpid():
                        print(f"[CLEANUP] Killing orphan Python process: PID {proc.info['pid']}")
                        proc.kill()
                        killed.append(proc.info['pid'])

                # Kill orphan cloudflared
                if 'cloudflared' in proc.info['name'].lower():
                    print(f"[CLEANUP] Killing orphan tunnel: PID {proc.info['pid']}")
                    proc.kill()
                    killed.append(proc.info['pid'])

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if killed:
            time.sleep(2)  # Wait for processes to die
            print(f"[CLEANUP] Killed {len(killed)} orphan process(es)")
        else:
            print("[CLEANUP] No orphans found")

    def start_backend(self):
        """Launch FastAPI server"""
        print("\n🚀 [STAGE 1] Starting FastAPI backend...")

        try:
            self.processes['app'] = subprocess.Popen(
                [sys.executable, str(BASE_DIR / "app.py")],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )

            # Wait for server to bind to port
            time.sleep(3)

            if self.processes['app'].poll() is not None:
                _, stderr = self.processes['app'].communicate()
                raise RuntimeError(f"Backend failed to start: {stderr}")

            print("✅ [STAGE 1] Backend running (PID: {})".format(
                self.processes['app'].pid
            ))

        except Exception as e:
            print(f"❌ [STAGE 1 FAILED] {e}")
            self.cleanup()
            sys.exit(1)

    def start_tunnel(self):
        """Launch Cloudflare tunnel and capture URL"""
        print("\n🌍 [STAGE 2] Starting Cloudflare tunnel...")

        if not os.path.exists(CLOUDFLARED_PATH):
            raise FileNotFoundError(
                f"Cloudflared not found at {CLOUDFLARED_PATH}\n"
                "Download: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation"
            )

        try:
            self.processes['tunnel'] = subprocess.Popen(
                [CLOUDFLARED_PATH, "tunnel", "--url", "http://localhost:8000"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )

            # Capture tunnel URL with timeout
            print("⏳ Waiting for tunnel URL...")
            start_time = time.time()

            for line in self.processes['tunnel'].stdout:
                print(f"[TUNNEL LOG] {line.strip()}")

                # Look for URL pattern
                match = re.search(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)', line)
                if match:
                    self.tunnel_url = match.group(1)
                    print(f"\n✅ [STAGE 2] Tunnel URL captured: {self.tunnel_url}\n")
                    break

                # Timeout check
                if time.time() - start_time > TUNNEL_TIMEOUT:
                    raise TimeoutError(f"Tunnel URL not captured within {TUNNEL_TIMEOUT}s")

                # Check if tunnel process died
                if self.processes['tunnel'].poll() is not None:
                    raise RuntimeError("Tunnel process died unexpectedly")

            if not self.tunnel_url:
                raise RuntimeError("Failed to capture tunnel URL")

        except Exception as e:
            print(f"❌ [STAGE 2 FAILED] {e}")
            self.cleanup()
            sys.exit(1)

    def start_bot(self):
        """Launch Telegram bot with tunnel URL"""
        print("\n🤖 [STAGE 3] Starting Telegram bot...")

        if not self.tunnel_url:
            raise RuntimeError("Cannot start bot without tunnel URL")

        # Prepare environment with tunnel URL
        env = os.environ.copy()
        env["WEBAPP_URL"] = self.tunnel_url

        try:
            self.processes['bot'] = subprocess.Popen(
                [sys.executable, str(BASE_DIR / "bot.py")],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )

            time.sleep(2)

            if self.processes['bot'].poll() is not None:
                _, stderr = self.processes['bot'].communicate()
                raise RuntimeError(f"Bot failed to start: {stderr}")

            print(f"✅ [STAGE 3] Bot running (PID: {self.processes['bot'].pid})")
            print(f"\n{'='*60}")
            print(f"🎉 ALL SYSTEMS OPERATIONAL")
            print(f"{'='*60}")
            print(f"📱 Web App URL: {self.tunnel_url}")
            print(f"🔧 Backend: http://localhost:8000")
            print(f"{'='*60}\n")

        except Exception as e:
            print(f"❌ [STAGE 3 FAILED] {e}")
            self.cleanup()
            sys.exit(1)

    def monitor(self):
        """Monitor all processes and restart if any crash"""
        print("👁️ Monitoring processes (Ctrl+C to stop)...\n")

        try:
            while not self.shutdown_requested:
                # Check each process
                for name, proc in list(self.processes.items()):
                    if proc.poll() is not None:
                        print(f"\n⚠️ Process '{name}' died unexpectedly (PID: {proc.pid})")
                        _, stderr = proc.communicate()
                        if stderr:
                            print(f"[ERROR OUTPUT] {stderr[:500]}")

                        # Critical processes trigger full shutdown
                        if name in ['app', 'tunnel']:
                            print("❌ Critical process failed. Shutting down...")
                            self.cleanup()
                            sys.exit(1)

                time.sleep(HEALTH_CHECK_INTERVAL)

        except KeyboardInterrupt:
            pass  # Handled by signal handler

    def cleanup(self):
        """Terminate all child processes gracefully"""
        if self.shutdown_requested:
            return

        self.shutdown_requested = True
        print("\n[SHUTDOWN] Terminating processes...")

        for name, proc in self.processes.items():
            if proc.poll() is None:  # Still running
                print(f"[SHUTDOWN] Stopping {name} (PID: {proc.pid})...")

                try:
                    # Try graceful termination first
                    proc.terminate()
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if doesn't respond
                    print(f"[SHUTDOWN] Force killing {name}...")
                    proc.kill()
                    proc.wait()

                print(f"[SHUTDOWN] {name} stopped")

        print("[SHUTDOWN] All processes terminated\n")


def main():
    """Main orchestration entry point"""
    print("\n" + "="*60)
    print("FLOCKITY PRODUCTION ORCHESTRATOR")
    print("="*60 + "\n")

    manager = ProcessManager()

    try:
        # Step 1: Clean environment
        manager.kill_orphans()

        # Step 2: Start backend
        manager.start_backend()

        # Step 3: Start tunnel and capture URL
        manager.start_tunnel()

        # Step 4: Start bot with tunnel URL
        manager.start_bot()

        # Step 5: Monitor all processes
        manager.monitor()

    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        manager.cleanup()
        sys.exit(1)

    finally:
        manager.cleanup()


if __name__ == "__main__":
    main()
