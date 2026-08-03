from datetime import datetime
import os
import subprocess
import tempfile
import json
import sys
import re
import numpy as np
import traceback
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PyQt5.QtWidgets import (QApplication, QWidget, QMainWindow, QPushButton, 
                             QFileDialog, QTextEdit, QVBoxLayout, QHBoxLayout, 
                             QLabel, QProgressBar, QMessageBox, QLineEdit, QShortcut)
from PyQt5.QtCore import QThread, pyqtSignal, QObject
from PyQt5.QtGui import QKeySequence, QColor, QTextCharFormat, QTextDocument, QTextCursor

# --- CACHE DICTIONARY ---
# Hierin slaan we bekende apparaten op zodat we Windows niet telkens hoeven te pollen.
DEVICE_DICTIONARY = {
    # Gekoelde camera's en volgcamera's
    "VID_03C3&PID_2602": "ZWO ASI2600 Series (Main/Duo)",
    "VID_03C3&PID_2209": "ZWO ASI220MM Mini (Guide Camera)",
    
    # Accessoires
    "VID_03C3&PID_1F10": "ZWO EAF (Electronic Automatic Focuser)",
    
    # Externe schijfstations
    "VID_04E8&PID_4001": "Samsung Portable SSD T7",

    # Muis
    "VID_046D&PID_C077": "Logitech USB Receiver",

    # USB Hubs en Controllers (Vaak ingebouwd in PC, Montering of losse Hubs)
    "VID_2109&PID_0813": "VIA Labs SuperSpeed USB 3.0 Hub",
    "VID_2109&PID_2813": "VIA Labs USB 2.0 Hub Controller",
    "VID_0424&PID_2512": "Microchip/SMSC USB 2.0 Hub Controller",
    "VID_04B4&PID_6572": "Cypress Semiconductor USB Hub",
    "VID_0BDA&PID_0411": "Realtek SuperSpeed USB 3.0 Hub",
    "VID_0BDA&PID_5411": "Realtek USB 3.0 Hub Controller",
}

def check_windows_power_state(iso_timestamp):
    """
    Checkt het Windows Systeemlogboek voor Kernel-Power Event 105
    en selecteert de status van het event dat het dichtst bij iso_timestamp ligt.
    """
    try:
        # Omzetten naar datetime object voor exacte tijdsberekening
        clean_ts_str = iso_timestamp.split('.')[0].replace('T', ' ')
        target_dt = datetime.strptime(clean_ts_str, "%Y-%m-%d %H:%M:%S")
        print(f"Zoekt naar N.I.N.A. Event time {target_dt}")

        # Zoek in Windows log met een strak venster van +- 3 seconden
        ps_script = f"""
        $time = Get-Date '{clean_ts_str}'
        Get-WinEvent -FilterHashtable @{{
            LogName = 'System'
            ProviderName = 'Microsoft-Windows-Kernel-Power'
            Id = 105
            StartTime = $time.AddSeconds(-3)
            EndTime = $time.AddSeconds(3)
        }} -ErrorAction SilentlyContinue | ForEach-Object {{
            [xml]$xml = $_.ToXml()
            $ac = $xml.Event.EventData.Data | Where-Object {{ $_.Name -eq 'AcOnline' }} | Select-Object -ExpandProperty '#text'
            [PSCustomObject]@{{
                Time     = $_.TimeCreated.ToString("yyyy-MM-dd HH:mm:ss")
                AcOnline = ($ac -eq 'true')
            }}
        }} | ConvertTo-Json
        """
        
        res = subprocess.run(["powershell", "-Command", ps_script], capture_output=True, text=True)
        
        if res.stdout.strip():
            data = json.loads(res.stdout)
            print(f'Windows Power state data: {data}')
            # Zorg dat we altijd een lijst hebben om doorheen te lussen
            events = [data] if isinstance(data, dict) else data
            
            # Zoek het event dat het dichtst bij target_dt ligt
            closest_event = None
            min_diff = float('inf')

            for ev in events:
                ev_dt = datetime.strptime(ev['Time'], "%Y-%m-%d %H:%M:%S")
                diff = abs((ev_dt - target_dt).total_seconds())
                if diff < min_diff:
                    min_diff = diff
                    closest_event = ev

            if closest_event:
                is_ac = closest_event.get('AcOnline', False)
                return "AC (Netspanning)" if is_ac else "BATTERIJ (Accu)"

    except Exception as e:
        print(f"Fout bij Windows Power check: {e}")
    
    return "Onbekend"

class EmittingStream(QObject):
    """Vangt stdout/stderr op en verzendt de tekst via een PyQt signaal."""
    text_written = pyqtSignal(str)

    def write(self, text):
        # Strip de witruimte/newlines om te checken of er echt inhoud is
        cleaned_text = text.strip()
        
        # Alleen uitzenden als de tekst niet leeg is
        if cleaned_text:
            self.text_written.emit(cleaned_text)

    def flush(self):
        """Vereist voor compatibiliteit met sys.stdout / sys.stderr."""
        pass


