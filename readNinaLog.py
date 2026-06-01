import sys
import os
from PyQt5.QtWidgets import QApplication, QFileDialog

def parse_nina_logs():
    app = QApplication(sys.argv)

    # Pad naar NINA logs
    local_app_data = os.environ.get('LOCALAPPDATA')
    nina_log_path = os.path.join(local_app_data, 'NINA', 'logs')

    options = QFileDialog.Options()
    file_path, _ = QFileDialog.getOpenFileName(
        None, 
        "Selecteer NINA Log Bestand", 
        nina_log_path, 
        "Log Files (*.log);;All Files (*)", 
        options=options
    )

    if not file_path:
        return

    print(f"\n--- Analyse van Autofocus Triggers in: {os.path.basename(file_path)} ---\n")
    print(f"{'TIJDSTIP':<25} | {'TRIGGER TYPE':<25} | {'DETAILS / REDEN'}")
    print("-" * 100)

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            pending_explanation = "" # Buffer voor de HFR reden
            
            for line in file:
                # STAP 1: Zoek naar de reden (HFR increase check)
                if "Autofocus" in line and "ShouldTrigger" in line:
                    parts = line.split('|')
                    if len(parts) >= 6:
                        # We slaan de uitleg op voor de volgende regel
                        pending_explanation = parts[5].strip()
                    continue

                # STAP 2: Zoek naar de feitelijke start van de trigger
                if "SequenceTrigger.cs" in line and "AutofocusAfter" in line:
                    parts = line.split('|')
                    if len(parts) >= 6:
                        timestamp = parts[0]
                        full_msg = parts[5].strip() # "Starting Trigger: AutofocusAfterTimeTrigger, Amount: 30m"
                        
                        # Extraheer de info na de komma
                        detail_after_comma = ""
                        if "," in full_msg:
                            detail_after_comma = full_msg.split(',', 1)[1].strip()
                        
                        # Bepaal welk type trigger het was (Time, HFR, etc)
                        trigger_name = "Onbekend"
                        if "Starting Trigger:" in full_msg:
                            trigger_name = full_msg.split(':')[1].split(',')[0].strip()

                        # Toon de verzamelde informatie
                        output_msg = detail_after_comma
                        if pending_explanation:
                            output_msg = f"{detail_after_comma} ({pending_explanation})"
                        
                        print(f"{timestamp:<25} | {trigger_name:<25} | {output_msg}")
                        
                        # Reset de buffer voor de volgende ronde
                        pending_explanation = ""
                
                # Optioneel: reset pending_explanation als er te veel regels tussen zitten?
                # Voor nu laten we het staan tot de volgende SequenceTrigger.

    except Exception as e:
        print(f"Fout bij lezen bestand: {e}")

    input("\nSelectie voltooid. Druk op Enter om af te sluiten...")
    sys.exit(app.quit())

if __name__ == "__main__":
    parse_nina_logs()