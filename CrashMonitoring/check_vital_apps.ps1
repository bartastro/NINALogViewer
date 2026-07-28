param (
    [Parameter(Mandatory = $false)]
    [switch]$TestMode  # Negeert de 90-seconden limiet voor testdoeleinden
)

# --- CONFIGURATIE ---
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
$envPath = Join-Path -Path $scriptPath -ChildPath ".env-astrocrash"

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

# --- APPLICATIE DEFINITIES ---
# Koppel de procesnamen/zoektermen aan de weergavenamen
$MonitoredApps = @(
    @{ Name = "N.I.N.A."; Pattern = "*NINA.exe*" },
    @{ Name = "PHD2 Guiding"; Pattern = "*phd2.exe*" },
    @{ Name = "Pegasus Unity 3"; Pattern = "*Peg.UI*" }
)

# Zoek Event ID 1000 (Application Error) in de Application log
$WinEventArgs = @{
    FilterHashtable = @{ LogName = 'Application'; ID = 1000; Level = 2 }
    MaxEvents       = 5  # Bekijk de laatste 5 events om te zorgen dat we niks missen
    ErrorAction     = 'SilentlyContinue'
}

$CrashEvents = Get-WinEvent @WinEventArgs
if (-not $CrashEvents) { exit }

$Nu = Get-Date

foreach ($CrashEvent in $CrashEvents) {
    # 1. Controleer de ouderdom van het event (max 90 sec oud, tenzij TestMode actief is)
    $OuderdomInSeconden = ($Nu - $CrashEvent.TimeCreated).TotalSeconds
    
    if ($TestMode -or ($OuderdomInSeconden -le 90)) {
        $EventMessage = $CrashEvent.Message

        foreach ($App in $MonitoredApps) {
            # Test-override trigger (bijv. via werfault)
            $IsTestTrigger = ($EventMessage -like "*werfault.exe*")

            if (($EventMessage -like $App.Pattern) -or $IsTestTrigger) {
                
                $TijdstipFormatted = $CrashEvent.TimeCreated.ToString("dd-MM-yyyy HH:mm:ss")
                
                if ($IsTestTrigger) {
                    $Titel = "[TEST] " + $App.Name + " detector OK"
                    $Description = "Test-melding: WerFault getriggerd voor " + $App.Name
                    $Color = 3066993  # Groen
                }
                else {
                    $Titel = "CRASH: " + $App.Name + " is vastgelopen!"
                    $Description = $App.Name + " is onverwacht gestopt (Event ID 1000)."
                    $Color = 15158332 # Rood
                }

                $Fields = @(
                    [PSCustomObject]@{ name = "Applicatie"; value = $App.Name; inline = $true },
                    [PSCustomObject]@{ name = "Tijdstip"; value = $TijdstipFormatted; inline = $true },
                    [PSCustomObject]@{ name = "Systeem"; value = $env:COMPUTERNAME; inline = $true }
                )

                # Webhook Payload opbouwen
                $Payload = [PSCustomObject]@{
                    embeds = @(
                        [PSCustomObject]@{
                            title       = $Titel
                            description = $Description
                            color       = $Color
                            fields      = $Fields
                            footer      = [PSCustomObject]@{ text = "Windows Event Viewer - Task Scheduler" }
                        }
                    )
                }

                $BodyJson = $Payload | ConvertTo-Json -Depth 4 -Compress
                $BodyBytes = [System.Text.Encoding]::UTF8.GetBytes($BodyJson)

                $DiscordParams = @{
                    Uri         = $DiscordWebhookURL
                    Method      = 'Post'
                    Body        = $BodyBytes
                    ContentType = 'application/json; charset=utf-8'
                }

                Invoke-RestMethod @DiscordParams
                Write-Host ("[OK] Discord crash melding verstuurd voor: " + $App.Name) -ForegroundColor Green

                # Zodra deze specifieke event verwerkt is, stoppen we de inner loop voor dit event
                break
            }
        }
    }
}