# 1. Probeer het pad van het actieve OneDrive-proces te pakken
$oneDriveProc = Get-Process -Name "OneDrive" -ErrorAction SilentlyContinue | Select-Object -First 1

if ($oneDriveProc) {
    $oneDriveExe = $oneDriveProc.Path
}
else {
    # Dynamisch pad naar de AppData van de ingelogde gebruiker + Program Files
    $userAppData = "C:\Users\$env:USERNAME\AppData\Local\Microsoft\OneDrive\OneDrive.exe"
    
    $paths = @(
        $userAppData,
        "C:\Program Files\Microsoft OneDrive\OneDrive.exe",
        "C:\Program Files (x86)\Microsoft OneDrive\OneDrive.exe"
    )
    $oneDriveExe = $paths | Where-Object { Test-Path $_ } | Select-Object -First 1
}

# 2. Hervat de OneDrive synchronisatie
if ($oneDriveExe) {
    Start-Process -FilePath $oneDriveExe -ArgumentList "/unpause" -ErrorAction SilentlyContinue
    Write-Host "OneDrive synchronisatie succesvol hervat." -ForegroundColor Green
}
else {
    Write-Warning "Kon het OneDrive.exe bestand niet traceren om te hervatten."
}

# 3. Herstart de Windows Update Service
Start-Service -Name "wuauserv" -ErrorAction SilentlyContinue
Write-Host "Windows Update Service hervat." -ForegroundColor Green

# 4. Herstart Delivery Optimization
Start-Service -Name "dosvc" -ErrorAction SilentlyContinue
Write-Host "Delivery Optimization Service hervat." -ForegroundColor Green