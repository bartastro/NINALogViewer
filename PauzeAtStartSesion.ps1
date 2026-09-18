# ==============================================================================
# PRE-SESSION CHECK: Pending Reboot Controle
# ==============================================================================
$pendingReboot = $false

# Check 1: Component Based Servicing (CBS)
if (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending") { 
    $pendingReboot = $true 
}

# Check 2: Windows Update Auto Update
if (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired") { 
    $pendingReboot = $true 
}

# Check 3: Pending File Rename Operations (tijdelijke installatiebestanden)
$fileRename = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager" -Name "PendingFileRenameOperations" -ErrorAction SilentlyContinue
if ($null -ne $fileRename) { 
    $pendingReboot = $true 
}

# Afhandeling als er wél een reboot klaarstaat
if ($pendingReboot) {
    Write-Host ""
    Write-Host "==================================================================" -ForegroundColor Red
    Write-Host " WAARSCHUWING: Er staat nog een herstart klaar van een update!" -ForegroundColor Red
    Write-Host " Herstart de pc EERST handmatig voordat je de NINA-sessie start!" -ForegroundColor Red
    Write-Host "==================================================================" -ForegroundColor Red
    Write-Host ""
    
    # Maak een visuele Windows Pop-upmelding (vereist System.Windows.Forms)
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "LET OP: Er staat nog een herstart klaar van een update die overdag is gedownload.`n`nHerstart de pc EERST handmatig om te voorkomen dat Windows vannacht onverwacht herstart!", 
        "NINA Pre-Check: Pending Reboot Detected", 
        [System.Windows.Forms.MessageBoxButtons]::OK, 
        [System.Windows.Forms.MessageBoxIcon]::Warning
    )
} else {
    Write-Host "Geen Pending Reboot gevonden. Je kunt veilig starten met belichten!" -ForegroundColor Green
}

# ==============================================================================
# 1. ONEDRIVE PAUZEREN
# ==============================================================================
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

# ==============================================================================
# 2. WINDOWS SERVICES STOPPEN
# ==============================================================================
# Stop de Windows Update Service
Stop-Service -Name "wuauserv" -Force -ErrorAction SilentlyContinue

# Optioneel: Stop de Delivery Optimization Service (P2P updates op de achtergrond)
Stop-Service -Name "dosvc" -Force -ErrorAction SilentlyContinue