# ScreenX — Desk Deck

Turn an old iPhone X into a desk companion for your Windows PC:

- 🕐 **Big clock + date** (tap to switch 12/24h, long‑press to dim)
- 🎵 **Now playing** from Spotify, browsers, or any Windows media app — album art, progress, play/pause/next/prev
- 🎛️ **Button pad** — volume, mute, hotkeys, open apps, run commands (swipe for more pages if you add >9)

It's a PWA that runs full‑screen on the phone, plus a small Python helper on the PC. The phone talks to the PC over your Wi‑Fi.

## Setup

1. Install Python on the PC (from python.org or `winget install Python.Python.3`).
2. Double‑click **`start.bat`**. On first run it installs what it needs and creates `config.json` with a random PIN.
   If Windows Firewall asks, allow Python on **Private networks**.
3. The window shows an address like `http://192.168.0.195:8765` and a PIN.
4. On the iPhone (same Wi‑Fi), open that address in **Safari** and enter the PIN.
5. Tap **Share → Add to Home Screen**. Open it from the home screen; it runs full‑screen.

### iPhone tips
- Settings → Display & Brightness → **Auto‑Lock: Never**
- Settings → Accessibility → **Guided Access** to lock the phone into the app
- The layout drifts a few pixels every minute to protect the OLED screen from burn‑in.

## Customising buttons

Edit `config.json` (created on first run), then restart `start.bat`. Each button:

```json
{ "id": "obs", "label": "OBS", "icon": "🎬", "color": "#8b5cf6",
  "action": { "type": "open", "target": "C:\\Program Files\\obs-studio\\bin\\64bit\\obs64.exe" } }
```

| `action.type` | Fields | Example |
|---|---|---|
| `media` | `key`: `volume_up`, `volume_down`, `mute`, `play_pause`, `next`, `prev` | `{"type":"media","key":"mute"}` |
| `keys` | `keys`: combo joined with `+` | `{"type":"keys","keys":"ctrl+shift+m"}` |
| `open` | `target`: app path, program name, file, or URI | `{"type":"open","target":"spotify:"}` |
| `url` | `url` | `{"type":"url","url":"https://youtube.com"}` |
| `command` | `command`, optional `cwd` | `{"type":"command","command":"shutdown /s /t 60"}` |

Key names: `ctrl`, `shift`, `alt`, `win`, `esc`, `enter`, `tab`, `space`, `f1`–`f24`, arrows (`up`, `down`, `left`, `right`), letters and digits.

## Notes
- `config.json` holds your PIN and is git‑ignored. Delete it to reset to the default buttons and a new PIN.
- Anyone on your Wi‑Fi with the PIN can press your buttons. Only predefined actions in `config.json` can run.
- Hotkeys can't reach apps running as administrator unless the helper also runs as administrator.
