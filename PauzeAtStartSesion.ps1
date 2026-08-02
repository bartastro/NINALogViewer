# 1. Haal het pad op van het actieve OneDrive proces (werkt altijd, ook als Admin)
$oneDriveProc = Get-Process -Name "OneDrive" -ErrorAction SilentlyContinue | Select-Object -First 1

if ($oneDriveProc) {
    $oneDriveExe = $oneDriveProc.Path
}
else {
    # Val terug op de bekende paden voor de gebruiker 'barta'
    $paths = @(
        "C:\Users\barta\AppData\Local\Microsoft\OneDrive\OneDrive.exe",
        "C:\Program Files\Microsoft OneDrive\OneDrive.exe",
        "C:\Program Files (x86)\Microsoft OneDrive\OneDrive.exe"
    )
    $oneDriveExe = $paths | Where-Object { Test-Path $_ } | Select-Object -First 1
}

# 2. Pauzeer OneDrive voor 24 uur (86400 sec)
if ($oneDriveExe) {
    Start-Process -FilePath $oneDriveExe -ArgumentList "/pause 86400" -ErrorAction SilentlyContinue
    Write-Host "OneDrive succesvol gepauzeerd." -ForegroundColor Green
}
else {
    Write-Warning "Kon het OneDrive.exe bestand niet traceren."
}

# Stop de Windows Update Service
Stop-Service -Name "wuauserv" -Force -ErrorAction SilentlyContinue

# Optioneel: Stop de Delivery Optimization Service (P2P updates op de achtergrond)
Stop-Service -Name "dosvc" -Force -ErrorAction SilentlyContinue