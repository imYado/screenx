"""Desk Deck - PC helper server.

Serves the phone web app and gives it:
  * now playing info + play/pause/next/prev (Windows media controls)
  * a button pad whose actions are defined in config.json

Run:  python server.py
"""

import asyncio
import ctypes
import hmac
import json
import mimetypes
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
CONFIG_PATH = ROOT / "config.json"

DEFAULT_BUTTONS = [
    # App grid. "logo" = where to pull the real app icon from (see icons.py).
    {"id": "spotify", "label": "Spotify", "logo": "appx:SpotifyAB.SpotifyMusic",
     "action": {"type": "open", "target": "spotify:"}},
    {"id": "chrome", "label": "Chrome", "logo": "exe:chrome.exe",
     "action": {"type": "open", "target": "chrome"}},
    {"id": "claude", "label": "Claude", "logo": "appx:Claude",
     "action": {"type": "open", "target": "shell:AppsFolder\\Claude_pzs8sxrjxfjjc!Claude"}},
    {"id": "steam", "label": "Steam", "logo": "exe:steam",
     "action": {"type": "open", "target": "steam://open/main"}},
    {"id": "files", "label": "Files", "logo": "exe:explorer.exe",
     "action": {"type": "open", "target": "explorer"}},
    # Tap to start/stop streaming PC audio to your phone; shows live status under the icon.
    {"id": "audiocast", "label": "AudioCast", "logo": "appx:sskplay.AudioCast", "status": "audiocast",
     "action": {"type": "audiocast", "do": "toggle"}},
    # Side bar. "glyph" = built-in symbol: vol_up, vol_down, mute, play_pause, next, prev, moon
    {"id": "vol_up", "label": "Volume Up", "place": "sidebar", "glyph": "vol_up",
     "action": {"type": "media", "key": "volume_up"}},
    {"id": "vol_down", "label": "Volume Down", "place": "sidebar", "glyph": "vol_down",
     "action": {"type": "media", "key": "volume_down"}},
]


# ---------------------------------------------------------------- config

def load_config():
    if not CONFIG_PATH.exists():
        cfg = {
            "port": 8765,
            "pin": f"{secrets.randbelow(10**6):06d}",
            "buttons": DEFAULT_BUTTONS,
        }
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        return cfg
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


CONFIG = load_config()

# ---------------------------------------------------------------- logos

from icons import load_logo  # noqa: E402

LOGOS = {}


def load_logos():
    for b in CONFIG["buttons"]:
        if b.get("logo"):
            r = load_logo(b["logo"])
            if r:
                LOGOS[b["id"]] = r
            else:
                print(f"[logo] no logo found for '{b['id']}' ({b['logo']}); showing its label instead")



# ---------------------------------------------------------------- keyboard

user32 = ctypes.WinDLL("user32", use_last_error=True)
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

VK = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
    "esc": 0x1B, "escape": 0x1B, "enter": 0x0D, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "printscreen": 0x2C, "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "next": 0xB0, "prev": 0xB1, "stop": 0xB2, "play_pause": 0xB3,
}
for i in range(1, 25):
    VK[f"f{i}"] = 0x6F + i
EXTENDED = {0x5B, 0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27,
            0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}

MEDIA_KEYS = {"volume_up": "volume_up", "volume_down": "volume_down", "mute": "volume_mute",
              "play_pause": "play_pause", "next": "next", "prev": "prev", "stop": "stop"}


def vk_for(name):
    name = name.strip().lower()
    if name in VK:
        return VK[name]
    if len(name) == 1:
        return user32.VkKeyScanW(ord(name)) & 0xFF
    raise ValueError(f"Unknown key: {name}")


def _key(vk, up=False):
    flags = (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0)
    user32.keybd_event(vk, 0, flags, 0)


def press_combo(combo):
    vks = [vk_for(k) for k in combo.split("+")]
    for vk in vks:
        _key(vk)
        time.sleep(0.01)
    for vk in reversed(vks):
        _key(vk, up=True)
        time.sleep(0.01)


# ---------------------------------------------------------------- media (Windows SMTC)

KNOWN_APPS = {"spotify": "Spotify", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox",
              "zunemusic": "Media Player", "vlc": "VLC", "itunes": "iTunes", "applemusic": "Apple Music",
              "discord": "Discord", "brave": "Brave", "opera": "Opera", "tidal": "TIDAL", "deezer": "Deezer"}


