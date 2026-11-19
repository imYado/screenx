# Start Desk Deck automatically when you sign in to Windows.
#   Turn on:   powershell -ExecutionPolicy Bypass -File autostart.ps1
#   Turn off:  powershell -ExecutionPolicy Bypass -File autostart.ps1 -Remove
# It puts a shortcut to start.bat (opening minimised) in your Startup folder.
# You can also manage it in Settings > Apps > Startup.
param([switch]$Remove)

$startup  = [Environment]::GetFolderPath('Startup')
$shortcut = Join-Path $startup 'Desk Deck.lnk'

if ($Remove) {
    if (Test-Path $shortcut) { Remove-Item $shortcut; 'Desk Deck will no longer start with Windows.' }
    else { 'Desk Deck was not set to start with Windows.' }
    return
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
$s.TargetPath       = Join-Path $here 'start.bat'
$s.WorkingDirectory = $here
$s.WindowStyle      = 7   # minimised
$s.IconLocation     = Join-Path $env:WINDIR 'System32\SHELL32.dll,15'
$s.Description      = 'Desk Deck helper for the iPhone desk screen'
$s.Save()
"Desk Deck will start (minimised) when you sign in to Windows."
"Shortcut: $shortcut"
