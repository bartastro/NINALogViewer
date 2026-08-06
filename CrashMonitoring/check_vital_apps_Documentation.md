# Vital Apps Crash Monitor (`check_vital_apps.ps1`) Documentation

The `check_vital_apps.ps1` script is a PowerShell-based background utility designed to run periodically (e.g., via Windows Task Scheduler) to monitor the health of vital astronomy applications. It scans the Windows Application Event log for crash events (Event ID 1000) and immediately dispatches alerts to a Discord channel via webhooks if any monitored app crashes.

---

## Table of Contents
1. [Key Features](#1-key-features)
2. [Monitored Applications](#2-monitored-applications)
3. [Configuration & Environment Setup](#3-configuration--environment-setup)
4. [Windows Event Log Auditing Logic](#4-windows-event-log-auditing-logic)
5. [Discord Alert Formatting](#5-discord-alert-formatting)
6. [Testing Mode](#6-testing-mode)
7. [Deployment with Windows Task Scheduler](#7-deployment-with-windows-task-scheduler)

---

## 1. Key Features

- **Automated Crash Detection**: Continuously audits Windows Application logs for Event ID 1000 (Application Errors) to detect sudden software failures.
- **Astronomy Stack Monitoring**: Tailored to track core software in the astrophotography ecosystem, ensuring that if guiding, acquisition, or power hub software fails, the operator is notified immediately.
- **Discord Integration**: Sends rich embed notifications featuring status colors (Red for real crashes, Green for testing verifications), computer hostnames, and time stamps.
- **Smart Filtering**: Evaluates only recent events (under 90 seconds old by default) to prevent duplicate notifications for historical crashes.

---

## 2. Monitored Applications

The script targets three critical programs defined in the `$MonitoredApps` array:

| Application Name | Executable Pattern / Match | Description |
| :--- | :--- | :--- |
| **N.I.N.A.** | `*NINA.exe*` | Primary sequencing and camera acquisition software. |
| **PHD2 Guiding** | `*phd2.exe*` | Telescope autoguiding software. |
| **Pegasus Unity 3** | `*Peg.UI*` | Power box and accessory controller software. |

---

## 3. Configuration & Environment Setup

### Environment Variable Loading (`.env-astrocrash`)
Before executing main loops, the script reads a `.env-astrocrash` file located in its script directory:
```powershell
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Definition
$envPath = Join-Path -Path $scriptPath -ChildPath ".env-astrocrash"
```
It extracts the `DISCORD_WEBHOOK_URL` to authenticate and target the webhook API channel.

---

## 4. Windows Event Log Auditing Logic

1. **Querying Application Log**: The script fetches the last 5 events matching Application Error 1000 from the Event Viewer:
   ```powershell
   $WinEventArgs = @{
       FilterHashtable = @{ LogName = 'Application'; ID = 1000; Level = 2 }
       MaxEvents       = 5
       ErrorAction     = 'SilentlyContinue'
   }
   $CrashEvents = Get-WinEvent @WinEventArgs
   ```
2. **Age Thresholding**: For each crash event, the script checks the difference between the current time and the event generation time:
   $$\text{Ouderdom} = \text{Current Time} - \text{Event TimeCreated}$$
   If this value is greater than **90 seconds**, the event is skipped to prevent spamming alerts for past failures.
3. **Application Verification**: The script checks if the event description message (`$CrashEvent.Message`) contains any of the defined patterns (e.g., `*NINA.exe*`, `*phd2.exe*`, or `*Peg.UI*`).

---

## 5. Discord Alert Formatting

When a crash is detected, the script builds a JSON payload for Discord. The colors are hex values converted to decimal:

- **Crash Alert (Red / `15158332`)**:
  - **Title**: `CRASH: <AppName> is vastgelopen!`
  - **Description**: `<AppName> is onverwacht gestopt (Event ID 1000).`
- **Test Alert (Green / `3066993`)**:
  - **Title**: `[TEST] <AppName> detector OK`
  - **Description**: `Test-melding: WerFault getriggerd voor <AppName>`

### Field Parameters
Every notification includes a table displaying:
- **Applicatie**: The target app name.
- **Tijdstip**: Time of the crash formatted as `dd-MM-yyyy HH:mm:ss`.
- **Systeem**: Hostname of the imaging PC (`$env:COMPUTERNAME`).

---

## 6. Testing Mode

The script supports a `-TestMode` switch parameter to simplify local configuration testing:

```powershell
.\check_vital_apps.ps1 -TestMode
```

### Behaviors in Test Mode:
- **Age Filter Bypass**: Overrides the 90-second age restriction, allowing the script to inspect the 5 most recent application crashes regardless of when they occurred.
- **WerFault Triggering**: If a crash event message contains `werfault.exe` (Windows Error Reporting), it triggers a green **[TEST]** success webhook, validating that the monitoring agent can read events, match patterns, and dispatch webhooks to Discord.

---

## 7. Deployment with Windows Task Scheduler

Rather than polling at a set interval, the monitoring system is configured as an **event-driven (push-based) task** that runs instantly when Windows logs an application error. Below are the verified configurations from the scheduled task `Astro APP monitoring`:

### Task Metadata
* **Task Name**: `Astro APP monitoring`
* **Path**: `\` (Root Folder)
* **Execution Privileges**: Run with highest privileges (`Highest`)
* **Security Context**: Executed under user account `barta`

### Trigger (Event Log Subscription)
The task triggers automatically when Event Viewer logs a crash matching **Event ID 1000** (Application Error) with **Level 2** (Error) in the Application channel.
- **Log Name**: `Application`
- **XML Query Filter**:
  ```xml
  <QueryList>
      <Query Id="0" Path="Application">
          <Select Path="Application">*[System[(EventID=1000) and (Level=2)]]</Select>
      </Query>
  </QueryList>
  ```

### Action (Start a Program)
- **Program/Script**: `powershell.exe`
- **Arguments**: 
  ```text
  -NoProfile -ExecutionPolicy Bypass -File "C:\Users\barta\Documents\Python\Astro tools\NINAlog\CrashMonitoring\check_vital_apps.ps1"
  ```
- **Execution Advantage**: By coupling the Event Log trigger with `-ExecutionPolicy Bypass`, the script executes instantaneously when an application crashes, providing real-time Discord notifications without CPU polling overhead.

