# --- CONFIGURATIE ---
$NinaLogFolder      = "$env:LOCALAPPDATA\NINA\Logs"
$MaxToegestaneTijd  = 25.0
$AantalRegels       = 30

# Link naar NINA-problems chat kanaal
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
$envPath = Join-Path -Path $scriptPath -ChildPath ".env-nina-groundstation"

if (Test-Path -Path $envPath) {
    Get-Content $envPath | ForEach-Object {
        $line = $_.Trim()
        # Sla lege regels en commentaar (#) over
        if ($line -and -not $line.StartsWith('#')) {
            $key, $value = $line.Split('=', 2)
            # Stel in als omgevingsvariabele in PowerShell
            [System.Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim(), "Process")
        }
    }
}

# Haal de omgevingsvariabele op (net als os.getenv() in Python)
$DiscordWebhookURL = $env:DISCORD_WEBHOOK_URL

if (-not $DiscordWebhookURL) {
    Write-Error "DISCORD_WEBHOOK_URL is niet ingesteld!"
    exit
}

# 1. Zoek het meest recente N.I.N.A. logbestand
$LatestLog = Get-ChildItem -Path $NinaLogFolder -Filter "*.log" -ErrorAction SilentlyContinue | 
             Sort-Object LastWriteTime -Descending | 
             Select-Object -First 1

if (-not $LatestLog) { Exit }

# 2. Lees de laatste regels veilig uit (werkt op PS 5.1 & 7+)
$FileStream   = [System.IO.File]::Open($LatestLog.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
$StreamReader = [System.IO.StreamReader]::new($FileStream)
$AllText      = $StreamReader.ReadToEnd()
$StreamReader.Close()
$FileStream.Close()

$LogLines = ($AllText -split "`r?\n") | Select-Object -Last $AantalRegels

# 3. Flexibele Regex voor N.I.N.A. logs
$ImageSaveRegex = '^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}).*?Successfully saved file at.*Duration Total: (\d+):(\d+):([\d\.]+);'

$TargetLine = $LogLines | Select-String -Pattern $ImageSaveRegex | Select-Object -Last 1

if ($TargetLine) {
    $Match = $TargetLine.Matches[0]
    
    $LogTimeStamp = [datetime]::Parse($Match.Groups[1].Value)
    $Nu           = Get-Date

    $OuderdomInSeconden = ($Nu - $LogTimeStamp).TotalSeconds

    # CHECK 1: Maximaal 90 seconden oud
    if ($OuderdomInSeconden -le 90) {

        # Reken de totale duur om naar seconden
        $Hours   = [double]$Match.Groups[2].Value
        $Minutes = [double]$Match.Groups[3].Value
        $Seconds = [double]$Match.Groups[4].Value
        $TotalDurationSeconds = ($Hours * 3600) + ($Minutes * 60) + $Seconds

        # CHECK 2: Overschrijdt drempelwaarde?
        if ($TotalDurationSeconds -gt $MaxToegestaneTijd) {
            
            $TijdstipFormatted = $LogTimeStamp.ToString("dd-MM-yyyy HH:mm:ss")
            $AfgerondeTijd     = [math]::Round($TotalDurationSeconds, 1)

            # Discord Embed opbouwen met PSCustomObject om single-element arrays af te dwingen
            $Payload = [PSCustomObject]@{
                embeds = @(
                    [PSCustomObject]@{
                        title       = "Trage N.I.N.A. Image Save!"
                        description = "Het verwerken/opslaan van het laatste FITS-bestand duurde te lang."
                        color       = 15105570
                        fields      = @(
                            [PSCustomObject]@{ name = "Totale Duur"; value = "$AfgerondeTijd s (Drempel: $MaxToegestaneTijd s)"; inline = $true },
                            [PSCustomObject]@{ name = "Tijdstip Frame"; value = "$TijdstipFormatted"; inline = $true },
                            [PSCustomObject]@{ name = "Systeem"; value = "$env:COMPUTERNAME"; inline = $true }
                        )
                        footer      = [PSCustomObject]@{ text = "N.I.N.A. Log Monitor - Task Scheduler" }
                    }
                )
            }

            # ConvertTo-Json strak instellen
            $BodyJson = $Payload | ConvertTo-Json -Depth 5 -Compress
            
            # Converteer de string naar UTF-8 bytes (voorkomt JSON format/encoding fouten in PS 5.1)
            $BodyBytes = [System.Text.Encoding]::UTF8.GetBytes($BodyJson)

            # Parameter-tabel voor Invoke-RestMethod
            $DiscordParams = @{
                Uri         = $DiscordWebhookURL
                Method      = 'Post'
                Body        = $BodyBytes
                ContentType = 'application/json; charset=utf-8'
            }

            Invoke-RestMethod @DiscordParams
        }
    }
}