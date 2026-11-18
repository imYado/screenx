"""Pull real app logos off this PC as PNG bytes.

`logo` values understood (set per button in config.json):
  "exe:chrome.exe"                 -> app registered in Windows "App Paths", or on PATH
  "exe:steam"                      -> Steam (found via its registry key)
  "exe:C:\\path\\to\\app.exe"      -> any .exe / .dll / .ico (",N" suffix picks icon index)
  "appx:SpotifyAB.SpotifyMusic"    -> Microsoft Store / MSIX app, by package name
  "file:C:\\path\\to\\logo.png"    -> any image file you like
"""

import ctypes
import os
import re
import shutil
import struct
import subprocess
import winreg
import xml.etree.ElementTree as ET
import zlib
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

HANDLE = ctypes.c_void_p
user32.PrivateExtractIconsW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.POINTER(HANDLE), ctypes.POINTER(wintypes.UINT),
                                        wintypes.UINT, wintypes.UINT]
user32.PrivateExtractIconsW.restype = wintypes.UINT
user32.GetIconInfo.argtypes = [HANDLE, ctypes.c_void_p]
user32.DestroyIcon.argtypes = [HANDLE]
user32.GetDC.argtypes = [HANDLE]
user32.GetDC.restype = HANDLE
user32.ReleaseDC.argtypes = [HANDLE, HANDLE]
gdi32.GetObjectW.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi32.GetDIBits.argtypes = [HANDLE, HANDLE, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [HANDLE]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", HANDLE), ("hbmColor", HANDLE)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
                ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def png_rgba(w, h, rgba):
    rows = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def exe_icon(path, index=0, size=256):
    hicon = HANDLE()
    iid = wintypes.UINT()
    n = user32.PrivateExtractIconsW(str(path), index, size, size, ctypes.byref(hicon), ctypes.byref(iid), 1, 0)
    if n == 0 or n == 0xFFFFFFFF or not hicon.value:
        return None
    info = ICONINFO()
    try:
        if not user32.GetIconInfo(hicon, ctypes.byref(info)) or not info.hbmColor:
            return None
        bm = BITMAP()
        gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bm), ctypes.byref(bm))
        w, h = bm.bmWidth, bm.bmHeight
        bih = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                               biPlanes=1, biBitCount=32, biCompression=0)
        buf = ctypes.create_string_buffer(w * h * 4)
        hdc = user32.GetDC(None)
        gdi32.GetDIBits(hdc, info.hbmColor, 0, h, buf, ctypes.byref(bih), 0)
        user32.ReleaseDC(None, hdc)
        bgra = bytearray(buf.raw)
        has_alpha = any(bgra[i] for i in range(3, len(bgra), 4))
        for i in range(0, len(bgra), 4):
            bgra[i], bgra[i + 2] = bgra[i + 2], bgra[i]
            if not has_alpha:
                bgra[i + 3] = 255
        return png_rgba(w, h, bytes(bgra))
    finally:
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            gdi32.DeleteObject(info.hbmMask)
        user32.DestroyIcon(hicon)


def find_exe(name):
    if os.path.isabs(name) and os.path.exists(name):
        return name
    if name.lower().removesuffix(".exe") == "steam":
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                p = winreg.QueryValueEx(k, "SteamExe")[0]
                if os.path.exists(p):
                    return p
        except OSError:
            pass
    exe = name if name.lower().endswith(".exe") else name + ".exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                p = winreg.QueryValueEx(k, "")[0].strip('"')
                if os.path.exists(p):
                    return p
        except OSError:
            pass
    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / exe
    if win.exists():
        return str(win)
    return shutil.which(exe)


def png_size(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    return (0, 0)


def appx_logo(package_name):
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-AppxPackage -Name '{package_name}' | Select-Object -First 1).InstallLocation"],
        capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    loc = out.stdout.strip()
    if not loc or not os.path.isdir(loc):
        return None
    stems = []
    try:
        root = ET.parse(os.path.join(loc, "AppxManifest.xml")).getroot()
        for el in root.iter():
            if el.tag.endswith("VisualElements"):
                for attr in ("Square44x44Logo", "Square150x150Logo"):
                    if el.get(attr):
                        stems.append(el.get(attr))
                break
    except Exception:
        stems = [r"Assets\Square44x44Logo.png", r"Assets\Square150x150Logo.png"]

    best, best_score = None, -1
    for stem in stems:
        folder = Path(loc) / Path(stem).parent
        base = Path(stem).stem
        if not folder.is_dir():
            continue
        for f in folder.glob(base + "*.png"):
            name = f.name.lower()
            if "contrast" in name or "lightunplated" in name:
                continue
            m = re.search(r"targetsize-(\d+)", name)
            if m:  # tightly-cropped app-list icons: prefer these (largest, unplated)
                score = 100000 + int(m.group(1)) * 10 + (5 if "unplated" in name else 0)
            else:  # padded Start-menu tiles: fallback only
                score = png_size(f.read_bytes()[:24])[0]
            if score > best_score:
                best, best_score = f, score
    return best.read_bytes() if best else None


def load_logo(spec):
    """Returns (content_type, bytes) or None."""
    if not spec:
        return None
    kind, _, value = spec.partition(":")
    try:
        if kind == "exe":
            idx = 0
            m = re.match(r"^(.*),(-?\d+)$", value)
            if m:
                value, idx = m.group(1), int(m.group(2))
            path = find_exe(value)
            data = exe_icon(path, idx) if path else None
            return ("image/png", data) if data else None
        if kind == "appx":
            data = appx_logo(value)
            return ("image/png", data) if data else None
        if kind == "file":
            p = Path(value)
            if p.is_file():
                ext = p.suffix.lower()
                if ext in (".exe", ".dll", ".ico"):
                    data = exe_icon(p)
                    return ("image/png", data) if data else None
                ctype = {".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                         ".webp": "image/webp"}.get(ext, "image/png")
                return (ctype, p.read_bytes())
    except Exception as e:
        print(f"[logo] {spec}: {e}")
    return None
