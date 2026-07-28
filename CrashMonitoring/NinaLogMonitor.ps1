param (
    [Parameter(Mandatory = $false, Position = 0)]
    [string]$LogFilePath,

    [Parameter(Mandatory = $false)]
    [switch]$TestMode
)

# --- CONFIGURATIE ---
$NinaLogFolder     = "$env:LOCALAPPDATA\NINA\Logs"
$MaxToegestaneTijd = 25.0
$AantalRegels      = 40

# Link naar NINA-problems chat kanaal
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
$envPath    = Join-Path -Path $scriptPath -ChildPath ".env-nina-groundstation"

if (Test-Path -Path $envPath) {
    Get-Content $envPath | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#')) {
            $key, $value = $line.Split('=', 2)
            [System.Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim(), "Process")
        }
    }
}

$DiscordWebhookURL = $env:DISCORD_WEBHOOK_URL

if (-not $DiscordWebhookURL) {
    Write-Error "DISCORD_WEBHOOK_URL is niet ingesteld!"
    exit
}

# 1. Bepaal welk logbestand gelezen moet worden
if ($LogFilePath) {
    if (Test-Path -Path $LogFilePath) {
        $LatestLog   = Get-Item -Path $LogFilePath
        $LogPathText = $LatestLog.FullName
        Write-Host ("Testen met opgegeven logbestand: " + $LogPathText) -ForegroundColor Cyan
    } else {
        Write-Error ("Opgegeven bestand niet gevonden: " + $LogFilePath)
        exit
    }
} else {
    $LatestLog = Get-ChildItem -Path $NinaLogFolder -Filter "*.log" -ErrorAction SilentlyContinue | 
                 Sort-Object LastWriteTime -Descending | 
                 Select-Object -First 1
}

if (-not $LatestLog) { Exit }

