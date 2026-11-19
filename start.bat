@echo off
cd /d "%~dp0"
title Desk Deck
py -c "import winrt.windows.media.control" 2>nul || (
  echo Installing Windows media support...
  py -m pip install --quiet winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation winrt-Windows.Storage.Streams
)
py -c "import cryptography, sounddevice, pycaw, pystray, PIL" 2>nul || (
  echo Installing phone-mic, tray icon and Spotify-ad support...
  py -m pip install --quiet cryptography sounddevice pycaw pystray pillow
)
py server.py
pause
