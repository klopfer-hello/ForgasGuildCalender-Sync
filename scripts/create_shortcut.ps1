# Creates a Windows Startup shortcut for FGC Calendar Sync.
# Assumes the package is installed in a venv at .venv/ next to this script's parent dir.

$scriptDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$exe = Join-Path $scriptDir ".venv\Scripts\fgc-sync.exe"

if (-not (Test-Path $exe)) {
    Write-Host "fgc-sync.exe not found at $exe" -ForegroundColor Red
    Write-Host "Make sure you ran: pip install -e ." -ForegroundColor Yellow
    exit 1
}

$startup = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup")
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut("$startup\FGCCalendarSync.lnk")
$sc.TargetPath = $exe
$sc.WorkingDirectory = $scriptDir
$sc.Description = "FGC Calendar Sync"
$sc.Save()

Write-Host "Startup shortcut created at: $startup\FGCCalendarSync.lnk" -ForegroundColor Green