def friendly_app(app_id):
    low = (app_id or "").lower()
    for k, v in KNOWN_APPS.items():
        if k in low:
            return v
    name = (app_id or "").split("!")[0].split("\\")[-1].split("_")[0].replace(".exe", "")
    return name.split(".")[-1]


class Media:
    """Polls the Windows 'now playing' session in a background asyncio loop."""

    def __init__(self):
        self.state = {"available": False}
        self.art = None          # (content_type, bytes)
        self.art_key = None
        self.art_version = 0
        self.mgr = None
        self.ok = True
        try:
            from winrt.windows.media.control import (  # noqa: F401
                GlobalSystemMediaTransportControlsSessionManager,
            )
        except Exception as e:  # pragma: no cover
            print(f"[media] Windows media packages not available ({e}). Now playing disabled; media buttons still work.")
            self.ok = False
            return
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._poll())

    async def _session(self):
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MM
        if self.mgr is None:
            self.mgr = await MM.request_async()
        return self.mgr.get_current_session()

    async def _poll(self):
        while True:
            try:
                await self._update()
            except Exception as e:
                self.state = {"available": False, "error": str(e)}
                self.mgr = None
            await asyncio.sleep(1)

    async def _update(self):
        s = await self._session()
        if s is None:
            self.state = {"available": False}
            return
        props = await s.try_get_media_properties_async()
        info = s.get_playback_info()
        tl = s.get_timeline_properties()
        playing = int(info.playback_status) == 4  # PLAYING
        duration = tl.end_time.total_seconds() - tl.start_time.total_seconds()
        position = tl.position.total_seconds()
        if playing:
            try:
                updated = tl.last_updated_time
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - updated).total_seconds()
                if 0 < age < 3600:
                    position += age
            except Exception:
                pass
        if duration > 0:
            position = max(0.0, min(position, duration))

        key = (props.title, props.artist, props.album_title, s.source_app_user_model_id)
        if key != self.art_key:
            self.art_key = key
            self.art = await self._read_thumb(props)
            self.art_version += 1

        app = s.source_app_user_model_id or ""
        self.state = {
            "available": True,
            "title": props.title or "",
            "artist": props.artist or props.album_artist or "",
            "album": props.album_title or "",
            "app": friendly_app(app),
            "playing": playing,
            "position": round(position, 1),
            "duration": round(duration, 1) if duration > 0 else 0,
            "art": self.art_version if self.art else None,
        }

    async def _read_thumb(self, props):
        if props.thumbnail is None:
            return None
        try:
            from winrt.windows.storage.streams import Buffer, InputStreamOptions, DataReader
            stream = await props.thumbnail.open_read_async()
            size = stream.size
            if not size:
                return None
            buf = Buffer(size)
            await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
            try:
                data = bytes(buf)
            except TypeError:
                reader = DataReader.from_buffer(buf)
                arr = bytearray(buf.length)
                reader.read_bytes(arr)
                data = bytes(arr)
            return (stream.content_type or "image/jpeg", data)
        except Exception as e:
            print(f"[media] could not read album art: {e}")
            return None

    async def _command(self, cmd):
        s = await self._session()
        if s is None:
            return False
        fn = {"play_pause": s.try_toggle_play_pause_async,
              "next": s.try_skip_next_async,
              "prev": s.try_skip_previous_async}[cmd]
        return bool(await fn())

    def command(self, cmd):
        if self.ok:
            try:
                fut = asyncio.run_coroutine_threadsafe(self._command(cmd), self.loop)
                if fut.result(timeout=3):
                    return True
            except Exception as e:
                print(f"[media] {cmd} via media session failed ({e}); using media key")
        press_combo(MEDIA_KEYS[cmd])
        return True


MEDIA = Media()


# ---------------------------------------------------------------- AudioCast (PC -> phone audio streaming)

