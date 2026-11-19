"""Desk Deck - PC helper server.

Serves the phone web app and gives it:
  * now playing info + play/pause/next/prev (Windows media controls)
  * a button pad whose actions are defined in config.json
  * "phone as PC microphone" (over HTTPS, into a virtual audio cable)

Run:  python server.py
"""

import asyncio
import base64
import ctypes
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from ctypes import wintypes
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
        # Spotify Free reports ads as tracks by the artist "Spotify" (or with no artist at all).
        artist = (props.artist or props.album_artist or "").strip()
        is_ad = (friendly_app(app) == "Spotify" and playing
                 and (artist.lower() in ("", "spotify") or (props.title or "").strip().lower() in ("advertisement", "spotify")))
        self.state = {
            "available": True,
            "ad": is_ad,
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


class AdDucker:
    """Turns Spotify itself down while it plays an ad, and back up afterwards.

    Only Spotify's own slider in the Windows volume mixer changes; other apps and the
    PC's main volume are left alone.
    """

    # Spotify's real volume is kept on disk while it's turned down, so a crash, force-close
    # or reboot mid-ad can't leave Spotify quiet: the next start puts it back.
    SAVE = ROOT / "spotify_volume.json"

    def __init__(self, cfg):
        self.enabled = cfg.get("duck", True)
        self.level = float(cfg.get("volume", 0.0))
        self.saved = None          # Spotify's volume before the ad
        self.ducked = False
        try:
            self.saved = float(json.loads(self.SAVE.read_text())["volume"])
            self.ducked = True     # left turned down last time: restore once the ad (if any) is over
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if self.enabled or self.ducked:
            threading.Thread(target=self._run, daemon=True).start()

    def _sessions(self):
        from pycaw.pycaw import AudioUtilities
        return [s.SimpleAudioVolume for s in AudioUtilities.GetAllSessions()
                if s.Process and s.Process.name().lower() == "spotify.exe"]

    def _run(self):
        try:
            import comtypes
            comtypes.CoInitialize()
            self._sessions()
        except Exception as e:
            print(f"[ads] per-app volume isn't available ({e}); Spotify ads won't be turned down")
            return
        import atexit
        atexit.register(self._restore)
        while True:
            try:
                ad = self.enabled and bool(MEDIA.state.get("ad"))
                if ad and not self.ducked:
                    vols = self._sessions()
                    if vols:
                        self.saved = vols[0].GetMasterVolume()
                        self.SAVE.write_text(json.dumps({"volume": self.saved}))
                        for v in vols:
                            v.SetMasterVolume(min(self.level, v.GetMasterVolume()), None)
                        self.ducked = True
                        print("[ads] Spotify ad: volume lowered")
                elif not ad and self.ducked and self._restore():
                    print("[ads] ad over: Spotify volume restored")
            except Exception as e:
                print(f"[ads] {e}")
            time.sleep(0.5)

    def _restore(self):
        """Puts Spotify back to its saved volume. False if Spotify isn't running yet (try again later)."""
        if not self.ducked:
            return True
        vols = self._sessions()
        if not vols:
            return False
        for v in vols:
            v.SetMasterVolume(1.0 if self.saved is None else self.saved, None)
        self.ducked = False
        try:
            self.SAVE.unlink()
        except OSError:
            pass
        return True


ADS = AdDucker(CONFIG.get("spotify_ads", {}))


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


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p)
user32.EnumWindows.argtypes = [WNDENUMPROC, ctypes.c_void_p]
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.IsIconic.argtypes = [ctypes.c_void_p]
user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.GetWindow.restype = ctypes.c_void_p
user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def exe_of_window(hwnd):
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        return os.path.basename(buf.value).lower() if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(h)


def find_window(exe):
    found = []

    def cb(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4)  # GW_OWNER
                and user32.GetWindowTextLengthW(hwnd) > 0 and exe_of_window(hwnd) == exe):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(cb), None)
    return found[0] if found else None