class LogParserWorker(QThread):
    """Worker thread om het logbestand te lezen zonder de GUI te laten bevriezen."""
    progressSignal = pyqtSignal(int)
    resultSignal = pyqtSignal(str)
    finishedSignal = pyqtSignal()
    dataParsedSignal = pyqtSignal(dict)

    def __init__(self, filePath):
        super().__init__()
        self.filePath = filePath

    def LookupDeviceInWindows(self, hardwareID):
        """
        Vraagt Windows via PowerShell wat de menselijke naam is van een VID/PID combinatie
        als deze nog niet in onze dictionary zit.
        """
        global DEVICE_DICTIONARY
        
        if hardwareID in DEVICE_DICTIONARY:
            return DEVICE_DICTIONARY[hardwareID]
        
        # --- NIEUW: Geef direct feedback in het logvenster dat we gaan zoeken ---
        self.resultSignal.emit(f"🔍 Nieuw apparaat ontdekt ({hardwareID}). Windows database raadplegen...")
        
        psCommand = f'Get-CimInstance Win32_PnPSignedDriver | Where-Object DeviceID -like "*{hardwareID}*" | Select-Object Description | ConvertTo-Json'
        try:
            result = subprocess.run(["powershell", "-Command", psCommand], capture_output=True, text=True, timeout=5)
            if result.stdout.strip():
                data = json.loads(result.stdout)
                if isinstance(data, list) and len(data) > 0:
                    desc = data[0].get('Description', 'Onbekend apparaat')
                elif isinstance(data, dict):
                    desc = data.get('Description', 'Onbekend apparaat')
                else:
                    desc = "Onbekend apparaat"
                
                # --- NIEUW: Geef succes-feedback in het logvenster ---
                self.resultSignal.emit(f"➕ Toegevoegd aan dictionary: {hardwareID} -> '{desc}'\n")
                
                DEVICE_DICTIONARY[hardwareID] = desc
                return desc
        except Exception:
            pass
        
        # Fallback als PowerShell niets vindt
        fallbackDesc = f"Onbekend USB-toestel ({hardwareID})"
        self.resultSignal.emit(f"⚠️ Geen specifieke naam gevonden in Windows voor {hardwareID}. Fallback '{fallbackDesc}' toegepast.\n")
        DEVICE_DICTIONARY[hardwareID] = fallbackDesc
        return fallbackDesc


    def run(self):
        outputText = ""
        if not os.path.exists(self.filePath):
            self.resultSignal.emit("Fout: Bestand bestaat niet.")
            self.finishedSignal.emit()
            return

        timestamps = []
        total_durations = []
        before_save_durations = []
        before_finalize_durations = []
        finalize_durations = []
        exposure_times = []  # Nieuwe lijst voor belichtingstijden per frame
        device_lags = [] # Bevat tuples: (timestamp_object, device_name, total_time)
        power_events = []    # Bevat power events met datetime, status & details
        
        current_exposure_time = None  # Bijhouden van de laatst gelezen belichtingstijd

        # Regex definities
        logStartRegex = re.compile(r"^-+([\d]{4}-[\d]{2}-[\d]{2}T[\d]{2}:[\d]{2}:[\d]{2})-+$")
        logRegex = re.compile(r"^([\d\-T\:\.]+)\|INFO\|.*\|(UsbDeviceWatcher_\w+)\|.*\|(.*)$")
        errorRegex = re.compile(r"^([\d\-T\:\.]+)\|ERROR\|([^|]+)\|.*\|(.*)$")
        timestampStartRegex = re.compile(r"^[\d]{4}-[\d]{2}-[\d]{2}T[\d]{2}:[\d]{2}:[\d]{2}")        
        systemEventsRegex = re.compile(r"^([\d\-T\:\.]+)\|INFO\|.*\|(SystemEvents)_([^\|]+)\|.*\|(.*)$")
        
        # Regex voor DeviceUpdateTimer waarschuwingen
        devicePollRegex = re.compile(
            r"^([\d\-T\:\.]+)\|WARNING\|DeviceUpdateTimer\.cs\|.*?"
            r"\|(\w+)\s+value update cycle took longer than the device poll interval "
            r"\(Total:\s*([\d\.]+)s\s*>\s*([\d\.]+)s"
        )
        # Regex om de exposure time uit 'Starting Exposure - Exposure Time: 120s;' op te vangen
        exposurePattern = re.compile(r"Starting Exposure - Exposure Time:\s*(?P<exp>\d+(?:\.\d+)?)s")

        # Regex patroon specifiek voor de ImageSaveController logregel
        imageSavePattern = re.compile(
            r'^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+).*?'
            r'Successfully saved file at.*?'
            r'Duration Total: (?P<total>\d+:\d+:\d+\.\d+);\s*'
            r'BeforeSave: (?P<before_save>\d+:\d+:\d+\.\d+);\s*'
            r'BeforeFinalizeImageSaved: (?P<before_finalize>\d+:\d+:\d+\.\d+);\s*'
            r'FinalizeSaveTime: (?P<finalize>\d+:\d+:\d+\.\d+)'
        )

        try:
            fileSize = os.path.getsize(self.filePath)
            bytesRead = 0

            inErrorMode = False
            currentErrorTimestamp = ""
            currentErrorComponent = ""
            currentErrorStack = []
            
            logStartTime = "Onbekend"

            with open(self.filePath, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    bytesRead += len(line.encode('utf-8'))
                    self.progressSignal.emit(int((bytesRead / fileSize) * 100))

                    cleanedLine = line.strip()
                    if not cleanedLine:
                        continue

                    # STAP 0: Log starttijd uit de header (eenmalig)
                    if logStartTime == "Onbekend":
                        startMatch = logStartRegex.match(cleanedLine)
                        if startMatch:
                            logStartTime = startMatch.group(1)
                            datePart, timePart = logStartTime.split('T')
                            outputText += (
                                f"{'=' * 66}\n"
                                f"\U0001f4c5 NINA LOG STARTTIJD: {datePart} om {timePart}\n"
                                f"{'=' * 66}\n\n"
                            )
                            continue

                    # Bepaal of we een Starting Exposure regel tegenkomen
                    exp_match = exposurePattern.search(cleanedLine)
                    if exp_match:
                        current_exposure_time = float(exp_match.group('exp'))

                    # Verzamelmodus: regel zonder timestamp hoort bij de lopende stack trace
                    if inErrorMode and not timestampStartRegex.match(cleanedLine):
                        currentErrorStack.append(cleanedLine)
                        continue

                    # Einde verzamelmodus: nieuwe timestampregel gevonden — flush de opgespaarde fout
                    if inErrorMode:
                        outputText += self.FormatErrorEvent(
                            currentErrorTimestamp, currentErrorComponent, currentErrorStack
                        )
                        inErrorMode = False
                        currentErrorStack = []

                    # --- VERWERKEN VAN DE TIMESTAMPREGELS ---

                    # SCENARIO 1: USB-gebeurtenis
                    if "UsbDeviceWatcher" in cleanedLine:
                        match = logRegex.match(cleanedLine)
                        if match:
                            outputText += self.ProcessUSBEvent(
                                timestamp=match.group(1),
                                action=match.group(2),
                                usbInfo=match.group(3)
                            )

                    # SCENARIO 2: Harde ERROR (geen SequenceItem)
                    elif "|ERROR|" in cleanedLine and "SequenceItem" not in cleanedLine:
                        match = errorRegex.match(cleanedLine)
                        if match:
                            inErrorMode = True
                            currentErrorTimestamp = match.group(1)
                            currentErrorComponent = match.group(2)
                            currentErrorStack = [match.group(3)]

                    # SCENARIO 3: System events
                    elif "SystemEvents" in cleanedLine:
                        match = systemEventsRegex.match(cleanedLine)
                        if match:
                            timestamp_str = match.group(1)
                            system_event = match.group(3)
                            info_str = match.group(4)
                            power_status = ""

                            if "PowerModeChanged" in system_event:
                                power_status = check_windows_power_state(timestamp_str)
                                print(f"POWER STATE: {power_status}")
                                if power_status != "Onbekend" and timestamps:
                                    # 1. Bewaar data voor de grafiek overlay (Matplotlib)
                                    # enkel wanneer we gestart zijn met het nemen van foto's
                                    try:
                                        dt = datetime.strptime(timestamp_str.split('.')[0], "%Y-%m-%dT%H:%M:%S")
                                        power_events.append({
                                            'datetime': dt,
                                        'timestamp_str': timestamp_str,
                                        'status': power_status,
                                        'is_ac': "AC" in power_status,
                                        'info': info_str
                                    })
                                    except Exception:
                                        pass

                            # 2. Logtekst altijd via ProcessSystemEvent opbouwen (100% uniforme stijl)
                            outputText += self.ProcessSystemEvent(
                                timestamp=timestamp_str,
                                systemEvent=system_event,
                                info=info_str,
                                power_status=power_status
                            )

                    # SCENARIO 4: Device Poll Lag Warnings
                    elif "DeviceUpdateTimer.cs" in cleanedLine:
                        match = devicePollRegex.match(cleanedLine)
                        if match:
                            timestamp_str = match.group(1)
                            device_name = match.group(2)
                            total_time = float(match.group(3))
                            poll_limit = float(match.group(4))
        
                            # Converteer ISO timestamp naar datetime object voor de grafiek-as
                            try:
                                # Captures YYYY-MM-DDTHH:MM:SS.ffff
                                dt = datetime.strptime(timestamp_str.split('.')[0], "%Y-%m-%dT%H:%M:%S")
                                device_lags.append({
                                    'datetime': dt,
                                    'device': device_name,
                                    'duration': total_time
                                })
                            except Exception:
                                pass   
                            outputText += self.ProcessDevicePollWarning(
                                timestamp=match.group(1),
                                device=match.group(2),
                                total_time=match.group(3),
                                poll_interval=match.group(4)
                            )
                    
                    # SCENARIO 5: ImageSave-tijden
                    elif match := imageSavePattern.match(line):
                        groups = match.groupdict()
                        
                        def parse_time_duration(time_str):
                            parts = time_str.split(':') # [H, M, S.fff]
                            hours = float(parts[0])
                            minutes = float(parts[1])
                            seconds = float(parts[2])
                            return hours * 3600 + minutes * 60 + seconds

                        timestamps.append(groups['timestamp'])
                        total_durations.append(parse_time_duration(groups['total']))
                        before_save_durations.append(parse_time_duration(groups['before_save']))
                        before_finalize_durations.append(parse_time_duration(groups['before_finalize']))
                        finalize_durations.append(parse_time_duration(groups['finalize']))
                        
                        # Koppel de laatst bekende belichtingstijd aan dit specifieke frame
                        exposure_times.append(current_exposure_time)

                if inErrorMode:
                    outputText += self.FormatErrorEvent(
                        currentErrorTimestamp, currentErrorComponent, currentErrorStack
                    )

            if not outputText or outputText.strip().endswith("==="):
                outputText += "Geen relevante USB-gebeurtenissen of camera-errors gevonden in dit logbestand."

            self.resultSignal.emit(outputText)
        except Exception as e:
            self.resultSignal.emit(f"Er is een fout opgetreden tijdens het parsen: {e}")

        save_data = {
            'timestamps': timestamps,
            'total': total_durations,
            'before_save': before_save_durations,
            'before_finalize': before_finalize_durations,
            'finalize': finalize_durations,
            'exposure_times': exposure_times,
            'device_lags': device_lags,
            'power_events': power_events
        }
        self.dataParsedSignal.emit(save_data)
        self.finishedSignal.emit()


    def ProcessDevicePollWarning(self, timestamp, device, total_time, poll_interval):
        return (
            f"[{timestamp}] ⚠️ DEVICE LAG\n"
            f"📝 Context: {device} Update duurde {total_time}s (limiet: {poll_interval}s)\n"
            f"{'-' * 80}\n"
        )
        
    def ProcessUSBEvent(self, timestamp, action, usbInfo) -> str:
        """Verwerk een USB-apparaat regel en geef de opgemaakte tekst terug.
        Args:
            timestamp (str): Tijdstempel van de gebeurtenis.
            action (str): De actie die plaatsvond (bijv. 'UsbDeviceAdded', 'UsbDeviceRemoved').
            usbInfo (str): De USB-informatie string.
        Returns:
            str: De opgemaakte tekst die aan het logboek wordt toegevoegd.
        """
        vidPidRegex = re.compile(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})")
        descRegex = re.compile(r"Description:\s*([^,]+)")
        nameRegex = re.compile(r"Name:\s*([^,]+)")

        deviceName = None
        logFoundName = None
        hardwareId = None

        if "Removed" in action:
            action = "\U0001f534 VERWIJDERD"
        else:
            action = "\U0001f7e2 GEPLAATST"
        action = action.strip()

        descMatch = descRegex.search(usbInfo)
        if descMatch:
            logFoundName = descMatch.group(1).strip()
        else:
            nameMatch = nameRegex.search(usbInfo)
            if nameMatch:
                logFoundName = nameMatch.group(1).strip()

        if logFoundName and "generieke" not in logFoundName.lower() and "usb-invoer" not in logFoundName.lower():
            deviceName = logFoundName

        vpMatch = vidPidRegex.search(usbInfo)
        if vpMatch:
            hardwareId = f"VID_{vpMatch.group(1)}&PID_{vpMatch.group(2)}"
            if hardwareId in DEVICE_DICTIONARY:
                deviceName = DEVICE_DICTIONARY[hardwareId]
            elif not deviceName:
                psName = self.LookupDeviceInWindows(hardwareID=hardwareId)
                print(f"psName: {psName}")
                if "onbekend" not in psName.lower() and "unknown" not in psName.lower():
                    deviceName = psName

        if not deviceName or "onbekend" in deviceName.lower() or "unknown" in deviceName.lower():
            deviceName = logFoundName if logFoundName else f"Onbekend USB-toestel ({hardwareId or 'Geen ID'})"

        return (
            f"[{timestamp}] {action}\n"
            f"\U0001f449 Toestel: {deviceName}\n"
            f"\U0001f4c4 Log data: {usbInfo}\n"
            + "-" * 80 + "\n"
        )

    def ProcessSystemEvent(self, timestamp, systemEvent, info, power_status="") -> str:
        """Verwerk een SystemEvents-regel en geef de opgemaakte tekst terug."""
        
        if "PowerModeChanged" in systemEvent:
            is_ac = "AC" in power_status
            status_text = "AC Netspanning (Hersteld)" if is_ac else "Batterij / Accu (Netspanning weggevallen)"

            return (
                f"[{timestamp}] System Event ({systemEvent})\n"
                f"⚡ Status: {status_text} | Windows Status: {power_status}\n"
                f"📋 Details: {info}\n"
                + "-" * 80 + "\n"
            )
        
        # Standaard weergave voor overige system events
        return (
            f"[{timestamp}] System Event ({systemEvent})\n"
            f"🔥 Details: {info}\n"
            + "-" * 80 + "\n"
        )

    def FormatErrorEvent(self, timestamp, component, stackLines):
        """Hulpfunctie om elke verzamelde stack trace intelligent te formatteren."""
        # 1. Bepaal de header
        if "NotifyTaskCompletion" in component:
            header = f"[{timestamp}] ⚠️ ASYNC TAAK GEFAALD ({component})"
        elif "AnchorableSnapshotVM" in component:
            header = f"[{timestamp}] ⚠️ SNAPSHOT DOWNLOAD GEFAALD ({component})"
        else:
            header = f"[{timestamp}] ⚠️ CRASH / ERROR ({component})"
            
        out = f"{header}\n"
        
        # 2. Bepaal de Oorzaak / Context (altijd de eerste regel behouden!)
        firstLine = stackLines[0] if stackLines else "Onbekende fout"
        out += f"📝 Context: {firstLine}\n"
        
        # Zoek of er een specifieke Exception-regel in de stack zit voor extra details
        exceptionDetail = None
        for line in stackLines[1:]: # Sla de eerste regel over, die hebben we al
            if "Exception:" in line or "--->" in line:
                exceptionDetail = line
                break
        
        if exceptionDetail:
            out += f"💥 Details: {exceptionDetail}\n"
        
        # 3. Intelligent zoeken naar de getroffen actie in de volledige stack
        contextHints = []
        full_stack_text = " ".join(stackLines)
        if "CoolCamera" in full_stack_text: contextHints.append("Camera Koeling (CoolCamera)")
        if "RegulateTemperature" in full_stack_text: contextHints.append("Temperatuurregeling (RegulateTemperature)")
        if "CaptureImage" in full_stack_text: contextHints.append("Opname maken (CaptureImage)")
        if "SnapImage" in full_stack_text: contextHints.append("Snapshot downloaden (SnapImage)")
        if "Slew" in full_stack_text: contextHints.append("Telescoopbesturing / Slewing")
        if "Focuser" in full_stack_text: contextHints.append("Focusser aansturen (Focuser)")
        
        if contextHints:
            out += f"🎯 Getroffen actie(s): {', '.join(list(set(contextHints)))}\n"
        
        # 4. Filter en toon de stacktrace (nu ook voor ASCOM en System crashes)
        traceLines = []
        for line in stackLines[1:]:
            # Accepteer NINA traces, ASCOM traces, inner uitzonderingen en algemene 'at ' regels
            if "at " in line or "---" in line or "--->" in line:
                # Kort eventuele lange source-paddens in voor de leesbaarheid
                shortLine = line.split(" in /_")[0] if " in /_" in line else line
                # Verwijder ook eventuele lokale Windows-paden om het strak te houden
                shortLine = shortLine.split(" in J:\\")[0] if " in J:\\" in shortLine else shortLine
                traceLines.append(f"   {shortLine}")

        if traceLines:
            out += "📋 Volledige trace:\n"
            out += "\n".join(traceLines) + "\n"
                
        out += "-" * 80 + "\n"
        return out

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.file_path = ""
        # Alle GUI-opbouw gebeurt nu op één centrale plek in initUI!
        self.initUI()
        # ------------------------------------------------------------------
        # OMLEIDEN VAN STDOUT EN STDERR NAAR HET TEKSTVENSTER
        # ------------------------------------------------------------------
        # Omleiding voor gewone print() meldingen
        sys.stdout = EmittingStream()
        sys.stdout.text_written.connect(self.append_to_log)

        # Omleiding voor Exception tracebacks en foute outputs
        sys.stderr = EmittingStream()
        sys.stderr.text_written.connect(self.append_to_log_error)

    def initUI(self):
        self.setWindowTitle("NINA Log Analyzer")
        self.setGeometry(100, 100, 900, 600)

        # 1. UI Elementen
        self.label = QLabel("Selecteer een NINA logbestand om te analyseren:", self)
        self.btn_open = QPushButton("Blader naar Logbestand...", self)
        self.btn_open.clicked.connect(self.OpenFileDialog)
        
        self.btn_export_pdf = QPushButton("Exporteer naar PDF", self)
        self.btn_export_pdf.setEnabled(False)
        self.btn_export_pdf.clicked.connect(self.ExportToPDF)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setValue(0)
        
        self.text_edit = QTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlaceholderText("De resultaten verschijnen hier...")

        # --- Zoekbalk UI (Embedded Widget) ---
        self.search_widget = QWidget()
        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(5, 2, 5, 2)  # Lekker compact
        search_layout.setSpacing(5)

        self.search_label = QLabel("Zoeken:", self)
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Typ zoektekst...")
        self.search_input.returnPressed.connect(self.search_text)  # Zoek bij 'Enter'
        
        self.btn_search_next = QPushButton("Volgende", self)
        self.btn_search_next.clicked.connect(self.search_text)

        self.btn_search_prev = QPushButton("Vorige", self)
        self.btn_search_prev.clicked.connect(self.search_text_previous)
        
        self.btn_close_search = QPushButton("✕", self)
        self.btn_close_search.setMaximumWidth(30)
        self.btn_close_search.clicked.connect(self.hide_search_bar)
        
        search_layout.addWidget(self.search_label)
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.btn_search_next)
        search_layout.addWidget(self.btn_search_prev)
        search_layout.addWidget(self.btn_close_search)
        self.search_widget.setLayout(search_layout)
        
        self.search_widget.hide()  # Standaard verborgen bij opstarten

        # --- Ctrl+F Sneltoets ---
        self.shortcut_search = QShortcut(QKeySequence("Ctrl+F"), self)
        self.shortcut_search.activated.connect(self.toggle_search_bar)

        # 2. Matplotlib Canvas & Figure hier ÉÉN KEER aanmaken
        self.figure = Figure(figsize=(10, 5), dpi=100)
        self.canvas = FigureCanvas(self.figure)

        self.toolbar = NavigationToolbar(self.canvas, self)
        
        # Koppel de muis-hover gebeurtenis op het canvas voor de live loep
        self.canvas.mpl_connect('motion_notify_event', self.OnCanvasHover)

        # 3. Knoppenbalk bovenaan (inclusief Loep Checkbox)
        top_layout = QHBoxLayout()
        top_layout.addWidget(self.label)
        top_layout.addWidget(self.btn_open)
        top_layout.addWidget(self.btn_export_pdf)
        top_layout.addStretch()  # Houdt de knoppen netjes links uitgelijnd

        # 4. Hoofd layout opbouwen
        main_layout = QVBoxLayout()
        main_layout.addLayout(top_layout)
        main_layout.addWidget(self.progress_bar)
        
        # Zoekbalk direct boven de text_edit plaatsen
        main_layout.addWidget(self.search_widget)
        main_layout.addWidget(self.text_edit)
        
        # Voeg het canvas toe aan de hoofdlayout (onder het tekstvak)
        main_layout.addWidget(self.toolbar)
        main_layout.addWidget(self.canvas)

        # 5. EENMALIG de centrale widget instellen
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)


    def append_to_log(self, text):
        """Voegt reguliere console print-statements toe aan het tekstvenster."""
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(f"Opgevangen print: {text}\n")
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()

    def append_to_log_error(self, text):
        """Voegt uitzonderingen en foutmeldingen toe in het rood."""
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        
        # Optioneel: Maak uitzonderingen/tracebacks rood voor beter contrast
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("crimson"))
        
        cursor.insertText(f"Opgevangen fout: {text}\n", fmt)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()


    def InitLoupeAxes(self, ax):
        """Maakt een inset-axes aan die dienst doet als de loep-lens."""
        # Maak een sub-axes aan de rechterbovenhoek (of zwevend)
        # [left, bottom, width, height] in relatieve coördinaten van de hoofd-axes (0 tot 1)
        self.ax_loupe = ax.inset_axes([0.65, 0.55, 0.32, 0.40])
        self.ax_loupe.set_title("🔍 Loep (Zoom)", fontsize=8, fontweight='bold')
        self.ax_loupe.grid(True, linestyle=':', alpha=0.6)
        self.ax_loupe.set_visible(False)

    def toggle_search_bar(self):
        """Schakelt de zoekbalk in of uit bij het indrukken van Ctrl+F."""
        if self.search_widget.isVisible():
            self.hide_search_bar()
        else:
            self.show_search_bar()

    def show_search_bar(self):
        """Toont de zoekbalk en zet de focus direct in het invoerveld."""
        self.search_widget.show()
        self.search_input.setFocus()
        self.search_input.selectAll()

    def hide_search_bar(self):
        """Verbergt de zoekbalk en wist de gele markering in het tekstvak."""
        self.search_widget.hide()
        # Wis de felgele markeringen
        self.text_edit.setExtraSelections([])
        self.text_edit.setFocus()

    def search_text(self):
        """Zoekt voorwaarts naar tekst, scrolt er naartoe en markeert de tekst felgeel."""
        query = self.search_input.text()
        
        if not query:
            self.text_edit.setExtraSelections([])
            return

        # Start vanaf de huidige positie (of net na een actieve match)
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            start_pos = cursor.selectionEnd()
        else:
            start_pos = cursor.position()

        document = self.text_edit.document()
        # Zoek voorwaarts vanaf de startpositie
        found_cursor = document.find(query, start_pos)

        # Wrap-around: Als er niets is gevonden, zoek vanaf het begin (positie 0)
        if found_cursor.isNull():
            found_cursor = document.find(query, 0)

        # Als er een resultaat is gevonden:
        if not found_cursor.isNull():
            # 1. Maak het felgele markeer-formaat aan
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#FFE600"))  # Fel geel
            fmt.setForeground(QColor("black"))    # Zwarte letters voor contrast

            selection = QTextEdit.ExtraSelection()
            selection.cursor = found_cursor
            selection.format = fmt

            # 2. Pas de felgele markering toe
            self.text_edit.setExtraSelections([selection])

            # 3. Zorg dat het scherm automatisch naar de match scrolt
            # We zetten de weergave-cursor op het begin van de vondst (zonder blauwe selectie)
            scroll_cursor = document.find(query, found_cursor.selectionStart())
            scroll_cursor.clearSelection()
            self.text_edit.setTextCursor(scroll_cursor)
            self.text_edit.ensureCursorVisible()
        else:
            # Niets gevonden: wis bestaande markeringen
            self.text_edit.setExtraSelections([])


    def search_text_previous(self):
        """Zoekt achterwaarts naar tekst met dezelfde felgele markering."""
        query = self.search_input.text()
        
        if not query:
            self.text_edit.setExtraSelections([])
            return

        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            start_pos = cursor.selectionStart()
        else:
            start_pos = cursor.position()

        document = self.text_edit.document()
        # Zoek achterwaarts vanaf de huidige positie
        found_cursor = document.find(query, start_pos, QTextDocument.FindBackward)

        # Wrap-around: Als er niets is gevonden, zoek vanaf het einde van het document
        if found_cursor.isNull():
            found_cursor = document.find(query, document.characterCount(), QTextDocument.FindBackward)

        if not found_cursor.isNull():
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#FFE600"))
            fmt.setForeground(QColor("black"))

            selection = QTextEdit.ExtraSelection()
            selection.cursor = found_cursor
            selection.format = fmt

            self.text_edit.setExtraSelections([selection])

            # Zorg voor scrollen en opheffen van standaard selectie
            scroll_cursor = document.find(query, found_cursor.selectionStart())
            scroll_cursor.clearSelection()
            self.text_edit.setTextCursor(scroll_cursor)
            self.text_edit.ensureCursorVisible()
        else:
            self.text_edit.setExtraSelections([])

    def OpenFileDialog(self):
        # Start automatisch in de standaard NINA log-map
        defaultPath = os.path.expandvars(r'%LOCALAPPDATA%\NINA\Logs')
        if not os.path.exists(defaultPath):
            defaultPath = os.path.expanduser("~")

        filePath, _ = QFileDialog.getOpenFileName(
            self, "Open NINA Logbestand", defaultPath, "Log Bestanden (*.log);;Alle Bestanden (*.*)"
        )

        if filePath:
            self.file_path = filePath
            self.text_edit.setText(f"Bezig met analyseren van: {os.path.basename(filePath)}...\nMoment, apparaten worden gekoppeld via Windows hardware ID's indien nodig...\n\n")
            self.btn_open.setEnabled(False)
            self.progress_bar.setValue(0)

            # Start de worker thread zodat de GUI soepel blijft draaien
            self.worker = LogParserWorker(filePath)
            self.worker.progressSignal.connect(self.progress_bar.setValue)
            self.worker.resultSignal.connect(self.text_edit.append)
            self.worker.dataParsedSignal.connect(self.OnDataReceived)
            self.worker.finishedSignal.connect(self.AnalysisFinished)
            self.worker.start()

    def OnDataReceived(self, data):
        """Wordt aangeroepen als de worker klaar is met parsen."""
        self.parsed_data = data

    def AnalysisFinished(self):
        self.btn_open.setEnabled(True)
        self.progress_bar.setValue(100)
        if self.parsed_data and self.parsed_data['timestamps']:
            self.PlotSaveTimes(self.parsed_data)


    def _get_clean_pdf_text(self, raw_text):
        """
        Vervangt of verwijdert Unicode-symbolen die niet standaard 
        in PDF-lettertypen zitten, om 'vierkantjes' te voorkomen.
        """
        # Eventuele bekende emoji's/symbolen vervangen door tekstversies
        replacements = {
            "✔": "[OK]", "❌": "[FOUT]", "⚠️": "[WAARSCHUWING]",
            "ℹ️": "[INFO]", "⚡": "[POWER]", "🔌": "[USB]",
            "📷": "[CAM]", "⏱️": "[TIJD]", "📁": "[FILE]"
        }
        text = raw_text
        for symbol, alt in replacements.items():
            text = text.replace(symbol, alt)

        # Optioneel: Filter eventuele overige niet-ASCII/niet-Latin1 karakters uit
        text = text.encode('latin-1', 'ignore').decode('latin-1')
        return text

    def ExportToPDF(self):
        """Exporteert het tekstuele dashboard, de grafiek, Device Lag tabel én PowerState events naar een Landscape PDF rapport."""
        if not self.file_path:
            QMessageBox.warning(self, "Waarschuwing", "Er is geen logbestand geladen.")
            return

        default_name = os.path.splitext(os.path.basename(self.file_path))[0] + "_Rapport.pdf"
        pdf_path, _ = QFileDialog.getSaveFileName(
            self, "Sla PDF Rapport op", default_name, "PDF Bestanden (*.pdf)"
        )

        if not pdf_path:
            return  # Annuleer door gebruiker

        temp_img_path = None
        try:
            # 1. Probeer een TrueType font te registreren (Windows Arial)
            font_name = 'Helvetica' # Fallback
            try:
                arial_path = "C:\\Windows\\Fonts\\arial.ttf"
                if os.path.exists(arial_path):
                    pdfmetrics.registerFont(TTFont('ArialWin', arial_path))
                    font_name = 'ArialWin'
            except Exception:
                pass

            # 2. Sla de huidige Matplotlib grafiek tijdelijk op als afbeelding
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as temp_img:
                temp_img_path = temp_img.name
                self.figure.savefig(temp_img_path, format='png', dpi=200, bbox_inches='tight')

            # 3. Bouw het ReportLab PDF document op in LANDSCAPE
            doc = SimpleDocTemplate(
                pdf_path,
                pagesize=landscape(A4),
                rightMargin=36, leftMargin=36,
                topMargin=36, bottomMargin=36
            )

            story = []
            styles = getSampleStyleSheet()

            # Stijlen definieren
            title_style = ParagraphStyle(
                'DocTitle',
                parent=styles['Heading1'],
                fontName=font_name,
                fontSize=16,
                leading=20,
                textColor=colors.HexColor('#1a2a3a'),
                spaceAfter=6
            )

            text_style = ParagraphStyle(
                'LogText',
                parent=styles['Code'],
                fontName=font_name,
                fontSize=8,
                leading=11,
                backColor=colors.HexColor('#f8f9fa'),
                borderColor=colors.HexColor('#dcdcdc'),
                borderWidth=1,
                borderPadding=8,
                spaceAfter=12
            )

            cell_style = ParagraphStyle(
                'TableCell',
                parent=styles['Normal'],
                fontName=font_name,
                fontSize=8,
                leading=10
            )

            # A. Titel toevoegen
            log_filename = os.path.basename(self.file_path)
            story.append(Paragraph(f"<b>N.I.N.A. Log Analyse Rapport</b>", title_style))
            story.append(Paragraph(f"<i>Bestand: {log_filename}</i>", styles['Normal']))
            story.append(Spacer(1, 10))

            # B. Tekstueel Dashboard opschonen en toevoegen
            raw_dashboard = self.text_edit.toPlainText().strip()
            if not raw_dashboard:
                raw_dashboard = "Geen tekstuele resultaten beschikbaar."

            clean_dashboard = self._get_clean_pdf_text(raw_dashboard)

            formatted_text = (
                clean_dashboard
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('\n', '<br/>')
            )
            story.append(Paragraph(formatted_text, text_style))

            # C. Grafiek Afbeelding toevoegen
            story.append(Spacer(1, 5))
            story.append(Paragraph("<b>Opslagtijden Grafiek:</b>", styles['Heading2']))
            story.append(Spacer(1, 5))

            img = RLImage(temp_img_path, width=760, height=240)
            story.append(img)

            # Data ophalen voor tabellen
            data = getattr(self, 'current_data', {})

            # ------------------------------------------------------------------
            # D. OVERZICHTSTABEL: DEVICE LAGS
            # ------------------------------------------------------------------
            device_lags = data.get('device_lags', [])

            if device_lags:
                story.append(Spacer(1, 10))
                story.append(Paragraph("<b>Gedetecteerde Device Lags (Vertragingen):</b>", styles['Heading3']))
                story.append(Spacer(1, 4))

                table_data = [
                    [
                        Paragraph("<b>Tijdstip</b>", cell_style),
                        Paragraph("<b>Apparaat / Device</b>", cell_style),
                        Paragraph("<b>Duurtijd (seconden)</b>", cell_style),
                        Paragraph("<b>Status / Impact</b>", cell_style)
                    ]
                ]

                for lag in device_lags:
                    lag_dt = lag.get('datetime')
                    time_str = lag_dt.strftime("%H:%M:%S") if lag_dt else "Onbekend"
                    device = lag.get('device', 'Onbekend')
                    duration = lag.get('duration', 0.0)

                    if duration > 30:
                        status_str = "<font color='crimson'><b>Ernstige vertraging</b></font>"
                    elif duration > 10:
                        status_str = "<font color='orange'><b>Matige vertraging</b></font>"
                    else:
                        status_str = "<font color='gray'>Lichte vertraging</font>"

                    table_data.append([
                        Paragraph(time_str, cell_style),
                        Paragraph(device, cell_style),
                        Paragraph(f"{duration:.2f} s", cell_style),
                        Paragraph(status_str, cell_style)
                    ])

                lag_table = Table(table_data, colWidths=[100, 200, 160, 300])
                lag_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e9ecef')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1a2a3a')),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dcdcdc')),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ]))

                story.append(lag_table)

            # ------------------------------------------------------------------
            # E. OVERZICHTSTABEL: POWER CHANGED EVENTS (Nieuw)
            # ------------------------------------------------------------------
            power_events = data.get('power_events', [])

            if power_events:
                story.append(Spacer(1, 10))
                story.append(Paragraph("<b>Windows Power State Events (Netstroom / Accu):</b>", styles['Heading3']))
                story.append(Spacer(1, 4))

                power_table_data = [
                    [
                        Paragraph("<b>Tijdstip</b>", cell_style),
                        Paragraph("<b>Voedingsbron</b>", cell_style),
                        Paragraph("<b>Status / Melding</b>", cell_style)
                    ]
                ]

                for p_event in power_events:
                    p_dt = p_event.get('Time') or p_event.get('datetime')
                    if hasattr(p_dt, 'strftime'):
                        p_time_str = p_dt.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        p_time_str = str(p_dt) if p_dt else "Onbekend"

                    ac_online = p_event.get('AcOnline', None)
                    
                    if ac_online is True:
                        source_str = "<font color='green'><b>Netstroom (AC)</b></font>"
                        msg_str = "Systeem op externe voeding aangesloten"
                    elif ac_online is False:
                        source_str = "<font color='crimson'><b>Accu / Batterij</b></font>"
                        msg_str = "Systeem overgeschakeld naar accu"
                    else:
                        source_str = "Onbekend"
                        msg_str = str(p_event.get('Message', 'Geen aanvullende details'))

                    power_table_data.append([
                        Paragraph(p_time_str, cell_style),
                        Paragraph(source_str, cell_style),
                        Paragraph(msg_str, cell_style)
                    ])

                # Tabel aanmaken (Totaal 760 pt breed)
                power_table = Table(power_table_data, colWidths=[160, 200, 400])
                power_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e9ecef')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1a2a3a')),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dcdcdc')),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ]))

                story.append(power_table)

            # 4. Genereer de PDF
            doc.build(story)
            QMessageBox.information(self, "Succes", f"Rapport succesvol opgeslagen:\n{pdf_path}")

        except Exception as e:
            QMessageBox.critical(self, "Fout", f"Kon PDF niet exporteren: {e}")

        finally:
            if temp_img_path and os.path.exists(temp_img_path):
                os.remove(temp_img_path)


    def PlotSaveTimes(self, data):
        """Rendert een Matplotlib grafiek van de ImageSave opslagtijden direct op het PyQt canvas."""
        self.current_data = data
        timestamps = data.get('timestamps', [])
        if not timestamps:
            return

        times_short = [ts.split('T')[1][:8] if 'T' in ts else ts for ts in timestamps]
        total_frames = len(times_short)
        x_indices = np.arange(total_frames)

        self.figure.clear()
        ax = self.figure.add_subplot(111)

        # ------------------------------------------------------------------
        # 1. VISUELE ACCENTUERING VAN BELICHTINGSTIJDEN (Achtergrond-zones)
        # ------------------------------------------------------------------
        exposure_times = data.get('exposure_times', [])
    
        # Filter alleen de daadwerkelijk geregistreerde/opgeslagen belichtingstijden
        valid_exps = [e for e in exposure_times if e is not None]

        if valid_exps:
            exp_counts = {}
            for exp in valid_exps:
                exp_counts[exp] = exp_counts.get(exp, 0) + 1

            unique_exps = sorted(list(exp_counts.keys()))
            cmap = plt.get_cmap('Pastel1')
            colors = [cmap(i) for i in np.linspace(0, 1, max(len(unique_exps), 3))]
            exp_color_map = {exp: colors[i] for i, exp in enumerate(unique_exps)}

            start_idx = 0
            current_exp = exposure_times[0]

            for i in range(1, total_frames + 1):
                if i == total_frames or exposure_times[i] != current_exp:
                    if current_exp is not None and current_exp in exp_counts:
                        color = exp_color_map[current_exp]
                        count = exp_counts[current_exp]
                        label_text = f"Exposure: {current_exp:.0f}s ({count} {'sub' if count == 1 else 'subs'})"

                        ax.axvspan(
                            start_idx - 0.5, 
                            i - 0.5, 
                            color=color, 
                            alpha=0.35, 
                            zorder=0, 
                            label=label_text
                        )
                    
                    start_idx = i
                    if i < total_frames:
                        current_exp = exposure_times[i]

        # ------------------------------------------------------------------
        # 2. PLOTTEN VAN DE OPSLAGTIJDEN (Lijnen & Punten)
        # ------------------------------------------------------------------
        ax.plot(x_indices, data['total'], label='Total Duration', color='crimson', linewidth=2, zorder=2)
        ax.plot(x_indices, data['before_save'], label='Before Save', color='darkorange', linestyle='--', zorder=2)
        ax.plot(x_indices, data['before_finalize'], label='Before Finalize', color='royalblue', linestyle='--', zorder=2)
        ax.plot(x_indices, data['finalize'], label='Finalize Save Time', color='forestgreen', linestyle=':', zorder=2)

        ax.scatter(x_indices, data['total'], color='crimson', s=15, zorder=3)

        # Helper functie voor tijdsomzetting (hergebruikt door lags en power events)
        def parse_to_datetime(ts_str):
            clean_ts = ts_str.split('.')[0]
            if 'T' in clean_ts:
                return datetime.strptime(clean_ts, "%Y-%m-%dT%H:%M:%S")
            return datetime.strptime(clean_ts, "%H:%M:%S")

        first_dt = parse_to_datetime(timestamps[0]) if timestamps else None
        frame_secs = [(parse_to_datetime(ts) - first_dt).total_seconds() for ts in timestamps] if first_dt else []

        # ------------------------------------------------------------------
        # 3. OVERLAY: DEVICE LAGS (Verticale Lijnen met Hover Data)
        # ------------------------------------------------------------------
        device_lags = data.get('device_lags', [])
        self.lag_lines = []

        if device_lags and timestamps:
            try:
                grouped_lags = {}
                for lag in device_lags:
                    lag_dt = lag.get('datetime')
                    if not lag_dt:
                        continue
                    
                    lag_sec = (lag_dt - first_dt).total_seconds()
                    bucket_key = round(lag_sec / 2.0) * 2

                    if bucket_key not in grouped_lags:
                        grouped_lags[bucket_key] = {
                            'lag_sec': lag_sec,
                            'time_str': lag_dt.strftime("%H:%M:%S"),
                            'events': []
                        }
                    grouped_lags[bucket_key]['events'].append(lag)

                for bucket in grouped_lags.values():
                    lag_sec = bucket['lag_sec']
                    x_pos = np.interp(lag_sec, frame_secs, x_indices)

                    max_duration = max(e['duration'] for e in bucket['events'])
                    line_color = 'crimson' if max_duration > 30 else 'darkorange'

                    line = ax.axvline(
                        x=x_pos, 
                        color=line_color, 
                        linestyle='-.', 
                        linewidth=1.8, 
                        alpha=0.85, 
                        zorder=4,
                        label="⚠️ Device Lag Event"
                    )
                    
                    line.lag_info = bucket
                    self.lag_lines.append(line)

            except Exception as e:
                print(f"Fout bij het verwerken van device lags overlay: {e}")

        # ------------------------------------------------------------------
        # 3b. OVERLAY: POWER EVENTS (Verticale Lijnen voor AC / BATTERIJ)
        # ------------------------------------------------------------------
        power_events = data.get('power_events', [])
        self.power_lines = []

        if power_events and timestamps:
            try:
                for p_event in power_events:
                    p_dt = p_event.get('datetime')
                    if not p_dt:
                        continue

                    p_sec = (p_dt - first_dt).total_seconds()
                    x_pos = np.interp(p_sec, frame_secs, x_indices)

                    is_ac = p_event.get('is_ac', False)
                    line_color = '#00BFFF' if is_ac else '#FF00FF'  # Cyaan voor AC, Magenta voor Batterij
                    label_tag = "Stroom: AC (Netspanning)" if is_ac else "Stroom: Batterij"

                    line = ax.axvline(
                        x=x_pos, 
                        color=line_color, 
                        linestyle='--', 
                        linewidth=2.0, 
                        alpha=0.9, 
                        zorder=5,
                        label=label_tag
                    )
                    
                    # Sla het event op aan de lijn voor het pop-up venster bij een klik
                    line.power_info = p_event
                    self.power_lines.append(line)

            except Exception as e:
                print(f"Fout bij het verwerken van power events overlay: {e}")

        # Optioneel: dynamic tooltip-annotatie (verborgen tot hover)
        self.lag_tooltip = ax.annotate(
            "", 
            xy=(0, 0), 
            xytext=(15, 15),
            textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.5", fc="yellow", alpha=0.9, ec="black"),
            arrowprops=dict(arrowstyle="->", connectionstyle="arc3,rad=0"),
            zorder=10
        )
        self.lag_tooltip.set_visible(False)

        # Koppel hover en klik events aan de canvas
        if not hasattr(self, 'hover_cid') or self.hover_cid is None:
            self.hover_cid = self.canvas.mpl_connect("motion_notify_event", self.OnCanvasHover)
        
        if not hasattr(self, 'click_cid') or self.click_cid is None:
            self.click_cid = self.canvas.mpl_connect("button_press_event", self.OnCanvasClick)

        # ------------------------------------------------------------------
        # 4. OPMAAK EN ASSEN
        # ------------------------------------------------------------------
        max_labels = 12
        step = max(1, total_frames // max_labels)
        major_indices = list(range(0, total_frames, step))
        major_labels = [times_short[i] for i in major_indices]

        ax.set_xticks(major_indices)
        ax.set_xticklabels(major_labels, rotation=45, ha='right')
        ax.set_xticks(x_indices, minor=True)

        file_name = os.path.basename(self.file_path) if self.file_path else "Onbekend bestand"

        ax.set_title(
            f"N.I.N.A. Image Save Durations per Frame ({total_frames} subs)\n"
            f"Logbestand: {file_name}",
            fontsize=10,
            fontweight='bold'
        )
        ax.set_xlabel('Tijdstip (HH:MM:SS)')
        ax.set_ylabel('Opslagtijd (seconden)')

        ax.grid(True, which='major', linestyle='-', alpha=0.5)
        ax.grid(True, which='minor', linestyle=':', alpha=0.2)

        handles, labels = ax.get_legend_handles_labels()
        by_label = dict(zip(labels, handles))
        ax.legend(by_label.values(), by_label.keys(), loc='upper left', framealpha=0.9)

        self.figure.tight_layout()
        self.canvas.draw()
        self.btn_export_pdf.setEnabled(True)

    def OnCanvasHover(self, event):
        """Toont een popup-tooltip als de muis over een Device Lag lijn beweegt."""
        if not hasattr(self, 'lag_lines') or not self.lag_lines or event.inaxes is None:
            if hasattr(self, 'lag_tooltip') and self.lag_tooltip.get_visible():
                self.lag_tooltip.set_visible(False)
                self.canvas.draw_idle()
            return

        vis = False
        ax = event.inaxes
        x_min, x_max = ax.get_xlim()
        x_range = x_max - x_min if x_max != x_min else 1

        for line in self.lag_lines:
            x_line = line.get_xdata()[0]
            
            # Controleer of de muis in de buurt van de verticale lijn is (X-afstand < 0.4)
            if event.xdata is not None and abs(event.xdata - x_line) < 0.4:
                info = getattr(line, 'lag_info', None)
                if not info:
                    continue
                
                text_lines = [f"⚠️ Device Lag op {info['time_str']}:"]
                for ev in info['events']:
                    text_lines.append(f"  • {ev['device']}: {ev['duration']:.2f}s")

                tooltip_text = "\n".join(text_lines)
                self.lag_tooltip.set_text(tooltip_text)

                rel_x = (x_line - x_min) / x_range

                if rel_x > 0.65:
                    offset_x = -15
                    ha = 'right'
                else:
                    offset_x = 15
                    ha = 'left'

                self.lag_tooltip.set_annotation_clip(True)
                self.lag_tooltip.xytext = (offset_x, 15)
                self.lag_tooltip.set_ha(ha)
                self.lag_tooltip.set_multialignment('left')
                
                self.lag_tooltip.xy = (x_line, event.ydata if event.ydata is not None else 0)
                self.lag_tooltip.set_visible(True)
                vis = True
                break

        if not vis and hasattr(self, 'lag_tooltip') and self.lag_tooltip.get_visible():
            self.lag_tooltip.set_visible(False)

        self.canvas.draw_idle()

    def OnCanvasClick(self, event):
        """Toont een PyQt pop-up venster wanneer er op een stroomlijn (power event) geklikt wordt."""
        if event.inaxes is None or not hasattr(self, 'power_lines'):
            return

        click_x = event.xdata
        if click_x is None:
            return

        # Zoek of er een stroomlijn in de buurt van de muisklik staat (tolerantie van 0.5 frame op X-as)
        for line in self.power_lines:
            line_x = line.get_xdata()[0]
            if abs(click_x - line_x) < 0.5:
                p_info = getattr(line, 'power_info', None)
                if not p_info:
                    continue

                ts = p_info.get('timestamp_str', 'Onbekend')
                status = p_info.get('status', 'Onbekend')
                is_ac = p_info.get('is_ac', False)

                # Stel de pop-up in
                msg = QMessageBox(self)
                if is_ac:
                    msg.setIcon(QMessageBox.Information)
                    msg.setWindowTitle("🔌 Stroom Event: AC Netspanning")
                    status_text = "<font color='green'><b>Netspanning Hersteld (AC)</b></font>"
                else:
                    msg.setIcon(QMessageBox.Warning)
                    msg.setWindowTitle("🔋 Stroom Event: Batterij")
                    status_text = "<font color='red'><b>Netspanning Weggevallen (Accu)</b></font>"

                msg.setText(f"<h3>Stroomvoorziening Gewijzigd</h3>")
                msg.setInformativeText(
                    f"<b>Tijdstip:</b> {ts}<br>"
                    f"<b>Status:</b> {status_text}<br>"
                    f"<b>Windows Verificatie:</b> {status}"
                )
                msg.setStandardButtons(QMessageBox.Ok)
                msg.exec_()
                break

def custom_excepthook(type, value, tb):
    """Zorgt dat ongeopvangen exceptions direct in de GUI verschijnen."""
    error_msg = "".join(traceback.format_exception(type, value, tb))
    sys.stderr.write(f"\n❌ [ONGEHANDELDE EXCEPTION]\n{error_msg}\n" + "-"*80 + "\n")

if __name__ == "__main__":
    sys.excepthook = custom_excepthook
    # 1. Controleer of er al een QApplication draait
    app = QApplication.instance()
    
    if app is None:
        # Er draait nog geen app, dus maak een nieuwe aan
        app = QApplication(sys.argv)
        app.setStyle('Fusion')
        created_app = True
    else:
        created_app = False

    # 2. Maak het venster aan en toon het
    win = MainWindow()
    win.show()

    # 3. Start de event loop ALLEEN als we zelf de app gestart hebben
    if created_app:
        # Gebruik app.exec() voor Qt5/Qt6 (of app.exec_() voor oudere PyQt5)
        exec_func = getattr(app, 'exec', None) or getattr(app, 'exec_')
        sys.exit(exec_func())