# 2. Lees de laatste regels veilig uit
$FileStream   = [System.IO.File]::Open($LatestLog.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
$StreamReader = [System.IO.StreamReader]::new($FileStream)
$AllText      = $StreamReader.ReadToEnd()
$StreamReader.Close()
$FileStream.Close()

$LogLines = ($AllText -split "`r?\n") | Select-Object -Last $AantalRegels

# 3. REGEX PATTERNS
$ImageSaveRegex   = '^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}).*?Successfully saved file at.*Duration Total: (\d+):(\d+):([\d\.]+);'
$DeviceCycleRegex = '^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}).*?(\w+)\s+value update cycle took longer than the device poll interval \(Total: ([\d\.]+)s > ([\d\.]+)s'

$Nu = Get-Date

# Helper functie om Discord Webhook te sturen
function Send-DiscordNotification {
    param (
        [string]$Title,
        [string]$Description,
        [int]$Color,
        [array]$Fields,
        [string]$WebhookUrl
    )

    $Payload = [PSCustomObject]@{
        embeds = @(
            [PSCustomObject]@{
                title       = $Title
                description = $Description
                color       = $Color
                fields      = $Fields
                footer      = [PSCustomObject]@{ text = "N.I.N.A. Log Monitor - Task Scheduler" }
            }
        )
    }

    $BodyJson  = $Payload | ConvertTo-Json -Depth 5 -Compress
    $BodyBytes = [System.Text.Encoding]::UTF8.GetBytes($BodyJson)

    $DiscordParams = @{
        Uri         = $WebhookUrl
        Method      = 'Post'
        Body        = $BodyBytes
        ContentType = 'application/json; charset=utf-8'
    }

    Invoke-RestMethod @DiscordParams
}

# ==========================================
# SCENARIO A: TRAGE IMAGE SAVE
# ==========================================
$TargetSaveLine = $LogLines | Select-String -Pattern $ImageSaveRegex | Select-Object -Last 1

if ($TargetSaveLine) {
    $Match              = $TargetSaveLine.Matches[0]
    $LogTimeStamp       = [datetime]::Parse($Match.Groups[1].Value)
    $OuderdomInSeconden = ($Nu - $LogTimeStamp).TotalSeconds

    if ($TestMode -or ($OuderdomInSeconden -le 90)) {
        $Hours                = [double]$Match.Groups[2].Value
        $Minutes              = [double]$Match.Groups[3].Value
        $Seconds              = [double]$Match.Groups[4].Value
        $TotalDurationSeconds = ($Hours * 3600) + ($Minutes * 60) + $Seconds

        if ($TotalDurationSeconds -gt $MaxToegestaneTijd) {
            $TijdstipFormatted = $LogTimeStamp.ToString("dd-MM-yyyy HH:mm:ss")
            $AfgerondeTijd     = [math]::Round($TotalDurationSeconds, 1)

            $Fields = @(
                [PSCustomObject]@{ name = "Totale Duur"; value = [string]$AfgerondeTijd + " s (Drempel: " + [string]$MaxToegestaneTijd + " s)"; inline = $true },
                [PSCustomObject]@{ name = "Tijdstip Frame"; value = [string]$TijdstipFormatted; inline = $true },
                [PSCustomObject]@{ name = "Systeem"; value = [string]$env:COMPUTERNAME; inline = $true }
            )

            $NotificationParams = @{
                Title       = "Trage N.I.N.A. Image Save!"
                Description = "Het verwerken/opslaan van het laatste FITS-bestand duurde te lang."
                Color       = 15105570
                Fields      = $Fields
                WebhookUrl  = $DiscordWebhookURL
            }

            Send-DiscordNotification @NotificationParams

            $Msg = "Discord melding verstuurd voor Trage Image Save - " + $AfgerondeTijd + "s"
            Write-Host $Msg -ForegroundColor Green
        }
    }
}

# ==========================================
# SCENARIO B: TRAGE DEVICE VALUE UPDATE CYCLE
# ==========================================
$TargetCycleLine = $LogLines | Select-String -Pattern $DeviceCycleRegex | Select-Object -Last 1

if ($TargetCycleLine) {
    $MatchCycle         = $TargetCycleLine.Matches[0]
    $CycleTimeStamp     = [datetime]::Parse($MatchCycle.Groups[1].Value)
    $OuderdomInSeconden = ($Nu - $CycleTimeStamp).TotalSeconds

    if ($TestMode -or ($OuderdomInSeconden -le 90)) {
        $DeviceType     = $MatchCycle.Groups[2].Value
        $TotalCycleTime = [double]$MatchCycle.Groups[3].Value
        $PollInterval   = [double]$MatchCycle.Groups[4].Value

        $TijdstipFormatted = $CycleTimeStamp.ToString("dd-MM-yyyy HH:mm:ss")
        $AfgerondeTijd     = [math]::Round($TotalCycleTime, 1)

        $Fields = @(
            [PSCustomObject]@{ name = "Apparaat"; value = [string]$DeviceType; inline = $true },
            [PSCustomObject]@{ name = "Totale Cycle Duur"; value = [string]$AfgerondeTijd + " s"; inline = $true },
            [PSCustomObject]@{ name = "Poll Interval"; value = [string]$PollInterval + " s"; inline = $true },
            [PSCustomObject]@{ name = "Tijdstip Melding"; value = [string]$TijdstipFormatted; inline = $true },
            [PSCustomObject]@{ name = "Systeem"; value = [string]$env:COMPUTERNAME; inline = $true }
        )

        $DiscordTitle       = "N.I.N.A. Device Update Lag - " + $DeviceType
        $DiscordDescription = "De value update cycle van **" + $DeviceType + "** duurde aanzienlijk langer dan de poll interval."

        $NotificationParams = @{
            Title       = $DiscordTitle
            Description = $DiscordDescription
            Color       = 15158332
            Fields      = $Fields
            WebhookUrl  = $DiscordWebhookURL
        }

        Send-DiscordNotification @NotificationParams

        $Msg = "Discord melding verstuurd voor Device Update Lag - " + $DeviceType + ": " + $AfgerondeTijd + "s"
        Write-Host $Msg -ForegroundColor Green
    }
}