def bring_to_front(hwnd):
    """Windows blocks background programs from taking focus; these are the standard workarounds."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    me = kernel32.GetCurrentThreadId()
    attached = bool(fg_thread) and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        ok = user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)
    if not ok or foreground_exe() != exe_of_window(hwnd):
        # Last resort: a lone Alt tap counts as user input and unlocks SetForegroundWindow.
        _key(0x12)
        _key(0x12, up=True)
        user32.SetForegroundWindow(hwnd)


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
        # Bring Claude to the front (it may be minimised or behind other windows),
        # wait for it to settle on the new session, then press Enter once.
        seen = None
        last_pull = 0
        deadline = time.time() + 15
        time.sleep(1.0)
        while time.time() < deadline:
            if foreground_exe() != "claude.exe" and time.time() - last_pull > 1.0:
                last_pull = time.time()
                hwnd = find_window("claude.exe")
                if hwnd:
                    try:
                        bring_to_front(hwnd)
                    except Exception as e:
                        print(f"[claude] couldn't bring Claude to the front: {e}")
                time.sleep(0.2)
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


# ---------------------------------------------------------------- phone as PC microphone

class WebSocket:
    """Just enough of RFC 6455 for the phone to stream audio to us."""

    MAX = 1 << 20

    def __init__(self, rfile, wfile):
        self.r, self.w = rfile, wfile
        self.wlock = threading.Lock()

    def _read(self, n):
        data = self.r.read(n)
        if len(data) < n:
            raise ConnectionError("closed")
        return data

    def recv(self):
        """Returns ('text'|'binary', payload) or None when the socket closes."""
        parts, kind = [], None
        while True:
            b0, b1 = self._read(2)
            fin, op = b0 & 0x80, b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if n > self.MAX:
                raise ConnectionError("frame too large")
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = _unmask(data, mask)
            if op == 0x8:          # close
                self._frame(0x8, b"")
                return None
            if op == 0x9:          # ping
                self._frame(0xA, data)
                continue
            if op == 0xA:          # pong
                continue
            if op in (0x1, 0x2):
                kind = "text" if op == 0x1 else "binary"
            parts.append(data)
            if fin:
                return kind, b"".join(parts)

    def _frame(self, op, data):
        n = len(data)
        head = bytes([0x80 | op]) + (bytes([n]) if n < 126 else
                                     bytes([126]) + struct.pack(">H", n) if n < 65536 else
                                     bytes([127]) + struct.pack(">Q", n))
        with self.wlock:
            self.w.write(head + data)
            self.w.flush()

    def send_json(self, obj):
        self._frame(0x1, json.dumps(obj).encode("utf-8"))


def _unmask(data, mask):
    # XOR the whole payload at once via big ints: fast enough for ~2 KB audio frames.
    n = len(data)
    key = int.from_bytes((mask * (n // 4 + 1))[:n], "little")
    return (int.from_bytes(data, "little") ^ key).to_bytes(n, "little")


class PhoneMic:
    """Plays the phone's microphone into a virtual audio cable on the PC.

    With VB-Audio Virtual Cable installed, we play into "CABLE Input" and other apps
    (Discord, Zoom, OBS...) pick "CABLE Output" as their microphone.
    """

    def __init__(self, cfg):
        self.device = cfg.get("device", "CABLE Input")
        self.state = {"connected": False, "device": None, "error": None}
        self.current = None
        self.lock = threading.Lock()
        # Start/stop requests from the PC (tray icon / hotkey). The phone picks them up when it
        # polls /api/state; a stop also cuts the live stream right away.
        self.cmd = {"id": 0, "on": None, "at": 0}
        self.stopped_by_pc = None

    def request(self, on):
        self.cmd = {"id": self.cmd["id"] + 1, "on": bool(on), "at": time.time()}
        if not on and self.current is not None:
            self.stopped_by_pc = self.current
            self.current = None
        print(f"[mic] {'start' if on else 'stop'} requested from the PC")

    def toggle(self):
        self.request(not self.state["connected"])

    def waiting(self):
        """True while a start request is out but the phone hasn't connected yet."""
        c = self.cmd
        return bool(c["on"]) and not self.state["connected"] and time.time() - c["at"] < 10

    def find_device(self, rescan=True):
        import sounddevice as sd
        found = self._match(sd)
        if not found and rescan:
            # PortAudio only lists devices once; re-scan so a cable installed after start shows up.
            sd._terminate()
            sd._initialize()
            found = self._match(sd)
        return found

    def _match(self, sd):
        want = self.device.lower()
        apis = sd.query_hostapis()
        outs = [(i, d) for i, d in enumerate(sd.query_devices())
                if d["max_output_channels"] > 0 and want in d["name"].lower()]
        # MME converts sample rates for us, so the phone's 44.1/48 kHz always works.
        outs.sort(key=lambda x: apis[x[1]["hostapi"]]["name"] != "MME")
        return outs[0] if outs else None

    def serve(self, ws, peer):
        msg = ws.recv()
        if not msg or msg[0] != "text":
            return
        hello = json.loads(msg[1] or b"{}")
        rate = max(8000, min(96000, int(hello.get("rate", 48000))))

        try:
            import sounddevice as sd
            found = self.find_device()
        except Exception as e:
            ws.send_json({"error": f"Audio output isn't available on the PC ({e})"})
            return
        if not found:
            err = ("VB-Cable isn't installed on the PC. Get it free from vb-audio.com/Cable, "
                   "then restart Desk Deck." if self.device.lower().startswith("cable")
                   else f"No audio device named '{self.device}' on the PC.")
            self.state.update(connected=False, error=err)
            ws.send_json({"error": err})
            return
        dev_index, dev = found

        token = object()
        with self.lock:
            self.current = token          # a newer connection takes over from an older one

        buf = bytearray()
        blk = threading.Lock()
        bytes_per_s = rate * 2
        prebuffer = bytes_per_s * 60 // 1000 & ~1       # wait for 60 ms before playing
        ceiling = bytes_per_s * 250 // 1000 & ~1        # never lag more than 250 ms behind
        target = bytes_per_s * 80 // 1000 & ~1
        playing = [False]

        def callback(out, frames, _time, _status):
            need = frames * 2
            with blk:
                if not playing[0] and len(buf) < prebuffer:
                    n = 0
                else:
                    playing[0] = True
                    n = min(need, len(buf))
                    out[:n] = bytes(buf[:n])
                    del buf[:n]
                    if not buf:
                        playing[0] = False      # ran dry: re-buffer instead of crackling
            if n < need:
                out[n:need] = b"\0" * (need - n)

        try:
            stream = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16",
                                        device=dev_index, latency="low", callback=callback)
            stream.start()
        except Exception as e:
            err = f"Couldn't open '{dev['name']}': {e}"
            self.state.update(connected=False, error=err)
            ws.send_json({"error": err})
            return

        name = dev["name"].split(" (")[0]
        self.state.update(connected=True, device=name, error=None)
        print(f"[mic] phone {peer} is streaming its microphone into '{dev['name']}' ({rate} Hz)")
        ws.send_json({"ok": True, "device": name})
        try:
            while self.current is token:
                msg = ws.recv()
                if msg is None:
                    break
                if msg[0] == "binary" and msg[1]:
                    with blk:
                        buf += msg[1][: len(msg[1]) & ~1]
                        if len(buf) > ceiling:
                            del buf[: len(buf) - target]
            if self.stopped_by_pc is token:
                ws.send_json({"stopped": "pc"})
        finally:
            stream.stop()
            stream.close()
            if self.current is token or self.stopped_by_pc is token:
                self.current = None if self.current is token else self.current
                self.state.update(connected=False)
                print("[mic] phone microphone stopped")


