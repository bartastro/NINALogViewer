# N.I.N.A. Active Log Monitor (`NinaLogMonitor.ps1`) Documentation

The `NinaLogMonitor.ps1` script is a PowerShell-based background monitor designed to run periodically (e.g., via Windows Task Scheduler) to detect operational warnings in N.I.N.A. logs in real-time. It sends immediate alerts to a Discord channel using webhooks when slow image saves or device driver lags are detected.

---

## Table of Contents
1. [Key Features](#1-key-features)
2. [Configuration Settings](#2-configuration-settings)
3. [Environment Configuration & Discord Integration](#3-environment-configuration--discord-integration)
4. [Log Parsing & Scenario Logic](#4-log-parsing--scenario-logic)
   - [Scenario A: Slow Image Save](#scenario-a-slow-image-save)
   - [Scenario B: Device Value Update Lag](#scenario-b-device-value-update-lag)
5. [Arguments & Modes](#5-arguments--modes)
6. [Deployment with Windows Task Scheduler](#6-deployment-with-windows-task-scheduler)

---

## 1. Key Features

- **Real-Time Monitoring**: Tailored to check the newest logs immediately after they are written, scanning only the last few lines for high-priority operational issues.
- **Robust File Reading**: Opens active log files using a .NET FileStream (`[System.IO.File]::Open`) with `FileShare.ReadWrite` permissions. This ensures the script can read the log even if N.I.N.A. is actively writing to it, preventing lock conflicts.
- **Discord Alert Integration**: Sends rich embed notifications including formatting, color-coded levels, execution times, system names, and exact log timestamps.
- **Two Critical Diagnostic Scenarios**:
  1. Detects image save delays exceeding a configurable timeout (e.g., due to slow network shares, slow USB ports, or external SSD lag).
  2. Detects driver update loops that overrun their polling budgets, identifying hardware drivers that might be hanging or slow to respond.

---

## 2. Configuration Settings

The top of the script defines several baseline configurations:

| Parameter / Variable | Default Value | Description |
| :--- | :--- | :--- |
| `$NinaLogFolder` | `"$env:LOCALAPPDATA\NINA\Logs"` | Default directory where N.I.N.A. writes session log files. |
| `$MaxToegestaneTijd` | `25.0` | Threshold (in seconds) for image saving. Save durations exceeding this will trigger a Discord alert. |
| `$AantalRegels` | `40` | Number of lines read from the end of the file during standard execution (prevents processing lag). |
| `$MaxOuderdom` | `90` | Maximum age (in seconds) of a log entry to be considered "recent". Prevents duplicate alerts for historical events. |

---

## 3. Environment Configuration & Discord Integration

### Environment Variables (`.env-nina-groundstation`)
The script searches for a file named `.env-nina-groundstation` in its parent directory. If found, it parses and sets system environment variables for the current process:
```powershell
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
$envPath = Join-Path -Path $scriptPath -ChildPath ".env-nina-groundstation"
```
It reads `DISCORD_WEBHOOK_URL` from this file to configure the destination channel.

### Discord Webhook Dispatcher
The inner function `Send-DiscordNotification` converts a PowerShell custom object payload into compressed JSON and posts it to Discord:
```powershell
Invoke-RestMethod -Uri $WebhookUrl -Method 'Post' -Body $BodyBytes -ContentType 'application/json; charset=utf-8'
```
The payload is built using standard Discord webhook embeds containing:
- **Title** and **Description**
- **Color**: Represented as a decimal integer (e.g., `15105570` for orange/yellow warnings).
- **Fields**: Inline tables detailing durations, intervals, host systems, and timestamps.
- **Footer**: Identifies the source program (`N.I.N.A. Log Monitor - Task Scheduler`).

---

## 4. Log Parsing & Scenario Logic

### Scenario A: Slow Image Save
The monitor checks for successful write notifications from N.I.N.A.'s image saving controller using this regular expression:
```powershell
$ImageSaveRegex = '^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}).*?Successfully saved file at.*Duration Total: (\d+):(\d+):([\d\.]+);'
```
- **Execution Logic**:
  1. Searches for the most recent match in the parsed lines.
  2. Computes the age of the log entry. If the entry is older than `$MaxOuderdom` (90s), it is ignored.
  3. Converts N.I.N.A.'s timestamp duration (`HH:MM:SS.fff`) into total seconds:
     $$\text{Total Duration} = (\text{Hours} \times 3600) + (\text{Minutes} \times 60) + \text{Seconds}$$
  4. If the duration exceeds `$MaxToegestaneTijd` (25s), it triggers a Discord warning alert with the total duration, frame timestamp, and host machine name.

### Scenario B: Device Value Update Lag
Astronomy hardware drivers (ASCOM/native) that fail to update within their scheduled polling window trigger warnings in N.I.N.A. logs:
```powershell
$DeviceCycleRegex = '^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}).*?(\w+)\s+value update cycle took longer than the device poll interval \(Total: ([\d\.]+)s\s*>\s*([\d\.]+)s'
```
- **Execution Logic**:
  1. Finds the last occurrence of the warning.
  2. If the entry is recent (age $\le$ `$MaxOuderdom`), it extracts the device type, total duration, and the driver's configured poll interval.
  3. Sends a Discord alert indicating that the hardware driver is causing delays in the primary polling thread.

---

## 5. Arguments & Modes

The script supports parameters to customize its execution profile:

```powershell
param (
    [string]$LogFilePath,
    [switch]$TestMode
)
```

### A. Default Mode
Runs without parameters. It locates the newest `*.log` file in the N.I.N.A. logs folder, reads the last 40 lines, and evaluates matching entries. It only alerts on warnings less than 90 seconds old.

### B. Specific File Mode (`-LogFilePath`)
Directs the script to monitor a specific log file instead of auto-detecting the newest one:
```powershell
.\NinaLogMonitor.ps1 -LogFilePath "C:\Users\User\AppData\Local\NINA\Logs\20260806-120000.log"
```

### C. Test Mode (`-TestMode`)
Overrides safeguards for manual verification and testing:
- Bypasses the 40-line tail constraint, scanning the **entire file** from the beginning.
- Extends the maximum allowed warning age to **1 day** (86,400 seconds), forcing historical warnings to trigger.
- Prints console logging detailing matches or lack thereof.
```powershell
.\NinaLogMonitor.ps1 -TestMode
```

---

## 6. Deployment with Windows Task Scheduler

To monitor N.I.N.A. continuously during imaging sessions, configure a Windows Task Scheduler task with the following properties:

1. **Trigger**: Run daily, repeating every **1 minute** indefinitely.
2. **Action**: Start a Program.
   - **Program/Script**: `powershell.exe`
   - **Arguments**: `-NoProfile -WindowStyle Hidden -File "C:\Path\To\CrashMonitoring\NinaLogMonitor.ps1"`
3. **Conditions**: Uncheck "Start the task only if the computer is on AC power" if you operate on mobile battery power in the field.
