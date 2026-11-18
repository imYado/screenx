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
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
CONFIG_PATH = ROOT / "config.json"

DEFAULT_BUTTONS = [
    {"id": "vol_down", "label": "Vol -", "icon": "🔉", "color": "#3b82f6", "action": {"type": "media", "key": "volume_down"}},
    {"id": "mute", "label": "Mute", "icon": "🔇", "color": "#64748b", "action": {"type": "media", "key": "mute"}},
    {"id": "vol_up", "label": "Vol +", "icon": "🔊", "color": "#3b82f6", "action": {"type": "media", "key": "volume_up"}},
    {"id": "discord_mute", "label": "Discord Mic", "icon": "🎙️", "color": "#5865f2", "action": {"type": "keys", "keys": "ctrl+shift+m"}},
    {"id": "screenshot", "label": "Screenshot", "icon": "✂️", "color": "#f59e0b", "action": {"type": "keys", "keys": "win+shift+s"}},
    {"id": "taskmgr", "label": "Task Mgr", "icon": "📈", "color": "#10b981", "action": {"type": "keys", "keys": "ctrl+shift+esc"}},
    {"id": "spotify", "label": "Spotify", "icon": "🎵", "color": "#1db954", "action": {"type": "open", "target": "spotify:"}},
    {"id": "explorer", "label": "Files", "icon": "📁", "color": "#eab308", "action": {"type": "open", "target": "explorer"}},
    {"id": "lock", "label": "Lock PC", "icon": "🔒", "color": "#ef4444", "action": {"type": "command", "command": "rundll32.exe user32.dll,LockWorkStation"}},
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


# ---------------------------------------------------------------- button actions

def run_action(action):
    t = action.get("type")
    if t == "media":
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
                return self._send(200, {"media": MEDIA.state})
            if url.path == "/api/buttons":
                return self._send(200, [{k: b.get(k) for k in ("id", "label", "icon", "color")} for b in CONFIG["buttons"]])
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


def main():
    port = int(CONFIG.get("port", 8765))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
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