MIC = PhoneMic(CONFIG.get("mic", {}))


# ---------------------------------------------------------------- "More" sheet: all apps + PC controls

from pcapps import PCApps  # noqa: E402

PCAPPS = PCApps()
powrprof = ctypes.WinDLL("powrprof")
user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]


def shell_folder(name, sub=None):
    """A user folder (Videos, Pictures...) even when it was moved, e.g. into OneDrive."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            base = Path(os.path.expandvars(winreg.QueryValueEx(k, name)[0]))
    except OSError:
        base = Path.home()
    return base / sub if sub and (base / sub).exists() else base


class System:
    """PC controls for the phone's More sheet. Shut down / restart wait 30 s so they can be cancelled."""

    DELAY = 30

    def __init__(self):
        self.pending = None   # {"what": "shutdown"|"restart", "at": epoch seconds}

    def _later(self, fn, delay=1.0):
        # Let the HTTP reply reach the phone before the PC goes to sleep / locks.
        threading.Timer(delay, fn).start()

    def _shutdown(self, flag, what):
        subprocess.run(["shutdown", flag, "/t", str(self.DELAY)], creationflags=subprocess.CREATE_NO_WINDOW)
        self.pending = {"what": what, "at": time.time() + self.DELAY}

    def cancel(self):
        subprocess.run(["shutdown", "/a"], creationflags=subprocess.CREATE_NO_WINDOW)
        self.pending = None

    def spacedesk(self):
        app = next((a for a in PCAPPS.list() if "spacedesk" in a["name"].lower()), None)
        if not app:
            raise LookupError("spacedesk isn't installed on the PC")
        PCAPPS.launch(app["id"])

    def state(self):
        if self.pending and time.time() > self.pending["at"] + 5:
            self.pending = None
        return {"pending": self.pending}

    def do(self, name):
        acts = {
            "sleep": lambda: self._later(lambda: powrprof.SetSuspendState(0, 0, 0)),
            "lock": lambda: self._later(user32.LockWorkStation),
            "signout": lambda: self._later(lambda: subprocess.run(["shutdown", "/l"])),
            "shutdown": lambda: self._shutdown("/s", "shutdown"),
            "restart": lambda: self._shutdown("/r", "restart"),
            "cancel": self.cancel,
            "displays_off": lambda: self._later(
                lambda: user32.PostMessageW(ctypes.c_void_p(0xFFFF), 0x0112, ctypes.c_void_p(0xF170), ctypes.c_void_p(2))),
            "screenshot": lambda: press_combo("win+printscreen"),
            "snip": lambda: press_combo("win+shift+s"),
            "record": lambda: press_combo("win+alt+r"),
            "clip": lambda: press_combo("win+alt+g"),
            "record_area": lambda: press_combo("win+shift+r"),
            "captures": lambda: os.startfile(shell_folder("My Video", "Captures")),
            "screenshots": lambda: os.startfile(shell_folder("My Pictures", "Screenshots")),
            "show_desktop": lambda: press_combo("win+d"),
            "task_view": lambda: press_combo("win+tab"),
            "switch_app": lambda: press_combo("alt+tab"),
            "task_manager": lambda: press_combo("ctrl+shift+esc"),
            "mute": lambda: press_combo("volume_mute"),
            "settings": lambda: os.startfile("ms-settings:"),
            "spacedesk": self.spacedesk,
        }
        if name not in acts:
            raise ValueError(f"unknown control: {name}")
        acts[name]()


