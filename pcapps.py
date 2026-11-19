"""Every launchable app on this PC (what the Start menu's "All apps" shows), minus the junk.

Uses Windows' own "AppsFolder" (shell:AppsFolder), so desktop programs, Store apps
and Steam games all come from one place, launch the same way, and get their real
icons from the shell.
"""

import ctypes
import json
import re
import subprocess
import threading
import time
from ctypes import wintypes

from icons import BITMAP, BITMAPINFOHEADER, gdi32, png_rgba, user32

# Start-menu entries that aren't apps you'd launch from a desk companion.
JUNK_NAME = re.compile(
    r"uninstall|readme|read me|release notes|what'?s new|what is new|documentation|manual|\bhelp\b|\bfaq|"
    r"website|support center|license|reset preferences|telemetry|language preferences|^about |"
    r"debuggable|\(x86\)|odbc|iscsi|component services|local security policy|print management|"
    r"recovery drive|memory diagnostic|fax and scan|steps recorder|spreadsheet compare|"
    r"defragment|disk cleanup|system configuration|system information|performance monitor|"
    r"resource monitor|event viewer|task scheduler|computer management|services$|registry editor|"
    r"firewall with advanced|windows tools|^run$|^pydoc|developer (command prompt|powershell)|"
    r"^git (cmd|gui)$|skinned|software update|installer$|install manager|卸载|^\?",
    re.I)
JUNK_TARGET = re.compile(r"^(https?|ftp)://|uninst[^\\]*\.exe$|\.(url|txt|html?|chm|pdf|md|rtf|msc)$|\\[^\\.]+$", re.I)
TRAILING_FOLDER = re.compile(r"^\{[0-9A-F-]+\}\\[^.]*$", re.I)   # "cURL Folder" style entries


class PCApps:
    def __init__(self, ttl=300):
        self.ttl = ttl
        self.apps, self.ids, self.at = [], set(), 0
        self.icons = {}
        self.lock = threading.Lock()

    def _scan(self):
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW).stdout
        rows = json.loads(out.decode("utf-8") or "[]")
        if isinstance(rows, dict):
            rows = [rows]
        seen, apps = set(), []
        for r in rows:
            name, aid = (r.get("Name") or "").strip(), (r.get("AppID") or "").strip()
            if not name or not aid or name.lower() in seen:
                continue
            if JUNK_NAME.search(name) or JUNK_TARGET.search(aid) or TRAILING_FOLDER.match(aid):
                continue
            # steam:// game links are fine; other web links are not.
            seen.add(name.lower())
            apps.append({"id": aid, "name": name})
        apps.sort(key=lambda a: a["name"].lower())
        return apps

    def list(self):
        with self.lock:
            if not self.apps or time.time() - self.at > self.ttl:
                self.apps = self._scan()
                self.ids = {a["id"] for a in self.apps}
                self.at = time.time()
            return self.apps

    def launch(self, app_id):
        self.list()
        if app_id not in self.ids:
            raise ValueError("not an app on this PC")
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app_id])

    def icon(self, app_id, size=96):
        if app_id not in self.ids:
            return None
        if app_id not in self.icons:
            self.icons[app_id] = shell_icon("shell:AppsFolder\\" + app_id, size)
        return self.icons[app_id]


# ---------------------------------------------------------------- shell icons (IShellItemImageFactory)

ole32 = ctypes.WinDLL("ole32")
shell32 = ctypes.WinDLL("shell32")
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                                ctypes.POINTER(ctypes.c_void_p)]


class GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]


IID_IShellItemImageFactory = GUID(0xBCC18B79, 0xBA16, 0x442F,
                                  (ctypes.c_ubyte * 8)(0x80, 0xC4, 0x8A, 0x59, 0xC3, 0x0C, 0x46, 0x3B))
GetImage = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.SIZE, ctypes.c_int,
                              ctypes.POINTER(ctypes.c_void_p))
Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def shell_icon(parsing_name, size=96):
    ole32.CoInitializeEx(None, 0x2)   # per thread; harmless if already initialised
    ptr = ctypes.c_void_p()
    if shell32.SHCreateItemFromParsingName(parsing_name, None, ctypes.byref(IID_IShellItemImageFactory),
                                           ctypes.byref(ptr)) != 0 or not ptr.value:
        return None
    vtbl = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    hbmp = ctypes.c_void_p()
    try:
        # SIIGBF_ICONONLY | SIIGBF_BIGGERSIZEOK
        if GetImage(vtbl[3])(ptr, wintypes.SIZE(size, size), 0x4 | 0x1, ctypes.byref(hbmp)) != 0 or not hbmp.value:
            return None
        bm = BITMAP()
        gdi32.GetObjectW(hbmp, ctypes.sizeof(bm), ctypes.byref(bm))
        w, h = bm.bmWidth, bm.bmHeight
        bih = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                               biPlanes=1, biBitCount=32, biCompression=0)
        buf = ctypes.create_string_buffer(w * h * 4)
        hdc = user32.GetDC(None)
        gdi32.GetDIBits(hdc, hbmp, 0, h, buf, ctypes.byref(bih), 0)
        user32.ReleaseDC(None, hdc)
        px = bytearray(buf.raw)
        has_alpha = any(px[i] for i in range(3, len(px), 4))
        for i in range(0, len(px), 4):
            b, g, r, a = px[i], px[i + 1], px[i + 2], px[i + 3]
            if not has_alpha:
                a = 255
            elif 0 < a < 255:   # the shell hands back premultiplied alpha
                b, g, r = (min(255, c * 255 // a) for c in (b, g, r))
            px[i], px[i + 1], px[i + 2], px[i + 3] = r, g, b, a
        return png_rgba(w, h, bytes(px))
    finally:
        if hbmp.value:
            gdi32.DeleteObject(hbmp)
        Release(vtbl[2])(ptr)