class AudioCast:
    """Watches the AudioCast sender and turns it on/off.

    AudioCast has no remote-control API, but it auto-reconnects to the last phone
    when it starts and disconnects cleanly when it quits, so on/off = start/quit.
    """

    def __init__(self, cfg):
        self.app = cfg.get("app", r"shell:AppsFolder\sskplay.AudioCast_m5e88qwsy0m20!App")
        self.process = cfg.get("process", "audiocast.exe")
        self.state = {"running": False, "connected": False, "peer": None, "busy": False}
        self.lock = threading.Lock()
        threading.Thread(target=self._watch, daemon=True).start()

    def _run(self, *args):
        return subprocess.run(args, capture_output=True, text=True, timeout=10,
                              creationflags=subprocess.CREATE_NO_WINDOW).stdout

    def _pids(self):
        out = self._run("tasklist", "/FI", f"IMAGENAME eq {self.process}", "/FO", "CSV", "/NH")
        pids = set()
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) > 1 and parts[0].lower() == self.process.lower():
                pids.add(parts[1])
        return pids

    def refresh(self):
        pids = self._pids()
        peer = None
        if pids:
            for line in self._run("netstat", "-ano", "-p", "TCP").splitlines():
                cols = line.split()
                if len(cols) == 5 and cols[3] == "ESTABLISHED" and cols[4] in pids:
                    host = cols[2].rsplit(":", 1)[0].strip("[]")
                    if host not in ("127.0.0.1", "::1"):
                        peer = host
                        break
        self.state.update(running=bool(pids), connected=peer is not None, peer=peer)

    def _watch(self):
        while True:
            try:
                self.refresh()
            except Exception as e:
                print(f"[audiocast] status check failed: {e}")
            time.sleep(2)

    def start(self):
        os.startfile(self.app)

    def stop(self):
        # Ask nicely first so AudioCast can disconnect and restore PC sound; force only if it ignores us.
        self._run("taskkill", "/IM", self.process)
        for _ in range(10):
            time.sleep(0.4)
            if not self._pids():
                return
        print("[audiocast] did not quit when asked; forcing it to close")
        self._run("taskkill", "/F", "/IM", self.process)

    def do(self, what):
        with self.lock:
            self.state["busy"] = True
            try:
                running = bool(self._pids())
                if what == "toggle":
                    what = "stop" if running else "start"
                if what == "start" and not running:
                    self.start()
                elif what == "stop" and running:
                    self.stop()
                time.sleep(0.5)
                self.refresh()
            finally:
                self.state["busy"] = False


AUDIOCAST = AudioCast(CONFIG.get("audiocast", {}))


# ---------------------------------------------------------------- Claude voice commands

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


def foreground_exe():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


class ClaudeVoice:
    """Opens the Claude desktop app with a new Claude Code session that has the command filled in.

    Claude's claude://code/new link fills in the prompt but does not send it, so when
    auto_send is on we press Enter -- only if the Claude window is the one in front.
    """

    def __init__(self, cfg):
        self.folder = cfg.get("folder", str(ROOT))
        self.auto_send = cfg.get("auto_send", True)
        self.prefix = cfg.get("prefix", "[Voice command from Desk Deck on my phone] ")
        self.last = {"text": None, "sent": None, "at": None}

    def run(self, text):
        text = " ".join(text.split())[:2000]
        if not text:
            raise ValueError("empty command")
        url = "claude://code/new?" + urlencode({"q": self.prefix + text, "folder": self.folder},
                                                quote_via=quote)
        os.startfile(url)
        self.last = {"text": text, "sent": None, "at": time.time()}
        if self.auto_send:
            threading.Thread(target=self._send, daemon=True).start()
        else:
            self.last["sent"] = False

    def _send(self):
        # Wait for Claude to come to the front and settle, then press Enter once.
        seen = None
        deadline = time.time() + 10
        time.sleep(1.0)
        while time.time() < deadline:
            if foreground_exe() == "claude.exe":
                seen = seen or time.time()
                if time.time() - seen >= 1.2:
                    press_combo("enter")
                    self.last["sent"] = True
                    print("[claude] command sent")
                    return
            else:
                seen = None
            time.sleep(0.2)
        self.last["sent"] = False
        print("[claude] Claude window didn't come to the front; command is filled in, press Enter in Claude to send it")


CLAUDE = ClaudeVoice(CONFIG.get("claude", {}))


# ---------------------------------------------------------------- button actions

