"""
Test-script: poll DNP QW410 status via Windows print spooler.

Doel: empirisch vaststellen welke PRINTER_STATUS_* bits de Windows-driver
voor de QW410 daadwerkelijk zet bij verschillende foutcondities.

Gebruik:
  python test_printer_status.py
  (laat draaien, doe 1 voor 1 de 4 testen, observeer output)

Test-checklist:
  TEST 1: baseline — printer aan, klep dicht, papier in
          → verwacht: ready / Status=0
  TEST 2: trek USB-kabel los
          → wat zien we?
  TEST 3: USB terug, open de klep / top deksel
          → wat zien we?
  TEST 4: USB terug, klep dicht, haal de papier-cassette eruit
          → wat zien we?
"""

import time
import win32print
from datetime import datetime

# ── PRINTER_STATUS_* bits (uit winspool.h) ─────────────────────────────
PRINTER_STATUS_FLAGS = {
    0x00000001: "PAUSED",
    0x00000002: "ERROR",
    0x00000004: "PENDING_DELETION",
    0x00000008: "PAPER_JAM",
    0x00000010: "PAPER_OUT",
    0x00000020: "MANUAL_FEED",
    0x00000040: "PAPER_PROBLEM",
    0x00000080: "OFFLINE",
    0x00000100: "IO_ACTIVE",
    0x00000200: "BUSY",
    0x00000400: "PRINTING",
    0x00000800: "OUTPUT_BIN_FULL",
    0x00001000: "NOT_AVAILABLE",
    0x00002000: "WAITING",
    0x00004000: "PROCESSING",
    0x00008000: "INITIALIZING",
    0x00010000: "WARMING_UP",
    0x00020000: "TONER_LOW",
    0x00040000: "NO_TONER",
    0x00080000: "PAGE_PUNT",
    0x00100000: "USER_INTERVENTION",
    0x00200000: "OUT_OF_MEMORY",
    0x00400000: "DOOR_OPEN",
    0x00800000: "SERVER_UNKNOWN",
    0x01000000: "POWER_SAVE",
}

# ── JOB_STATUS_* bits ──────────────────────────────────────────────────
JOB_STATUS_FLAGS = {
    0x00000001: "PAUSED",
    0x00000002: "ERROR",
    0x00000004: "DELETING",
    0x00000008: "SPOOLING",
    0x00000010: "PRINTING",
    0x00000020: "OFFLINE",
    0x00000040: "PAPEROUT",
    0x00000080: "PRINTED",
    0x00000100: "DELETED",
    0x00000200: "BLOCKED_DEVQ",
    0x00000400: "USER_INTERVENTION",
    0x00000800: "RESTART",
    0x00001000: "COMPLETE",
    0x00002000: "RETAINED",
}

# ── Printer-attributes (informatief, niet runtime status) ──────────────
PRINTER_ATTRIBUTE_FLAGS = {
    0x00000004: "SHARED",
    0x00000008: "NETWORK",
    0x00000010: "HIDDEN",
    0x00000020: "LOCAL",
    0x00000040: "ENABLE_DEVQ",
    0x00000080: "KEEPPRINTEDJOBS",
    0x00000100: "DO_COMPLETE_FIRST",
    0x00000200: "WORK_OFFLINE",
    0x00000400: "ENABLE_BIDI",
    0x00000800: "RAW_ONLY",
    0x00001000: "PUBLISHED",
    0x00002000: "FAX",
    0x00004000: "TS",
    0x00008000: "PUSHED_USER",
    0x00010000: "PUSHED_MACHINE",
    0x00020000: "MACHINE",
    0x00040000: "FRIENDLY_NAME",
    0x00080000: "TS_GENERIC_DRIVER",
    0x00100000: "PER_USER",
    0x00200000: "ENTERPRISE_CLOUD",
}


def decode_flags(value, table):
    """Return list of flag names set in the bitmask."""
    return [name for bit, name in table.items() if value & bit]


def find_printer():
    """Vind een printer waarvan de naam 'QW410' of 'DP-QW' bevat.

    Prefereert de KOPIE 1 (= actieve printer per booth-config)
    boven andere QW410 instances.
    """
    printers = win32print.EnumPrinters(
        win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    )
    candidates = []
    for p in printers:
        name = p[2]
        if any(s in name.upper() for s in ("QW410", "DP-QW", "DNP")):
            candidates.append(name)
    # Prefereer de exacte naam uit settings.json
    for c in candidates:
        if c == "DP-QW410 (Kopie 1)":
            return [c]
    return candidates


def get_printer_info(printer_name):
    """Haal PRINTER_INFO_2 op via win32print.GetPrinter(level=2)."""
    h = win32print.OpenPrinter(printer_name)
    try:
        info = win32print.GetPrinter(h, 2)
    finally:
        win32print.ClosePrinter(h)
    return info


def get_jobs(printer_name):
    """Haal alle queued jobs op."""
    h = win32print.OpenPrinter(printer_name)
    try:
        jobs = win32print.EnumJobs(h, 0, 999, 1)
    finally:
        win32print.ClosePrinter(h)
    return jobs


