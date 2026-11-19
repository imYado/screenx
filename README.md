# ScreenX — Desk Deck

Turn an old iPhone X into a CarPlay‑style desk companion for your Windows PC:

- 🕐 **Clock + date widget** (tap to switch 12/24h)
- 🎵 **Now playing** from Spotify, browsers, or any Windows media app, with album art, a blurred-art background, progress, and play/pause/next/previous
- 📱 **App launcher** with the apps' **real logos**, pulled straight from your PC (Spotify, Chrome, Claude, Steam, Files by default)
- 📡 **AudioCast toggle**: tap to start/stop streaming PC audio to your phone, with live status (Streaming / Not connected / Off)
- 🔊 **Side bar** with volume up/down, a PC-connection indicator and a dim button

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

Edit `config.json` (created on first run), then restart `start.bat`. App buttons (up to 6 per page; swipe for more):

```json
{ "id": "obs", "label": "OBS", "logo": "exe:obs64.exe",
  "action": { "type": "open", "target": "C:\\Program Files\\obs-studio\\bin\\64bit\\obs64.exe" } }
```

Side-bar buttons use `"place": "sidebar"` and a built-in `"glyph"` (`vol_up`, `vol_down`, `mute`, `play`, `pause`, `next`, `prev`, `moon`):

```json
{ "id": "mute", "label": "Mute", "place": "sidebar", "glyph": "mute",
  "action": { "type": "media", "key": "mute" } }
```

**`logo`**: where the real app icon comes from:

| Value | Meaning |
|---|---|
| `exe:chrome.exe` | App registered with Windows (App Paths) or on PATH |
| `exe:steam` | Steam (found via its registry key) |
| `exe:C:\path\to\app.exe` | Any .exe / .dll / .ico (add `,N` for icon index N) |
| `appx:SpotifyAB.SpotifyMusic` | Microsoft Store / MSIX app, by package name (`Get-AppxPackage`) |
| `file:C:\path\to\logo.png` | Any image you like |

Logos that are already a filled square (like Claude's) automatically fill the whole icon; round or transparent logos sit on a dark iOS‑style tile.

**`action`**: what the button does:

| `type` | Fields | Example |
|---|---|---|
| `open` | `target`: app name, path, file, URI or `shell:AppsFolder\…` | `{"type":"open","target":"spotify:"}` |
| `media` | `key`: `volume_up`, `volume_down`, `mute`, `play_pause`, `next`, `prev` | `{"type":"media","key":"mute"}` |
| `keys` | `keys`: combo joined with `+` | `{"type":"keys","keys":"ctrl+shift+m"}` |
| `url` | `url` | `{"type":"url","url":"https://youtube.com"}` |
| `command` | `command`, optional `cwd` | `{"type":"command","command":"shutdown /s /t 60"}` |
| `audiocast` | `do`: `toggle`, `start` or `stop` (add `"status": "audiocast"` to the button for the live status line) | `{"type":"audiocast","do":"toggle"}` |

AudioCast has no remote-control API, so Desk Deck turns it on by launching it (it reconnects to the last phone by itself) and off by closing it (it disconnects and restores PC sound). "Streaming" means AudioCast has a live connection to another device on your network.

Key names: `ctrl`, `shift`, `alt`, `win`, `esc`, `enter`, `tab`, `space`, `f1`–`f24`, arrows (`up`, `down`, `left`, `right`), letters and digits.

## Notes
- `config.json` holds your PIN and is git‑ignored. Delete it to reset to the default buttons and a new PIN.
- Anyone on your Wi‑Fi with the PIN can press your buttons. Only predefined actions in `config.json` can run.
- Hotkeys can't reach apps running as administrator unless the helper also runs as administrator.