SYSTEM = System()
TLS = {"port": None, "ca": None}


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
        if self.path.startswith(("/api/state", "/api/art", "/api/appicon", "/api/logo")):
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
        if url.path == "/deskdeck-ca.cer" and TLS["ca"]:
            # Public certificate only (no key); safe to hand out without the PIN.
            from tls import ca_der
            return self._send(200, ca_der(TLS["ca"]), "application/x-x509-ca-cert",
                              {"Content-Disposition": 'attachment; filename="DeskDeck.cer"'})
        if url.path.startswith("/api/"):
            if not self._authed(q):
                return self._send(401, {"error": "bad pin"})
            if url.path == "/api/mic":
                return self._websocket()
            if url.path == "/api/state":
                return self._send(200, {"media": {**MEDIA.state, "ducked": ADS.ducked}, "audiocast": AUDIOCAST.state, "claude": CLAUDE.last,
                                        "mic": {**MIC.state, "https_port": TLS["port"], "cmd": MIC.cmd},
                                        "system": SYSTEM.state()})
            if url.path == "/api/apps":
                return self._send(200, PCAPPS.list())
            if url.path == "/api/appicon":
                png = PCAPPS.icon(q.get("id", [""])[0])
                if png:
                    return self._send(200, png, "image/png", {"Cache-Control": "max-age=604800"})
                return self._send(404, {"error": "no icon"})
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
            if url.path == "/api/apps/launch":
                length = min(int(self.headers.get("Content-Length") or 0), 4000)
                PCAPPS.launch(str(json.loads(self.rfile.read(length) or b"{}").get("id", "")))
                return self._send(200, {"ok": True})
            if len(parts) == 3 and parts[:2] == ["api", "system"]:
                try:
                    SYSTEM.do(parts[2])
                except LookupError as e:
                    return self._send(404, {"error": str(e)})
                return self._send(200, {"ok": True, "system": SYSTEM.state()})
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

    def _websocket(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if "websocket" not in (self.headers.get("Upgrade") or "").lower() or not key:
            return self._send(400, {"error": "websocket expected"})
        accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.wfile.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                          f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        self.wfile.flush()
        self.close_connection = True
        self.connection.settimeout(10)   # phone went to sleep / left Wi-Fi -> let go
        try:
            MIC.serve(WebSocket(self.rfile, self.wfile), self.client_address[0])
        except (ConnectionError, OSError, ValueError) as e:
            if not isinstance(e, ConnectionError):
                print(f"[mic] connection ended: {e}")

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

    def handle_error(self, request, client_address):
        # Phones dropping off Wi-Fi or refusing our certificate are normal; don't print tracebacks.
        err = sys.exc_info()[1]
        if isinstance(err, (ConnectionError, ssl.SSLError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def start_https(port, ip):
    try:
        from tls import ensure_certs
    except ImportError:
        print("  [https] 'cryptography' isn't installed, so the phone mic is off. Run start.bat to install it.")
        return None
    try:
        httpsd = Server(("0.0.0.0", port), Handler)
    except OSError:
        print(f"  [https] port {port} is in use, so the phone mic is off.")
        return None
    cert, key, ca = ensure_certs(ROOT, ip)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    # Handshake in the request thread, so a slow or untrusting phone can't stall the server.
    httpsd.socket = ctx.wrap_socket(httpsd.socket, server_side=True, do_handshake_on_connect=False)
    TLS.update(port=port, ca=ca)
    threading.Thread(target=httpsd.serve_forever, daemon=True).start()
    return httpsd


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
    threading.Thread(target=PCAPPS.list, daemon=True).start()   # warm up the All Apps list

    def quit_deskdeck():
        ADS._restore()
        print("  Desk Deck closed from the tray icon.")
        os._exit(0)

    from pctray import start_hotkey, start_tray
    start_tray(MIC, quit_deskdeck)
    hotkey = CONFIG.get("mic", {}).get("hotkey", "ctrl+alt+shift+m")
    hotkey_ok = bool(hotkey) and start_hotkey(hotkey, MIC.toggle, vk_for)
    ip = lan_ip()
    sport = int(os.environ.get("DESKDECK_HTTPS_PORT") or CONFIG.get("https_port", port + 1))
    secure = start_https(sport, ip)
    print()
    print("  Desk Deck is running")
    print("  -------------------------------------------")
    print(f"  On your iPhone, open Safari and go to:")
    print(f"      http://{ip}:{port}")
    if secure:
        print(f"  With the phone mic (after installing the certificate, see README):")
        print(f"      https://{ip}:{sport}")
    if secure:
        print("  Phone mic on/off from the PC: click the mic icon by the clock"
              + (f" or press {hotkey.upper()}" if hotkey_ok else ""))
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
