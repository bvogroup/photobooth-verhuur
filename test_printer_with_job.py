"""
Stuur een dummy-print job naar de QW410 en monitor de spooler-status.

Doel: vaststellen of de spooler tijdens een ACTIEVE job wel detail-bits zet
voor cover-open / paper-out / ribbon-out via PRINTER_STATUS_* of JOB_STATUS_*.
"""
import time
import sys
import win32print
from datetime import datetime

PRINTER_NAME = "DP-QW410 (Kopie 1)"

PRINTER_STATUS_FLAGS = {
    0x00000001: "PAUSED", 0x00000002: "ERROR", 0x00000004: "PENDING_DELETION",
    0x00000008: "PAPER_JAM", 0x00000010: "PAPER_OUT", 0x00000020: "MANUAL_FEED",
    0x00000040: "PAPER_PROBLEM", 0x00000080: "OFFLINE", 0x00000100: "IO_ACTIVE",
    0x00000200: "BUSY", 0x00000400: "PRINTING", 0x00000800: "OUTPUT_BIN_FULL",
    0x00001000: "NOT_AVAILABLE", 0x00002000: "WAITING", 0x00004000: "PROCESSING",
    0x00008000: "INITIALIZING", 0x00010000: "WARMING_UP", 0x00020000: "TONER_LOW",
    0x00040000: "NO_TONER", 0x00080000: "PAGE_PUNT",
    0x00100000: "USER_INTERVENTION", 0x00200000: "OUT_OF_MEMORY",
    0x00400000: "DOOR_OPEN", 0x00800000: "SERVER_UNKNOWN", 0x01000000: "POWER_SAVE",
}
JOB_STATUS_FLAGS = {
    0x00000001: "PAUSED", 0x00000002: "ERROR", 0x00000004: "DELETING",
    0x00000008: "SPOOLING", 0x00000010: "PRINTING", 0x00000020: "OFFLINE",
    0x00000040: "PAPEROUT", 0x00000080: "PRINTED", 0x00000100: "DELETED",
    0x00000200: "BLOCKED_DEVQ", 0x00000400: "USER_INTERVENTION",
    0x00000800: "RESTART", 0x00001000: "COMPLETE", 0x00002000: "RETAINED",
}


def decode(value, table):
    return [n for b, n in table.items() if value & b]


def snapshot(h):
    info = win32print.GetPrinter(h, 2)
    try:
        jobs = win32print.EnumJobs(h, 0, 999, 1)
    except Exception:
        jobs = []
    return info, jobs


def fmt(info, jobs):
    s = info.get("Status", 0)
    pflags = decode(s, PRINTER_STATUS_FLAGS)
    parts = [f"PRT Status=0x{s:08x} ({','.join(pflags) if pflags else 'NONE'})"]
    parts.append(f"pStatus={info.get('pStatus')!r}")
    parts.append(f"cJobs={info.get('cJobs', 0)}")
    line = " | ".join(parts)
    for j in jobs or []:
        js = j.get("Status", 0)
        jf = decode(js, JOB_STATUS_FLAGS)
        line += (f"\n    JOB#{j.get('JobId')} Status=0x{js:04x} "
                 f"({','.join(jf) if jf else 'NONE'}) "
                 f"pStatus={j.get('pStatus')!r} "
                 f"pages={j.get('TotalPages', 0)}/{j.get('PagesPrinted', 0)}")
    return line


def send_raw(h, name, data):
    """Stuur RAW data naar de printer (geen GDI, ook ongeldige data is OK
    voor onze test — de driver/printer hoeft niet te slagen)."""
    job_id = win32print.StartDocPrinter(h, 1, (name, None, "RAW"))
    try:
        win32print.StartPagePrinter(h)
        win32print.WritePrinter(h, data)
        win32print.EndPagePrinter(h)
    finally:
        win32print.EndDocPrinter(h)
    return job_id


def main():
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    print(f"================================================================")
    print(f"  Printer: {PRINTER_NAME}")
    print(f"  Stuur dummy RAW job + monitor 25s")
    print(f"================================================================")

    h = win32print.OpenPrinter(PRINTER_NAME)
    try:
        # Snapshot vóór de job
        info, jobs = snapshot(h)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] PRE-JOB: {fmt(info, jobs)}")

        # Stuur kleine dummy RAW
        try:
            jid = send_raw(h, "QW410 status-test", b"\x00" * 32)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Job verstuurd, JobId={jid}")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] SendRaw FOUT: {e}")
            return

        # Monitor 25 seconden
        last = ""
        end_ts = time.monotonic() + 25
        while time.monotonic() < end_ts:
            info, jobs = snapshot(h)
            line = fmt(info, jobs)
            if line != last:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {line}")
                last = line
            time.sleep(0.5)

        # Cleanup: verwijder de job mocht-ie blijven hangen
        for j in jobs or []:
            try:
                win32print.SetJob(h, j.get("JobId"), 0, None, win32print.JOB_CONTROL_DELETE)
                print(f"  cleanup: job {j.get('JobId')} delete gevraagd")
            except Exception as e:
                print(f"  cleanup fout: {e}")
    finally:
        win32print.ClosePrinter(h)


if __name__ == "__main__":
    main()
