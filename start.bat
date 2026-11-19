@echo off
cd /d "%~dp0"
title Desk Deck
py -c "import winrt.windows.media.control" 2>nul || (
  echo Installing Windows media support...
  py -m pip install --quiet winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation winrt-Windows.Storage.Streams
)
py -c "import cryptography, sounddevice" 2>nul || (
  echo Installing phone-mic support...
  py -m pip install --quiet cryptography sounddevice
)
py server.py
pause
