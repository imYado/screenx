# ScreenX — Desk Deck

Turn an old iPhone X into a CarPlay‑style desk companion for your Windows PC:

- 🕐 **Clock + date widget** (tap to switch 12/24h)
- 🎵 **Now playing** from Spotify, browsers, or any Windows media app, with album art, a blurred-art background, progress, and play/pause/next/previous
- 📱 **App launcher** with the apps' **real logos**, pulled straight from your PC (Spotify, Chrome, Claude, Steam, Files by default)
- 📡 **AudioCast toggle**: tap to start/stop streaming PC audio to your phone, with live status (Streaming / Not connected / Off)
- 🎙️ **Ask Claude**: the rainbow mic in the side bar. Speak (tap 🎤 on the iPhone keyboard) or type a command, and Claude on your PC opens a new Claude Code session with it and sends it
- 🎤 **Phone as PC microphone**: tap the mic button in the side bar and your iPhone becomes a mic for Discord, Zoom, OBS, games…
- 🔇 **Spotify ads turned down automatically**: when Spotify plays an ad, Spotify is silenced (only Spotify, nothing else), then its volume comes back when the music returns
- ▦ **More** (bottom of the side bar): **Apps** shows every app on your PC with its real icon and a search box; **Controls** has sleep, lock, screens off, shut down / restart / sign out (with a 30 s cancel), screenshot, snip, screen recording, show desktop, Task Manager, second screen and more
- 🔊 **Side bar** with volume up/down and a PC-connection indicator

It's a PWA that runs full‑screen on the phone, plus a small Python helper on the PC. The phone talks to the PC over your Wi‑Fi.

## Setup

1. Install Python on the PC (from python.org or `winget install Python.Python.3`).
2. Double‑click **`start.bat`**. On first run it installs what it needs and creates `config.json` with a random PIN.
   If Windows Firewall asks, allow Python on **Private networks**.
3. The window shows an address like `http://192.168.0.195:8765` and a PIN.
4. On the iPhone (same Wi‑Fi), open that address in **Safari** and enter the PIN.
5. Tap **Share → Add to Home Screen**. Open it from the home screen; it runs full‑screen.

### Start automatically with Windows
```powershell
powershell -ExecutionPolicy Bypass -File autostart.ps1          # turn on
powershell -ExecutionPolicy Bypass -File autostart.ps1 -Remove  # turn off
```
This adds a "Desk Deck" shortcut (opens minimised) to your Startup folder. You can also switch it off in Settings → Apps → Startup.

### Ask Claude (voice commands)
Needs the Claude desktop app. Desk Deck opens `claude://code/new` with your command filled in, then presses Enter to send it, but only if the Claude window is in front (so a stray Enter never goes to another app). If Claude didn't come to the front, the command is waiting in Claude; just press Enter there. Settings in `config.json`:

```json
"claude": { "folder": "C:\\Users\\you\\Documents\\screenx", "auto_send": true }
```

`folder` is where the new session works. Claude still asks on the PC before risky actions, as usual. Anyone with your Desk Deck PIN on your Wi‑Fi can send commands, so keep the PIN private.

### Phone as PC microphone
The crossed‑out mic in the side bar turns your iPhone into a microphone for the PC. It turns green and pulses with your voice while live. One-time setup:

1. **On the PC:** install [VB‑Audio Virtual Cable](https://vb-audio.com/Cable/) (free; run its setup as administrator, then reboot). Restart Desk Deck.
2. **On the iPhone:** Safari only allows the microphone over HTTPS, so Desk Deck also runs a secure address (the window shows it, e.g. `https://192.168.0.195:8766`). Tap the mic button once and follow the steps on screen:
   - **Get certificate** → Allow, then Settings → General → **VPN & Device Management** → Desk Deck → **Install**
   - Settings → General → About → **Certificate Trust Settings** → turn on **Desk Deck**
   - **Open secure Desk Deck**, then Share → **Add to Home Screen**. Use this new icon from now on (you can delete the old one).
3. **In your apps** (Discord, Zoom, OBS, Windows Sound settings…), choose **CABLE Output** as the microphone.

**Turn it on/off from the PC:** click the **mic icon by the clock** in the taskbar (green = live, yellow = waiting for the phone), or press **Ctrl+Alt+Shift+M** anywhere. Desk Deck must be open on the phone's screen (the secure https icon), and you need to have tapped it once since opening it, because iOS only allows audio after a tap. Change the hotkey with `"mic": { "hotkey": "ctrl+shift+f9" }` in `config.json`. Right-click the tray icon to quit Desk Deck.

**Keep your speakers as the default output.** Installing VB-Cable can make *CABLE Input* the Windows default playback device. Then all PC sound goes into the cable instead of your speakers, it mixes into the phone mic, and AudioCast keeps dropping. Fix it in Settings → System → Sound → Output by choosing your speakers or monitor again.

Things to know:
- The mic only runs while Desk Deck is open on the phone's screen; iOS stops it if you switch apps or lock the phone.
- Delay is about 0.1–0.2 s over Wi‑Fi. iOS echo cancellation and noise suppression are on.
- If AudioCast is playing PC sound through the phone's speaker, the mic may pick it up; use headphones on the PC or turn AudioCast off during calls.
- iOS may ask for microphone permission each time you open the home-screen app.
- To use a different virtual device, set `"mic": { "device": "part of its name" }` in `config.json`. The secure port is `"https_port"` (default: `port` + 1).

The certificate is made on your PC (in the `certs` folder, git-ignored) and can only vouch for local network addresses, never real websites. Keep that folder private anyway, and delete it to make a new one (you'd then reinstall it on the phone).

### More: all apps and PC controls
Tap the ▦ button at the bottom of the side bar.

- **Apps** lists what the Start menu's *All apps* shows (desktop programs, Store apps, Steam games), minus uninstallers, readmes, web links and Windows admin tools. Type to search; Enter opens the first match. The list refreshes every 5 minutes. Only apps on that list can be launched from the phone.
- **Controls**
  - *Power*: Sleep, Lock, Screens off, Shut down, Restart, Sign out. Risky ones need a second tap. Shut down and Restart wait 30 s and show a **Cancel** bar on the phone.
  - *Screenshot & record*: Screenshot (saved to Pictures › Screenshots), Snip an area, **Record / stop** (Xbox Game Bar records the app in front, saved to Videos › Captures), Record an area (Snipping Tool), Save last 30 s (needs Game Bar's background recording on), and shortcuts to both folders.
  - *Windows*: Show desktop, Task view, Switch app, Task Manager, Mute, Settings.
  - *Second screen*: starts **spacedesk** on the PC. Install the free spacedesk Windows driver (spacedesk.net) and the spacedesk iPhone app first; then tap it and connect from the app.
  - *This phone*: Dim Desk Deck (moved here from the side bar), Mic setup, Reload.

### Spotify ads
While Spotify plays an ad, Desk Deck mutes **Spotify's own volume** (the Windows volume mixer slider; nothing else changes) and restores it when the ad ends. The now-playing card shows "Ad · muted". Ads are recognised by Spotify reporting the artist as "Spotify" (or none). Settings in `config.json`:

```json
"spotify_ads": { "duck": true, "volume": 0 }
```

`volume` is 0–1 (default 0 = silent during ads). Set `duck` to `false` to turn it off. If Desk Deck is closed or crashes mid-ad, it puts Spotify's volume back the next time it starts.

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
