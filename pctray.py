"""Phone-mic controls on the PC: a tray icon next to the clock and a global hotkey.

Left-click the tray icon (or press the hotkey, Ctrl+Alt+Shift+M by default) to turn the
phone's microphone on or off. The icon is green while the phone mic is live.
"""

import ctypes
import threading
import time
from ctypes import wintypes

COLORS = {"live": (48, 209, 88), "waiting": (255, 214, 10), "off": (99, 99, 102)}


def _icon_image(mode):
    from PIL import Image, ImageDraw
    s = 64
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((1, 1, s - 2, s - 2), fill=COLORS[mode] + (255,))
    ink = (0, 0, 0, 255) if mode == "live" else (255, 255, 255, 255)
    d.rounded_rectangle((24, 11, 40, 37), radius=8, fill=ink)
    d.arc((17, 20, 47, 44), start=0, end=180, fill=ink, width=4)
    d.line((32, 44, 32, 52), fill=ink, width=4)
    if mode == "off":
        d.line((16, 14, 48, 50), fill=COLORS["off"] + (255,), width=9)
        d.line((16, 14, 48, 50), fill=ink, width=4)
    return img


def start_tray(mic, on_quit):
    """Runs the tray icon in a background thread. Returns False if pystray isn't installed."""
    try:
        import pystray
    except ImportError:
        print("  [tray] 'pystray' isn't installed, so there's no tray icon. Run start.bat to install it.")
        return False

    def mode():
        if mic.state["connected"]:
            return "live"
        return "waiting" if mic.waiting() else "off"

    def label(_item=None):
        return {"live": "Turn phone mic off", "waiting": "Waiting for the phone…",
                "off": "Turn phone mic on"}[mode()]

    icon = pystray.Icon(
        "DeskDeck", _icon_image("off"), "Desk Deck: phone mic off",
        menu=pystray.Menu(
            pystray.MenuItem(label, lambda: mic.toggle(), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Desk Deck", lambda: on_quit()),
        ))

    def watch():
        shown, asked_at = None, None
        while True:
            m = mode()
            if m != shown:
                icon.icon = _icon_image(m)
                icon.title = {"live": "Desk Deck: phone mic ON (CABLE Output)",
                              "waiting": "Desk Deck: waiting for the phone…",
                              "off": "Desk Deck: phone mic off"}[m]
                try:
                    icon.update_menu()
                except Exception:
                    pass
                if shown == "waiting" and m == "off" and asked_at == mic.cmd["id"]:
                    icon.notify("The phone didn't answer. Open the secure Desk Deck on it "
                                "(the https icon) and keep it on screen.", "Phone mic")
                shown = m
            if m == "waiting":
                asked_at = mic.cmd["id"]
            time.sleep(0.4)

    threading.Thread(target=icon.run, daemon=True).start()
    threading.Thread(target=watch, daemon=True).start()
    return True


# ---------------------------------------------------------------- global hotkey

MODS = {"ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312


def start_hotkey(combo, action, vk_for):
    """Registers a system-wide hotkey like 'ctrl+alt+shift+m'. Returns False if Windows refused it."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    mods = sum(MODS[p] for p in parts if p in MODS)
    keys = [p for p in parts if p not in MODS]
    if len(keys) != 1:
        print(f"  [hotkey] '{combo}' needs exactly one non-modifier key")
        return False
    vk = vk_for(keys[0])
    result = {}
    ready = threading.Event()

    def loop():
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        result["ok"] = bool(user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk))
        ready.set()
        if not result["ok"]:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    action()
                except Exception as e:
                    print(f"[hotkey] {e}")

    threading.Thread(target=loop, daemon=True).start()
    ready.wait(2)
    if not result.get("ok"):
        print(f"  [hotkey] {combo} is already used by another app; set another in config.json (mic.hotkey)")
    return result.get("ok", False)
