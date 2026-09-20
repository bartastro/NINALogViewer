# N.I.N.A. & Astrophotography Crash & Performance Monitoring

A comprehensive suite of PowerShell automation scripts designed for unattended, remote, and automated astrophotography imaging rigs. This suite provides real-time failure detection, driver lag diagnostics, image-save latency alerts, and unexpected system shutdown warnings, delivering instant rich embed notifications directly to **Discord** via webhooks.

---

## Table of Contents
1. [Overview & Architecture](#1-overview--architecture)
2. [PowerShell Scripts Summary](#2-powershell-scripts-summary)
3. [Script Details & Functionality](#3-script-details--functionality)
   - [NinaLogMonitor.ps1](#ninalogmonitorps1)
   - [check_vital_apps.ps1](#check_vital_appsps1)
   - [Send-RebootEventToDiscord.ps1](#send-rebooteventtodiscordps1)
   - [check_nina_crash.ps1 & Template](#check_nina_crashps1--template)
   - [enableMonitor.ps1](#enablemonitorps1)
4. [Environment & Webhook Configuration](#4-environment--webhook-configuration)
5. [Windows Task Scheduler Deployment](#5-windows-task-scheduler-deployment)
6. [Testing & Diagnostic Modes](#6-testing--diagnostic-modes)
7. [Reference Documentation & Assets](#7-reference-documentation--assets)

---

## 1. Overview & Architecture

When running automated imaging sessions overnight, software failures, hardware driver deadlocks, or unintended Windows reboots can ruin hours of acquisition time or even put equipment at risk. 

This monitoring toolkit addresses those challenges through a dual-layer approach:
1. **Event-Driven Push Monitoring**: Listens to Windows Event Viewer channels (Application and System logs) and triggers instantly when critical errors (Event ID 1000) or system reboot requests (Event ID 1074) occur.
2. **Active Telemetry Polling**: Safely reads active N.I.N.A. log files in non-blocking mode (`FileShare.ReadWrite`) to intercept operational warnings - such as ASCOM driver poll overruns and slow FITS/XISF image saves - before complete system hangs occur.

```
+-----------------------------------------------------------------------------------+
|                           Remote Imaging PC                                       |
|                                                                                   |
|  +---------------------------+       +-----------------------------------------+  |
|  | Windows Event Viewer      |       | Active N.I.N.A. Log                     |  |
|  | - Event 1000 (App Crash)  |       | - Save duration (> 25s)                 |  |
|  | - Event 1074 (Reboot)     |       | - Driver poll lag (> poll interval)     |  |
|  +-------------+-------------+       +--------------------+--------------------+  |
|                |                                          |                       |
|                v                                          v                       |
|  +---------------------------+       +-----------------------------------------+  |
|  | check_vital_apps.ps1      |       | NinaLogMonitor.ps1                      |  |
|  | Send-RebootEventToDiscord |       | (Scheduled Task: 1-min interval)        |  |
|  +-------------+-------------+       +--------------------+--------------------+  |
|                |                                          |                       |
|                +-------------------+  +-------------------+                       |
|                                    |  |                                           |
+------------------------------------|--|-------------------------------------------+
                                     v  v
                         +--------------------------+
                         |     Discord Webhooks     |
                         |  - Real-time Rich Embeds |
                         |  - Operator Alerts       |
                         +--------------------------+
```

---

## 2. PowerShell Scripts Summary

| Script | Primary Function | Trigger Mechanism | Target / Source | Config / Webhook File |
| :--- | :--- | :--- | :--- | :--- |
| [`NinaLogMonitor.ps1`](./NinaLogMonitor.ps1) | Detects slow image writes (>25s) and hardware driver poll lags | Scheduled Task (repeats every 1 min) or Manual | Latest N.I.N.A. log file | [`.env-nina-groundstation`](./.env-nina-groundstation) |
| [`check_vital_apps.ps1`](./check_vital_apps.ps1) | Detects crashes across the entire imaging stack (NINA, PHD2, Pegasus Unity) | Event-Driven (Windows Event ID 1000) | Windows Application Event Log | [`.env-astrocrash`](./.env-astrocrash) |
| [`Send-RebootEventToDiscord.ps1`](./Send-RebootEventToDiscord.ps1) | Intercepts reboot or shutdown commands and warns operator | Event-Driven (Windows Event ID 1074) | Windows System Event Log (`User32`) | [`.env-astrocrash`](./.env-astrocrash) |

---

## 3. Script Details & Functionality

### `NinaLogMonitor.ps1`
* **Full Documentation**: [NinaLogMonitor_Documentation.md](./NinaLogMonitor_Documentation.md)
* **Purpose**: Inspects the active N.I.N.A. session log for performance degradation and driver latency.
* **Key Functionality**:
  - **Lock-Free Reading**: Opens the currently writing log file via .NET `[System.IO.File]::Open` using `FileMode.Open`, `FileAccess.Read`, and `FileShare.ReadWrite`. This ensures zero locking conflicts with N.I.N.A.
  - **Scenario A (Slow Image Saves)**: Scans for image save completion entries. If the calculated duration exceeds `$MaxToegestaneTijd` (default: **25.0 seconds**), an alert is dispatched with the duration, timestamp, and host machine name.
  - **Scenario B (Device Update Cycle Overruns)**: Detects ASCOM and native driver cycle lags (`value update cycle took longer than the device poll interval`), alerting when a hardware driver hangs or exceeds its polling window.
  - **Recency Safeguard**: Filters out entries older than `$MaxOuderdom` (default: **90 seconds**) during normal operation to prevent duplicate alerts.
* **Command Syntax**:
  ```powershell
  # Standard execution (reads last 40 lines of newest log, 90s age threshold)
  .\NinaLogMonitor.ps1

  # Test mode (inspects entire log, expands age threshold to 24h)
  .\NinaLogMonitor.ps1 -TestMode

  # Target a specific log file
  .\NinaLogMonitor.ps1 -LogFilePath "$env:LOCALAPPDATA\NINA\Logs\20260920-010203.log"
  ```

---

### `check_vital_apps.ps1`
* **Full Documentation**: [check_vital_apps_Documentation.md](./check_vital_apps_Documentation.md)
* **Purpose**: Multi-application crash sentry for the core astrophotography suite.
* **Monitored Applications**:
  - **N.I.N.A.** (`*NINA.exe*`): Sequencing and camera acquisition.
  - **PHD2 Guiding** (`*phd2.exe*`): Telescope autoguiding.
  - **Pegasus Unity 3** (`*Peg.UI*`): Power box, dew heaters, and sensor telemetry.
* **Key Functionality**:
  - Queries the 5 most recent Event ID 1000 entries from the Windows `Application` log.
  - Matches process names against `$MonitoredApps`.
  - Sends a color-coded Discord notification (Red for real crashes, Green for `werfault.exe` test notifications).
  - Skips events older than 90 seconds to prevent alert spamming.
* **Command Syntax**:
  ```powershell
  # Standard execution
  .\check_vital_apps.ps1

  # Test mode (bypasses 90s age filter, tests WerFault patterns)
  .\check_vital_apps.ps1 -TestMode
  ```

---

### `Send-RebootEventToDiscord.ps1`
* **Purpose**: Early-warning notification when Windows initiates a system reboot or shutdown.
* **Key Functionality**:
  - Monitors the Windows `System` event log for Event ID 1074 from provider `User32`.
  - Extracts the reboot timestamp, initiator process, reason code, and user/system details.
  - Immediately dispatches a high-priority warning embed to Discord informing the operator that active processes (including N.I.N.A.) are being closed.
  - **Network Disconnect Fallback**: If the network adapter shuts down before the webhook call completes, writes a local failure log to `C:\Scripts\reboot_event_error.log`.
* **Command Syntax**:
  ```powershell
  .\Send-RebootEventToDiscord.ps1
  ```

---

## 4. Environment & Webhook Configuration

The scripts use dedicated `.env` configuration files to keep Discord webhook URLs externalized from the script logic:

| Config File | Used By | Intended Discord Target |
| :--- | :--- | :--- |
| [`.env-astrocrash`](./.env-astrocrash) | `check_vital_apps.ps1`, `Send-RebootEventToDiscord.ps1`, `check_nina_crash.ps1` | Critical alerts channel (app crashes, unexpected reboots) |
| [`.env-nina-groundstation`](./.env-nina-groundstation) | `NinaLogMonitor.ps1` | Operational telemetry channel (slow image saves, driver lag warnings) |

### Environment File Syntax
Each file follows standard `KEY=VALUE` formatting:
```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN
```

> [!NOTE]
> Separating the webhooks into two files allows routing critical crash events to high-priority alert channels while directing non-fatal operational telemetry to a dedicated monitoring feed.

---

## 5. Windows Task Scheduler Deployment

The monitoring suite is designed for automated deployment through two scheduled task designs:

### A. Event-Driven Tasks (Instant Execution)
* **Tasks**: `Astro APP monitoring` and `Astro Reboot Monitor`
* **Trigger Type**: Windows Event Log Subscription
  - **App Crash**: `Log: Application`, `Event ID: 1000`, `Level: 2` (Error)
  - **System Reboot**: `Log: System`, `Event ID: 1074`, `Provider: User32`
* **Action Settings**:
  - **Program**: `powershell.exe`
  - **Arguments**:
    ```text
    -NoProfile -ExecutionPolicy Bypass -File "C:\<PathToScripts>\check_vital_apps.ps1"
    ```
* **Privilege Level**: Run with highest privileges (`Highest`) under the user account.

### B. Repeating Polling Task (Telemetry)
* **Task Name**: `NINA_SaveTime_Monitor`
* **Trigger Type**: User Logon Trigger (`MSFT_TaskLogonTrigger`)
* **Repetition**: Repeats indefinitely every **1 minute** (`PT1M`)
* **Action Settings**:
  - **Program**: `powershell.exe`
  - **Arguments**:
    ```text
    -NoProfile -ExecutionPolicy Bypass -File "C:\<PathToScripts>\NinaLogMonitor.ps1"
    ```
* **Privilege Level**: Run with standard user privileges (`Limited`).

---

## 6. Testing & Diagnostic Modes

To verify Discord integration without having to wait for a crash or slow frame:

1. **Test `check_vital_apps.ps1`**:
   ```powershell
   powershell.exe -ExecutionPolicy Bypass -File ".\check_vital_apps.ps1" -TestMode
   ```
   *Scans the last 5 application error events ignoring age restrictions, and validates webhook transmission with green `[TEST]` status banners if Windows Error Reporting entries are present.*

2. **Test `NinaLogMonitor.ps1`**:
   ```powershell
   powershell.exe -ExecutionPolicy Bypass -File ".\NinaLogMonitor.ps1" -TestMode
   ```
   *Scans the full length of the newest N.I.N.A. log file with a 24-hour age window, outputting regex match results directly to the console.*

---

## 7. Reference Documentation & Assets

- [NinaLogMonitor_Documentation.md](./NinaLogMonitor_Documentation.md) - Detailed regex patterns, duration formulas, and setup steps for log monitoring.
- [check_vital_apps_Documentation.md](./check_vital_apps_Documentation.md) - Event log XML filters, app definitions, and Discord payload schemas.
- [AppMonitoring.pdf](./AppMonitoring.pdf) / [AppMonitoring.docx](./AppMonitoring.docx) - Comprehensive setup guide and operational handbook.
