# --- CONFIGURATIE ---
# Haal setting uit env

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
$envPath = Join-Path -Path $scriptPath -ChildPath ".env-astrocrash"

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

# Zoek de meest recente crash (Event ID 1000) in de Application logs
$WinEventArgs = @{
    FilterHashtable = @{LogName='Application'; ID=1000; Level=2}
    MaxEvents       = 1
    ErrorAction     = 'SilentlyContinue'
}

$CrashEvent = Get-WinEvent @WinEventArgs

if ($CrashEvent) {
    $EventMessage = $CrashEvent.Message
   
    # Controleer of Pegasus Unity de dader was
    if ($EventMessage -like "*Peg.UI*") {
       
        # Tijdstip mooi formatteren
        $Tijdstip = $CrashEvent.TimeCreated.ToString("dd-MM-yyyy HH:mm:ss")
		
		$Titel = "!! Pegasus Unity 3 crash gedetecteerd !!"

		if ($EventMessage -like "*werfault.exe") { $Titel = "Peg.UI detector -- Test OK"}
		
        # Discord Embed JSON structuur opbouwen
        $Body = @{
            embeds = @(
                @{
                    title       = $Titel
                    description = "Pegasus Unity 3 is zojuist onverwacht gecrasht."
					# Dit is een rode kleur of een groene (Hex #E74C3C omgezet naar Decimaal)
                    color       = if ($Titel -like "*Test*") { 3066993 } else { 15158332 }
                    fields      = @(
                        @{
                            name  = "Tijdstip"
                            value = $Tijdstip
                            inline = $true
                        },
                        @{
                            name  = "Systeem"
                            value = $env:COMPUTERNAME
                            inline = $true
                        }
                    )
                    footer      = @{
                        text = "Windows Event Viewer - ID 1000"
                    }
                }
            )
        } | ConvertTo-Json -Depth 4

        # Bericht naar Discord schieten
        $DiscordParams = @{
			Uri         = $DiscordWebhookURL
			Method      = 'Post'
			Body        = $Body
			ContentType = 'application/json'
		}

		# 3. Voer het commando uit met de @-notatie
		Invoke-RestMethod @DiscordParams
    }
} else {
	Exit
}