def run_action(action):
    t = action.get("type")
    if t == "audiocast":
        AUDIOCAST.do(action.get("do", "toggle"))
    elif t == "media":
        key = action["key"]
        if key in ("play_pause", "next", "prev"):
            MEDIA.command(key)
        else:
            press_combo(MEDIA_KEYS[key])
    elif t == "keys":
        press_combo(action["keys"])
    elif t == "open":
        target = action["target"]
        try:
            os.startfile(target)
        except OSError:
            subprocess.Popen(target, shell=True)
    elif t == "url":
        webbrowser.open(action["url"])
    elif t == "command":
        subprocess.Popen(action["command"], shell=True, cwd=action.get("cwd") or None,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        raise ValueError(f"Unknown action type: {t}")


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "DeskDeck/1.0"

    def log_message(self, fmt, *args):
        if "/api/state" in self.path or "/api/art" in self.path:
            return
        sys.stderr.write("[http] %s\n" % (fmt % args))

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        if ctype.startswith(("text/", "application/json", "application/manifest")):
            ctype += "; charset=utf-8"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self, query):
        pin = self.headers.get("X-Pin") or query.get("pin", [""])[0]
        return hmac.compare_digest(str(pin), str(CONFIG["pin"]))

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path.startswith("/api/"):
            if not self._authed(q):
                return self._send(401, {"error": "bad pin"})
            if url.path == "/api/state":
                return self._send(200, {"media": MEDIA.state, "audiocast": AUDIOCAST.state, "claude": CLAUDE.last})
            if url.path == "/api/buttons":
                return self._send(200, [
                    {**{k: b.get(k) for k in ("id", "label", "icon", "color", "glyph", "status")},
                     "place": b.get("place", "grid"), "logo": b["id"] in LOGOS}
                    for b in CONFIG["buttons"]])
            if url.path.startswith("/api/logo/"):
                logo = LOGOS.get(url.path.rsplit("/", 1)[-1])
                if logo:
                    return self._send(200, logo[1], logo[0], {"Cache-Control": "max-age=86400"})
                return self._send(404, {"error": "no logo"})
            if url.path == "/api/art":
                if MEDIA.art:
                    ctype, data = MEDIA.art
                    return self._send(200, data, ctype)
                return self._send(404, {"error": "no art"})
            return self._send(404, {"error": "not found"})
        self._static(url.path)

    def do_POST(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if not self._authed(q):
            return self._send(401, {"error": "bad pin"})
        parts = url.path.strip("/").split("/")
        try:
            if url.path == "/api/claude":
                length = min(int(self.headers.get("Content-Length") or 0), 20000)
                body = json.loads(self.rfile.read(length) or b"{}")
                CLAUDE.run(str(body.get("text", "")))
                return self._send(200, {"ok": True, "auto_send": CLAUDE.auto_send})
            if len(parts) == 3 and parts[:2] == ["api", "media"] and parts[2] in ("play_pause", "next", "prev"):
                MEDIA.command(parts[2])
                return self._send(200, {"ok": True})
            if len(parts) == 3 and parts[:2] == ["api", "button"]:
                btn = next((b for b in CONFIG["buttons"] if b["id"] == parts[2]), None)
                if not btn:
                    return self._send(404, {"error": "no such button"})
                run_action(btn["action"])
                return self._send(200, {"ok": True})
        except Exception as e:
            print(f"[action] error: {e}")
            return self._send(500, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        f = (WEB / path.lstrip("/")).resolve()
        if WEB not in f.parents or not f.is_file():
            return self._send(404, b"Not found", "text/plain")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if f.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        self._send(200, f.read_bytes(), ctype)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class Server(ThreadingHTTPServer):
    # Windows would otherwise let a second copy bind the same port silently.
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    port = int(os.environ.get("DESKDECK_PORT") or CONFIG.get("port", 8765))
    try:
        httpd = Server(("0.0.0.0", port), Handler)
    except OSError:
        print(f"\n  Desk Deck is already running (port {port} is in use).")
        print("  Close the other Desk Deck window first, then start it again.\n")
        sys.exit(1)
    print("  Loading app logos...")
    load_logos()
    ip = lan_ip()
    print()
    print("  Desk Deck is running")
    print("  -------------------------------------------")
    print(f"  On your iPhone, open Safari and go to:")
    print(f"      http://{ip}:{port}")
    print(f"  PIN: {CONFIG['pin']}")
    print("  Then: Share -> Add to Home Screen")
    print("  (Keep this window open. Ctrl+C to stop.)")
    print()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
