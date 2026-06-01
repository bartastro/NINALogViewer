import sys
import os
import re
import subprocess
import json
from PyQt5.QtWidgets import (QApplication, QWidget, QMainWindow, QPushButton, 
                             QFileDialog, QTextEdit, QVBoxLayout, QHBoxLayout, 
                             QLabel, QProgressBar)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

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
}

class LogParserWorker(QThread):
    """Worker thread om het logbestand te lezen zonder de GUI te laten bevriezen."""
    progress_signal = pyqtSignal(int)
    result_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path

    def lookup_device_in_windows(self, hardware_id):
        """
        Vraagt Windows via PowerShell wat de menselijke naam is van een VID/PID combinatie
        als deze nog niet in onze dictionary zit.
        """
        global DEVICE_DICTIONARY
        
        if hardware_id in DEVICE_DICTIONARY:
            return DEVICE_DICTIONARY[hardware_id]
        
        # --- NIEUW: Geef direct feedback in het logvenster dat we gaan zoeken ---
        self.result_signal.emit(f"🔍 Nieuw apparaat ontdekt ({hardware_id}). Windows database raadplegen...")
        
        ps_command = f'Get-CimInstance Win32_PnPSignedDriver | Where-Object DeviceID -like "*{hardware_id}*" | Select-Object Description | ConvertTo-Json'
        try:
            result = subprocess.run(["powershell", "-Command", ps_command], capture_output=True, text=True, timeout=5)
            if result.stdout.strip():
                data = json.loads(result.stdout)
                if isinstance(data, list) and len(data) > 0:
                    desc = data[0].get('Description', 'Onbekend apparaat')
                elif isinstance(data, dict):
                    desc = data.get('Description', 'Onbekend apparaat')
                else:
                    desc = "Onbekend apparaat"
                
                # --- NIEUW: Geef succes-feedback in het logvenster ---
                self.result_signal.emit(f"➕ Toegevoegd aan dictionary: {hardware_id} -> '{desc}'\n")
                
                DEVICE_DICTIONARY[hardware_id] = desc
                return desc
        except Exception:
            pass
        
        # Fallback als PowerShell niets vindt
        fallback_desc = f"Onbekend USB-toestel ({hardware_id})"
        self.result_signal.emit(f"⚠️ Geen specifieke naam gevonden in Windows voor {hardware_id}. Fallback '{fallback_desc}' toegepast.\n")
        DEVICE_DICTIONARY[hardware_id] = fallback_desc
        return fallback_desc


    def run(self):
        output_text = ""
        if not os.path.exists(self.file_path):
            self.result_signal.emit("Fout: Bestand bestaat niet.")
            self.finished_signal.emit()
            return

        # Regex definities
        log_start_regex = re.compile(r"^-+([\d]{4}-[\d]{2}-[\d]{2}T[\d]{2}:[\d]{2}:[\d]{2})-+$")
        log_regex = re.compile(r"^([\d\-T\:\.]+)\|INFO\|.*\|(UsbDeviceWatcher_\w+)\|.*\|(.*)$")
        error_regex = re.compile(r"^([\d\-T\:\.]+)\|ERROR\|([^|]+)\|.*\|(.*)$")
        timestamp_start_regex = re.compile(r"^[\d]{4}-[\d]{2}-[\d]{2}T[\d]{2}:[\d]{2}:[\d]{2}")        
        system_events_regex = re.compile(r"^([\d\-T\:\.]+)\|INFO\|.*\|(SystemEvents)_([^\|]+)\|.*\|(.*)$")

        try:
            file_size = os.path.getsize(self.file_path)
            bytes_read = 0

            in_notify_error = False
            current_error_timestamp = ""
            current_error_component = ""
            current_error_stack = []
            log_start_time = "Onbekend"

            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    bytes_read += len(line.encode('utf-8'))
                    self.progress_signal.emit(int((bytes_read / file_size) * 100))

                    cleaned_line = line.strip()
                    if not cleaned_line:
                        continue

                    # STAP 0: Log starttijd uit de header (eenmalig)
                    if log_start_time == "Onbekend":
                        start_match = log_start_regex.match(cleaned_line)
                        if start_match:
                            log_start_time = start_match.group(1)
                            date_part, time_part = log_start_time.split('T')
                            output_text += (
                                f"{'=' * 66}\n"
                                f"\U0001f4c5 NINA LOG STARTTIJD: {date_part} om {time_part}\n"
                                f"{'=' * 66}\n\n"
                            )
                            continue

                    # Verzamelmodus: regel zonder timestamp hoort bij de lopende stack trace
                    if in_notify_error and not timestamp_start_regex.match(cleaned_line):
                        current_error_stack.append(cleaned_line)
                        continue

                    # Einde verzamelmodus: nieuwe timestampregel gevonden — flush de opgespaarde fout
                    if in_notify_error:
                        output_text += self.format_notify_error(
                            current_error_timestamp, current_error_component, current_error_stack
                        )
                        in_notify_error = False
                        current_error_stack = []

                    # SCENARIO 1: USB-gebeurtenis
                    if "UsbDeviceWatcher" in cleaned_line:
                        match = log_regex.match(cleaned_line)
                        if match:
                            output_text += self._process_usb_event(
                                timestamp=match.group(1),
                                action=match.group(2),
                                usb_info=match.group(3)
                            )

                    # SCENARIO 2: Harde ERROR (geen SequenceItem)
                    elif "|ERROR|" in cleaned_line and "SequenceItem" not in cleaned_line:
                        match = error_regex.match(cleaned_line)
                        if match:
                            result = self._process_error_event(
                                timestamp = match.group(1),
                                component = match.group(2),
                                error_msg = match.group(3))
                            output_text += result['output']
                            if result['notify']:
                                in_notify_error = True
                                current_error_timestamp = result['notify']['timestamp']
                                current_error_component = result['notify']['component']
                                current_error_stack = result['notify']['stack']
                    # SCENATIO 3: System events
                    if "SystemEvents" in cleaned_line:
                        match = system_events_regex.match(cleaned_line)
                        if match:
                            result = self._process_system_event(
                                timestamp=match.group(1),
                                system_event = match.group(3),
                                info = match.group(4)
                            )
                            output_text += result

                if in_notify_error:
                    output_text += self.format_notify_error(
                        current_error_timestamp, current_error_component, current_error_stack
                    )

            if not output_text or output_text.strip().endswith("==="):
                output_text += "Geen relevante USB-gebeurtenissen of camera-errors gevonden in dit logbestand."

            self.result_signal.emit(output_text)
        except Exception as e:
            self.result_signal.emit(f"Er is een fout opgetreden tijdens het parsen: {e}")

        self.finished_signal.emit()


    def _process_usb_event(self, timestamp, action, usb_info) -> str:
        """Verwerk een USB-apparaat regel en geef de opgemaakte tekst terug.
        Args:
            timestamp (str): Tijdstempel van de gebeurtenis.
            action (str): De actie die plaatsvond (bijv. 'UsbDeviceAdded', 'UsbDeviceRemoved').
            usb_info (str): De USB-informatie string.
        Returns:
            str: De opgemaakte tekst die aan het logboek wordt toegevoegd.
        """
        vid_pid_regex = re.compile(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})")
        desc_regex = re.compile(r"Description:\s*([^,]+)")
        name_regex = re.compile(r"Name:\s*([^,]+)")

        device_name = None
        log_found_name = None
        hardware_id = None

        if "Removed" in action:
            action = "\U0001f534 VERWIJDERD"
        else:
            action = "\U0001f7e2 GEPLAATST"
        action = action.strip()

        desc_match = desc_regex.search(usb_info)
        if desc_match:
            log_found_name = desc_match.group(1).strip()
        else:
            name_match = name_regex.search(usb_info)
            if name_match:
                log_found_name = name_match.group(1).strip()

        if log_found_name and "generieke" not in log_found_name.lower() and "usb-invoer" not in log_found_name.lower():
            device_name = log_found_name

        vp_match = vid_pid_regex.search(usb_info)
        if vp_match:
            hardware_id = f"VID_{vp_match.group(1)}&PID_{vp_match.group(2)}"
            if hardware_id in DEVICE_DICTIONARY:
                device_name = DEVICE_DICTIONARY[hardware_id]
            elif not device_name:
                ps_name = self.lookup_device_in_windows(hardware_id)
                print(f"ps_name: {ps_name}")
                if "onbekend" not in ps_name.lower() and "unknown" not in ps_name.lower():
                    device_name = ps_name

        if not device_name or "onbekend" in device_name.lower() or "unknown" in device_name.lower():
            device_name = log_found_name if log_found_name else f"Onbekend USB-toestel ({hardware_id or 'Geen ID'})"

        return (
            f"[{timestamp}] {action}\n"
            f"\U0001f449 Toestel: {device_name}\n"
            f"\U0001f4c4 Log data: {usb_info}\n"
            + "-" * 80 + "\n"
        )

    def _process_error_event(self, timestamp, component, error_msg) -> dict:
        """
        Verwerk een ERROR-regel.

        Geeft een dict terug met:
          - 'output' : direct op te nemen tekst (leeg bij NotifyTaskCompletion)
          - 'notify' : None, of een dict met 'timestamp', 'component' en 'stack'
                       als we in meerregelige verzamelmodus gaan
        """

        if "NotifyTaskCompletion" in component:
            return {
                'output': '',
                'notify': {
                    'timestamp': timestamp,
                    'component': component,
                    'stack': [error_msg],
                },
            }

        return {
            'output': (
                f"[{timestamp}] \u26a0\ufe0f CRASH / ERROR ({component})\n"
                f"\U0001f4a5 Melding: {error_msg}\n"
                + "-" * 80 + "\n"
            ),
            'notify': None,
        }

    def _process_system_event(self, timestamp, system_event, info) -> str:
        """Verwerk een SystemEvents-regel en geef de opgemaakte tekst terug."""
        
        return (
            f"[{timestamp}] System Event ({system_event})\n"
            f"\U0001f525 Details: {info}\n"
            + "-" * 80 + "\n"
        )

    def format_notify_error(self, timestamp, component, stack_lines):
        """Hulpfunctie om de verzamelde stack trace intelligent te formatteren."""
        out = f"[{timestamp}] ⚠️ ASYNC TAAK GEFAALD ({component})\n"
        
        # Probeer de hoofd-exceptie te vinden (meestal de eerste of tweede regel)
        main_fault = "Task Canceled / Time-out"
        for line in stack_lines:
            if "Exception:" in line:
                main_fault = line
                break
        out += f"💥 Oorzaak: {main_fault}\n"
        
        # Intelligent zoeken: Waar ging het in NINA écht mis in deze stack trace?
        context_hints = []
        for line in stack_lines:
            if "CoolCamera" in line:
                context_hints.append("Camera Koeling (CoolCamera)")
            if "RegulateTemperature" in line:
                context_hints.append("Temperatuurregeling (RegulateTemperature)")
            if "CaptureImage" in line:
                context_hints.append("Opname maken (CaptureImage)")
            if "Slew" in line:
                context_hints.append("Telescoopbesturing / Slewing")
        
        # Verwijder dubbele hints en voeg ze toe
        context_hints = list(set(context_hints))
        if context_hints:
            out += f"🎯 Getroffen actie(s): {', '.join(context_hints)}\n"
        
        # Optioneel: Voeg de volledige stack trace ingeklapt/of als referentie toe
        out += "📋 Volledige trace:\n"
        for line in stack_lines:
            # Alleen de relevante 'at NINA...' regels tonen om het overzichtelijk te houden
            if "at NINA" in line:
                # Kort het pad een beetje in voor de leesbaarheid
                short_line = line.split(" in /_")[0] if " in /_" in line else line
                out += f"   {short_line}\n"
                
        out += "-" * 80 + "\n"
        return out

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        self.setWindowTitle("NINA USB Device Watcher Analyzer")
        self.setGeometry(100, 100, 900, 600)

        # Layout elementen
        self.label = QLabel("Selecteer een NINA logbestand om te analyseren:", self)
        self.btn_open = QPushButton("Blader naar Logbestand...", self)
        self.btn_open.clicked.connect(self.open_file_dialog)
        
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setValue(0)
        
        self.text_edit = QTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlaceholderText("De resultaten verschijnen hier...")

        # Knoppenbalk bovenaan
        top_layout = QHBoxLayout()
        top_layout.addWidget(self.label)
        top_layout.addWidget(self.btn_open)

        # Hoofd layout
        main_layout = QVBoxLayout()
        main_layout.addLayout(top_layout)
        main_layout.addWidget(self.progress_bar)
        main_layout.addWidget(self.text_edit)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

    def open_file_dialog(self):
        # Start automatisch in de standaard NINA log-map
        default_path = os.path.expandvars(r'%LOCALAPPDATA%\NINA\Logs')
        if not os.path.exists(default_path):
            default_path = os.path.expanduser("~")

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open NINA Logbestand", default_path, "Log Bestanden (*.log);;Alle Bestanden (*.*)"
        )

        if file_path:
            self.text_edit.setText(f"Bezig met analyseren van: {os.path.basename(file_path)}...\nMoment, apparaten worden gekoppeld via Windows hardware ID's indien nodig...\n\n")
            self.btn_open.setEnabled(False)
            self.progress_bar.setValue(0)

            # Start de worker thread zodat de GUI soepel blijft draaien
            self.worker = LogParserWorker(file_path)
            self.worker.progress_signal.connect(self.progress_bar.setValue)
            self.worker.result_signal.connect(self.text_edit.append)
            self.worker.finished_signal.connect(self.analysis_finished)
            self.worker.start()

    def analysis_finished(self):
        self.btn_open.setEnabled(True)
        self.progress_bar.setValue(100)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Zorg voor een clean Windows-achtig uiterlijk
    app.setStyle('Fusion')
    win = MainWindow()
    win.show()
    sys.exit(app.exec_insite() if hasattr(app, 'exec_insite') else app.exec_())