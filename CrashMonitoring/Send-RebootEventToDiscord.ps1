# ==============================================================================
# CONFIGURATIE
# ==============================================================================
$pcName = $env:COMPUTERNAME
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


# ==============================================================================
# EVENT DETAILS OPVRAGEN (EVENT ID 1074)
# ==============================================================================
# Haal het allernieuwste User32 Event 1074 op uit de System log
$event = Get-WinEvent -FilterHashtable @{
    LogName      = 'System'
    ProviderName = 'User32'
    Id           = 1074
} -MaxEvents 1 -ErrorAction SilentlyContinue

if ($null -ne $event) {
    $eventTime = $event.TimeCreated.ToString("dd-MM-yyyy HH:mm:ss")
    $eventMessage = $event.Message

    # Standaard payload opbouwen voor Discord
    $payload = @{
        username   = "Astro-PC Shutdown Monitor"
        embeds     = @(
            @{
                title       = "WAARSCHUWING: Systeemherstart/Afsluiting Gestart!"
                description = "Windows heeft zojuist een herstart of afsluiting in gang gezet. Actieve processen (zoals NINA) worden afgesloten."
                color       = 16738657 # Oranje/Rood
                fields      = @(
                    @{
                        name   = "🖥️ Systeem"
                        value  = $pcName
                        inline = $true
                    },
                    @{
                        name   = "⏰ Tijdstip"
                        value  = $eventTime
                        inline = $true
                    },
                    @{
                        name   = "📋 Event Details"
                        value  = "````n" + $eventMessage + "`n```"
                        inline = $false
                    }
                )
                footer      = @{
                    text = "Event ID 1074 Trigger"
                }
            }
        )
    } | ConvertTo-Json -Depth 4

    # Versturen naar Discord
    try {
        Invoke-RestMethod -Uri $DiscordWebhookURL -Method Post -Body $payload -ContentType "application/json"
    } catch {
        # Mocht de netwerkadapter al afgesloten zijn vóór de verzending, schrijf een lokale fallback log
        Add-Content -Path "C:\Scripts\reboot_event_error.log" -Value "$(Get-Date): Fout bij versturen naar Discord - $_"
    }
}