def format_status_line(info, jobs):
    """Maak een eenregelige samenvatting van de status."""
    status_val = info.get("Status", 0)
    attr_val = info.get("Attributes", 0)
    cjobs = info.get("cJobs", 0)
    state_name = info.get("pPrinterName", "?")

    status_flags = decode_flags(status_val, PRINTER_STATUS_FLAGS)
    attr_flags = decode_flags(attr_val, PRINTER_ATTRIBUTE_FLAGS)

    parts = [
        f"Status=0x{status_val:08x}",
        f"({', '.join(status_flags) if status_flags else 'NONE'})",
        f"cJobs={cjobs}",
    ]
    if "WORK_OFFLINE" in attr_flags:
        parts.append("[ATTR:WORK_OFFLINE]")

    job_lines = []
    for j in (jobs or []):
        jstatus = j.get("Status", 0)
        jstr = j.get("pStatus") or ""
        jflags = decode_flags(jstatus, JOB_STATUS_FLAGS)
        job_lines.append(
            f"    job#{j.get('JobId')} Status=0x{jstatus:04x} "
            f"({', '.join(jflags) if jflags else 'NONE'}) pStatus={jstr!r}"
        )
    return " | ".join(parts), job_lines


def main():
    candidates = find_printer()
    if not candidates:
        # Fallback: gebruik DEFAULT printer
        try:
            default = win32print.GetDefaultPrinter()
            print(f"[!] Geen QW410/DNP printer gevonden via naam-match.")
            print(f"[!] Default printer: {default!r}")
            print(f"[!] Probeer alle printers...")
            all_printers = win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            )
            for p in all_printers:
                print(f"    {p[2]!r}")
            print()
        except Exception as e:
            print(f"[!] Kon default printer niet ophalen: {e}")
        return

    if len(candidates) > 1:
        print(f"[!] Meerdere DNP printers gevonden:")
        for c in candidates:
            print(f"    {c!r}")
        # Gebruik de eerste
    printer_name = candidates[0]
    print(f"================================================================")
    print(f"  Printer: {printer_name!r}")
    print(f"  Polling elke 1 seconde — Ctrl+C om te stoppen")
    print(f"================================================================")
    print()
    print("  TEST-CHECKLIST (doe ze 1 voor 1, observeer output):")
    print("  1. Baseline (niks doen, alleen kijken)")
    print("  2. Trek de USB-kabel los")
    print("  3. USB terug, open de klep / top deksel")
    print("  4. USB terug, klep dicht, haal de papier-cassette eruit")
    print()
    print("================================================================")

    last_summary = ""
    last_jobs_count = -1
    tick = 0
    while True:
        tick += 1
        try:
            info = get_printer_info(printer_name)
        except Exception as e:
            ts = datetime.now().strftime("%H:%M:%S")
            line = f"[{ts}] OpenPrinter/GetPrinter FOUT: {e}"
            if line != last_summary:
                print(line)
                last_summary = line
            time.sleep(1)
            continue

        try:
            jobs = get_jobs(printer_name)
        except Exception:
            jobs = []

        summary, job_lines = format_status_line(info, jobs)
        ts = datetime.now().strftime("%H:%M:%S")

        if summary != last_summary or len(job_lines) != last_jobs_count:
            print(f"[{ts}] {summary}")
            for jl in job_lines:
                print(jl)
            last_summary = summary
            last_jobs_count = len(job_lines)
        elif tick % 10 == 0:
            # Heartbeat elke 10s zodat we weten dat het script nog draait
            print(f"[{ts}] (heartbeat) {summary}")

        time.sleep(1)


def once():
    """Doe 1 snapshot en exit (voor test/diagnostic gebruik)."""
    import sys
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    candidates = find_printer()
    if not candidates:
        print("[!] Geen QW410 gevonden")
        return
    printer_name = candidates[0]
    print(f"Printer: {printer_name}")
    try:
        info = get_printer_info(printer_name)
    except Exception as e:
        print(f"OpenPrinter/GetPrinter FOUT: {e}")
        return
    try:
        jobs = get_jobs(printer_name)
    except Exception:
        jobs = []
    summary, job_lines = format_status_line(info, jobs)
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {summary}")
    for jl in job_lines:
        print(jl)
    # Extra raw debug
    print(f"  raw Status int     = {info.get('Status', 0)}")
    print(f"  raw Attributes int = {info.get('Attributes', 0)}")
    print(f"  pPrinterName       = {info.get('pPrinterName')!r}")
    print(f"  pPortName          = {info.get('pPortName')!r}")
    print(f"  pDriverName        = {info.get('pDriverName')!r}")
    print(f"  pStatus            = {info.get('pStatus')!r}")
    print(f"  cJobs              = {info.get('cJobs')}")


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        once()
    else:
        try:
            main()
        except KeyboardInterrupt:
            print("\n[STOPGEZET]")
