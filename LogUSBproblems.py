import os

# Automatisch het juiste pad naar de NINA logs bepalen
log_dir = os.path.expandvars(r'%LOCALAPPDATA%\NINA\Logs')
search_term = "UsbDeviceWatcher"

print(f"Zoeken naar '{search_term}' in: {log_dir}\n" + "-"*50)

# Controleer of de map bestaat
if os.path.exists(log_dir):
    # Loop door alle bestanden in de map
    for root, dirs, files in os.walk(log_dir):
        for file in files:
            # Alleen in .log bestanden zoeken
            if file.endswith('.log'):
                file_path = os.path.join(root, file)
                
                try:
                    # Open het bestand met 'utf-8' en negeer eventuele vreemde tekens (errors='ignore')
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for line_num, line in enumerate(f, 1):
                            if search_term in line:
                                # Print de resultaten netjes uit
                                print(f"Bestand: {file} (Regel {line_num})")
                                print(f"  Tekst: {line.strip()}\n")
                except Exception as e:
                    print(f"Kon {file} niet lezen: {e}")
else:
    print("Fout: De opgegeven logmap bestaat niet.")