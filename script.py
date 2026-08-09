#!/usr/bin/env python3
"""
Post-processa un file G-code (o un .gcode.3mf di Bambu Studio) per spegnere
il piano riscaldato (bed) quando mancano circa N minuti alla fine della stampa.

Supporta i formati di stima tempo piu' comuni generati dagli slicer:
- M73 P<percentuale> R<minuti_rimanenti>
  (PrusaSlicer, SuperSlicer, Bambu Studio, OrcaSlicer con "Firmware/M73" attivo)
- ;TIME_ELAPSED:<secondi>
  (Cura)

Uso:
    python3 bed_off_gcode.py input.gcode -o output.gcode -m 5
    python3 bed_off_gcode.py input.gcode.3mf -o output.gcode.3mf -m 5

Note:
- L'accuratezza dipende dalla stima tempo dello slicer: e' una previsione,
  non un tempo reale misurato dalla stampante. Con retract/travel diversi
  dal previsto, o pause, lo scostamento puo' essere di qualche minuto.
- Per i file .gcode.3mf (archivio zip usato da Bambu Studio), lo script
  individua automaticamente il/i file gcode interni (es. Metadata/plate_1.gcode)
  e li modifica mantenendo intatta tutta la struttura del 3mf.
- Se il file non contiene ne' M73 ne' TIME_ELAPSED, lo script si ferma
  e ti dice come attivarli nello slicer.
"""
import argparse
import re
import sys
import zipfile

M73_RE = re.compile(r'^M73\s+P(?P<percent>[\d.]+)\s+R(?P<remaining>[\d.]+)', re.IGNORECASE)
TIME_ELAPSED_RE = re.compile(r';TIME_ELAPSED:(?P<elapsed>[\d.]+)')
BED_OFF_CMD = "M140 S0 ; bed spento automaticamente (~{m:g} min alla fine)\n"


def find_insert_point_m73(lines, minutes_before_end):
    """Cerca la prima riga M73 in cui il tempo rimanente (R, in minuti)
    scende sotto la soglia richiesta."""
    for i, line in enumerate(lines):
        m = M73_RE.match(line.strip())
        if m:
            remaining = float(m.group('remaining'))
            if remaining <= minutes_before_end:
                return i
    return None


def find_insert_point_time_elapsed(lines, minutes_before_end):
    """Usa i commenti TIME_ELAPSED (tempo cumulato, in secondi) di Cura.
    L'ultimo valore e' il tempo totale stimato; si cerca il punto in cui
    il tempo rimanente (totale - elapsed) scende sotto la soglia."""
    elapsed_lines = []
    for i, line in enumerate(lines):
        m = TIME_ELAPSED_RE.search(line)
        if m:
            elapsed_lines.append((i, float(m.group('elapsed'))))
    if not elapsed_lines:
        return None

    total_time = elapsed_lines[-1][1]
    threshold = total_time - minutes_before_end * 60
    for i, elapsed in elapsed_lines:
        if elapsed >= threshold:
            return i
    return None


def insert_bed_off(lines, minutes_before_end):
    """Restituisce (nuove_righe, metodo) oppure (None, None) se non trovato."""
    insert_idx = find_insert_point_m73(lines, minutes_before_end)
    method = "M73 R<minuti rimanenti>"

    if insert_idx is None:
        insert_idx = find_insert_point_time_elapsed(lines, minutes_before_end)
        method = "TIME_ELAPSED (Cura)"

    if insert_idx is None:
        return None, None

    lines = list(lines)
    lines.insert(insert_idx, BED_OFF_CMD.format(m=minutes_before_end))
    return lines, method


def no_estimate_error():
    print("ERRORE: nessuna stima di tempo trovata (ne' M73 ne' TIME_ELAPSED).")
    print("Attivala nello slicer:")
    print(" - PrusaSlicer/OrcaSlicer/SuperSlicer/Bambu Studio: normalmente M73 e' gia' generato di default.")
    print(" - Cura: normalmente e' gia' attivo; controlla che nessun plugin lo rimuova.")
    sys.exit(1)


def process_plain_gcode(input_path, output_path, minutes_before_end):
    with open(input_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    new_lines, method = insert_bed_off(lines, minutes_before_end)
    if new_lines is None:
        no_estimate_error()

    with open(output_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

    print(f"Fatto. Comando M140 S0 inserito (metodo usato: {method}).")
    print(f"File salvato in: {output_path}")


def process_3mf(input_path, output_path, minutes_before_end):
    with zipfile.ZipFile(input_path, 'r') as zin:
        names = zin.namelist()
        gcode_names = [n for n in names if n.lower().endswith('.gcode')]

        if not gcode_names:
            print("ERRORE: nessun file .gcode trovato dentro l'archivio .gcode.3mf.")
            sys.exit(1)

        modified = {}
        methods = {}
        for name in gcode_names:
            raw = zin.read(name)
            text = raw.decode('utf-8', errors='ignore')
            lines = text.splitlines(keepends=True)

            new_lines, method = insert_bed_off(lines, minutes_before_end)
            if new_lines is None:
                # Nessuna stima in questo specifico file interno: lo lascio invariato.
                continue

            modified[name] = ''.join(new_lines).encode('utf-8')
            methods[name] = method

        if not modified:
            no_estimate_error()

        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = modified.get(item.filename, zin.read(item.filename))
                zout.writestr(item, data)

    for name, method in methods.items():
        print(f"Modificato '{name}' (metodo usato: {method}).")
    print(f"File salvato in: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Inserisce M140 S0 nel G-code (o .gcode.3mf) quando mancano circa N minuti alla fine della stampa."
    )
    parser.add_argument("input", help="File .gcode o .gcode.3mf di input")
    parser.add_argument("-o", "--output", help="File di output (default: <input>_bedoff.<ext>)")
    parser.add_argument("-m", "--minutes", type=float, default=5.0,
                         help="Minuti prima della fine in cui spegnere il bed (default: 5)")
    args = parser.parse_args()

    input_lower = args.input.lower()

    if input_lower.endswith('.gcode.3mf'):
        default_output = args.input[:-len('.gcode.3mf')] + "_bedoff.gcode.3mf"
        output_path = args.output or default_output
        process_3mf(args.input, output_path, args.minutes)
    elif input_lower.endswith('.3mf'):
        default_output = args.input[:-len('.3mf')] + "_bedoff.3mf"
        output_path = args.output or default_output
        process_3mf(args.input, output_path, args.minutes)
    elif input_lower.endswith('.gcode'):
        default_output = args.input[:-len('.gcode')] + "_bedoff.gcode"
        output_path = args.output or default_output
        process_plain_gcode(args.input, output_path, args.minutes)
    else:
        default_output = args.input + "_bedoff.gcode"
        output_path = args.output or default_output
        process_plain_gcode(args.input, output_path, args.minutes)


if __name__ == "__main__":
    main()
