"""
Printer module for photobooth.

Uses win32print + pure ctypes GDI calls to print.
Uses a saved DEVMODE blob (captured via the driver's own preferences dialog)
to guarantee that driver-specific settings like split/cut are preserved.

Flow:
1. User clicks "Printer instellen" in settings → driver dialog opens
2. User selects paper size (e.g. "4x6 Split 2"), clicks OK
3. Full DEVMODE (incl. driver-private bytes) is saved to disk
4. Every print job loads that saved DEVMODE and passes it to CreateDCW
"""

import os
import struct
import ctypes
from ctypes import wintypes

from PIL import Image

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtPrintSupport import QPrinter, QPrintDialog

import win32print
import config


class PrinterError(Exception):
    pass


def get_available_printers():
    """Return a list of available printer names."""
    printers = win32print.EnumPrinters(
        win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    )
    return [p[2] for p in printers]


def find_printer(name_contains):
    """Find a printer whose name contains the given string (case-insensitive).

    Returns the exact printer name, or None.
    """
    search = name_contains.lower()
    for name in get_available_printers():
        if search in name.lower():
            return name
    return None


def _devmode_path(printer_name):
    """Return the path where the saved DEVMODE blob is stored."""
    safe_name = "".join(c if c.isalnum() else "_" for c in printer_name)
    return os.path.join(config.DATA_DIR, f"printer_devmode_{safe_name}.bin")


def capture_printer_devmode(printer_name, hwnd=None):
    """Open the printer driver's preferences dialog and save the resulting DEVMODE.

    This shows the HiTi (or other printer) driver's own UI where the user
    can select paper size, split/cut mode, media type, etc.
    The full DEVMODE (including driver-private bytes) is saved to disk.

    Args:
        printer_name: Name (or partial name) of the printer
        hwnd: Parent window handle (optional, for dialog positioning)

    Returns:
        (ok, message) tuple
    """
    exact_name = find_printer(printer_name)
    if not exact_name:
        return False, f"Printer '{printer_name}' niet gevonden"

    try:
        hprinter = win32print.OpenPrinter(exact_name)
    except Exception as e:
        return False, f"Kan printer niet openen: {e}"

    try:
        _winspool = ctypes.WinDLL('winspool.drv')
        _winspool.DocumentPropertiesW.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD
        ]
        _winspool.DocumentPropertiesW.restype = ctypes.c_long

        # Use c_void_p to avoid 64-bit handle overflow
        _hprinter = ctypes.c_void_p(int(hprinter))
        _hwnd = ctypes.c_void_p(hwnd) if hwnd else None

        # Get required buffer size
        dm_size = _winspool.DocumentPropertiesW(
            _hwnd, _hprinter, exact_name, None, None, 0
        )
        if dm_size < 0:
            return False, "Kan DEVMODE grootte niet bepalen"

        devmode_buf = ctypes.create_string_buffer(dm_size)

        # Show the driver's own preferences dialog (DM_IN_PROMPT)
        # and capture the result (DM_OUT_BUFFER)
        DM_IN_PROMPT = 0x04
        DM_OUT_BUFFER = 0x02
        result = _winspool.DocumentPropertiesW(
            _hwnd, _hprinter, exact_name,
            devmode_buf, None,
            DM_IN_PROMPT | DM_OUT_BUFFER
        )

        if result != 1:  # IDOK = 1
            return False, "Dialoog geannuleerd"

        # Save the full DEVMODE blob to disk
        path = _devmode_path(exact_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(devmode_buf.raw)

        # Log what we captured
        dm_struct_size = struct.unpack_from('<H', devmode_buf, 68)[0]
        dm_extra = struct.unpack_from('<H', devmode_buf, 70)[0]
        dm_paper = struct.unpack_from('<h', devmode_buf, 78)[0]
        print(f"[PRINTER] DEVMODE opgeslagen: {dm_size}B "
              f"(struct={dm_struct_size}, extra={dm_extra}, paper={dm_paper})")
        print(f"[PRINTER] Opgeslagen naar: {path}")

        return True, f"Printer instellingen opgeslagen ({dm_size} bytes)"

    except Exception as e:
        return False, f"Fout bij opslaan instellingen: {e}"
    finally:
        win32print.ClosePrinter(hprinter)


def load_saved_devmode(printer_name):
    """Load a previously saved DEVMODE blob for the given printer.

    Returns the raw bytes, or None if no saved DEVMODE exists.
    """
    exact_name = find_printer(printer_name)
    if not exact_name:
        return None

    path = _devmode_path(exact_name)
    if not os.path.isfile(path):
        print(f"[PRINTER] Geen opgeslagen DEVMODE gevonden: {path}")
        return None

    with open(path, 'rb') as f:
        data = f.read()

    dm_struct_size = struct.unpack_from('<H', data, 68)[0]
    dm_extra = struct.unpack_from('<H', data, 70)[0]
    dm_paper = struct.unpack_from('<h', data, 78)[0]
    print(f"[PRINTER] DEVMODE geladen: {len(data)}B "
          f"(struct={dm_struct_size}, extra={dm_extra}, paper={dm_paper})")
    return data


def has_saved_devmode(printer_name):
    """Check if there's a saved DEVMODE for this printer."""
    exact_name = find_printer(printer_name)
    if not exact_name:
        return False
    return os.path.isfile(_devmode_path(exact_name))


def check_printer_status(printer_name):
    """Check if printer is online and ready.

    Returns (ok, message) tuple.
    ok=True means printer is ready, ok=False means there's a problem.
    """
    exact_name = find_printer(printer_name)
    if exact_name is None:
        available = get_available_printers()
        return False, (
            f"Printer '{printer_name}' niet gevonden.\n"
            f"Beschikbare printers: {', '.join(available) if available else 'Geen'}"
        )

    try:
        hprinter = win32print.OpenPrinter(exact_name)
    except Exception as e:
        return False, f"Kan printer niet openen: {exact_name}\n{e}"

    try:
        info = win32print.GetPrinter(hprinter, 2)
        status = info.get("Status", 0)
    finally:
        win32print.ClosePrinter(hprinter)

    # Decode status flags
    # See: https://learn.microsoft.com/en-us/windows/win32/printdocs/printer-info-2
    problems = []
    status_flags = {
        0x00000001: "Gepauzeerd",
        0x00000002: "Fout",
        0x00000004: "Wordt verwijderd",
        0x00000008: "Papier vastgelopen",
        0x00000010: "Geen papier",
        0x00000020: "Handmatige invoer nodig",
        0x00000040: "Papier probleem",
        0x00000080: "Offline",
        0x00000200: "Geheugen vol",
        0x00000400: "Klep open",
        0x00000800: "Server onbekend",
        0x00001000: "Energiebesparing",
        0x00010000: "Bezig met verwerken",
        0x00040000: "Opwarmen",
        0x00080000: "Weinig toner/inkt",
        0x00100000: "Geen toner/inkt",
        0x00200000: "Pagina niet geprint",
        0x00400000: "Interventie nodig",
        0x00800000: "Uitgeschakeld",
    }

    for flag, description in status_flags.items():
        if status & flag:
            problems.append(description)

    if problems:
        return False, f"Printer '{exact_name}': {', '.join(problems)}"

    return True, f"Printer '{exact_name}' is gereed"


def wait_for_job_completion(printer_name, job_start_time, timeout=30):
    """Monitor print job queue after sending a job.

    Checks if the job actually starts printing or hits an error.
    Returns (ok, message) tuple.
    """
    import time

    exact_name = find_printer(printer_name)
    if not exact_name:
        return False, "Printer niet gevonden"

    # Give the spooler a moment to register the job
    time.sleep(0.5)

    t0 = time.time()
    last_status = ""
    job_seen = False

    while time.time() - t0 < timeout:
        try:
            hprinter = win32print.OpenPrinter(exact_name)
            try:
                jobs = win32print.EnumJobs(hprinter, 0, 20, 1)
            finally:
                win32print.ClosePrinter(hprinter)
        except Exception:
            time.sleep(1)
            continue

        if not jobs:
            if job_seen:
                # Job was in queue and is now gone = completed
                return True, "Print job voltooid"
            # Job may not have appeared yet
            time.sleep(0.5)
            continue

        job_seen = True

        # Check most recent job
        for job in jobs:
            job_status = job.get("Status", 0)

            # Job error flags
            JOB_STATUS_ERROR = 0x00000002
            JOB_STATUS_OFFLINE = 0x00000020
            JOB_STATUS_PAPEROUT = 0x00000040
            JOB_STATUS_BLOCKED = 0x00000200

            error_flags = {
                JOB_STATUS_ERROR: "Print job fout",
                JOB_STATUS_OFFLINE: "Printer offline",
                JOB_STATUS_PAPEROUT: "Geen papier",
                JOB_STATUS_BLOCKED: "Print job geblokkeerd",
            }

            for flag, msg in error_flags.items():
                if job_status & flag:
                    return False, msg

            # Job is printing or spooling = good
            JOB_STATUS_PRINTING = 0x00000010
            JOB_STATUS_PRINTED = 0x00000080
            JOB_STATUS_COMPLETE = 0x00001000

            if job_status & (JOB_STATUS_PRINTED | JOB_STATUS_COMPLETE):
                return True, "Print job voltooid"

            if job_status & JOB_STATUS_PRINTING:
                last_status = "Bezig met printen..."

        time.sleep(1)

    # Timeout — job is probably still printing (slow printer)
    if job_seen:
        return True, f"Print job verzonden ({last_status or 'in wachtrij'})"
    return False, "Print job niet gevonden in wachtrij"


def print_photo(image_path, printer_name, copies=1):
    """
    Print a photo to the specified printer using Windows GDI.

    Checks printer status before printing, monitors job after sending.
    Reads the printer driver's own DEVMODE — does NOT override any
    settings (paper size, cutting, split, media type, etc.).

    Args:
        image_path: Path to the image file
        printer_name: Name (or partial name) of the target printer
        copies: Number of copies to print (default 1)

    Returns:
        True on success, raises PrinterError on failure
    """
    # 1. Check printer status before sending
    ok, status_msg = check_printer_status(printer_name)
    if not ok:
        raise PrinterError(status_msg)
    print(f"[PRINTER] Status: {status_msg}")

    # Find the exact printer name
    exact_name = find_printer(printer_name)
    if exact_name is None:
        available = get_available_printers()
        raise PrinterError(
            f"Printer '{printer_name}' niet gevonden.\n"
            f"Beschikbare printers: {', '.join(available) if available else 'Geen'}"
        )

    # Load the image with PIL
    if not os.path.exists(image_path):
        raise PrinterError(f"Kan afbeelding niet laden: {image_path}")

    try:
        pil_image = Image.open(image_path)
        pil_image = pil_image.convert("RGB")
    except Exception as e:
        raise PrinterError(f"Kan afbeelding niet laden: {image_path}\n{e}")

    img_w, img_h = pil_image.size
    print(f"[PRINTER] Afbeelding: {img_w}x{img_h}px, "
          f"Printer: {exact_name}, Kopieen: {copies}")

    try:
        # Load saved DEVMODE (captured via driver dialog) if available
        # This preserves ALL driver settings: split/cut, paper size, media, etc.
        saved_devmode = load_saved_devmode(printer_name)

        _gdi32 = ctypes.windll.gdi32
        _gdi32.CreateDCW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p
        ]
        _gdi32.CreateDCW.restype = ctypes.c_void_p

        if saved_devmode:
            # Use saved DEVMODE — create a mutable buffer from saved bytes
            devmode_buf = ctypes.create_string_buffer(saved_devmode)

            # BELANGRIJK: dmCopies in DEVMODE NIET aanpassen!
            # De for-loop verderop verstuurt zelf één page per kopie. Als we
            # hier ook dmCopies=N zouden zetten, dupliceert de driver elke
            # page nog eens, met 2x het aantal prints tot gevolg (bug fix
            # v2.39 — eerder gaf "2 kopieën" 4 prints af op HiTi P525L).

            hdc = _gdi32.CreateDCW("WINSPOOL", exact_name, None, devmode_buf)
            if not hdc:
                raise PrinterError("Kan printer DC niet aanmaken met opgeslagen DEVMODE")
            print(f"[PRINTER] DC aangemaakt met OPGESLAGEN DEVMODE ({len(saved_devmode)}B)")
        else:
            # No saved DEVMODE — use NULL (driver defaults)
            hdc = _gdi32.CreateDCW("WINSPOOL", exact_name, None, None)
            if not hdc:
                raise PrinterError("Kan printer DC niet aanmaken")
            print(f"[PRINTER] DC aangemaakt met NULL DEVMODE (driver defaults) "
                  f"— gebruik 'Printer instellen' om split/cut op te slaan")

        try:
            # Get printable area (in printer pixels)
            HORZRES = 8    # win32con.HORZRES
            VERTRES = 10   # win32con.VERTRES
            LOGPIXELSX = 88
            LOGPIXELSY = 90
            _gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
            _gdi32.GetDeviceCaps.restype = ctypes.c_int
            printable_w = _gdi32.GetDeviceCaps(hdc, HORZRES)
            printable_h = _gdi32.GetDeviceCaps(hdc, VERTRES)
            dpi_x = _gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
            dpi_y = _gdi32.GetDeviceCaps(hdc, LOGPIXELSY)
            inch_w = printable_w / dpi_x if dpi_x else 0
            inch_h = printable_h / dpi_y if dpi_y else 0
            print(f"[PRINTER] Printbaar gebied: {printable_w}x{printable_h}px "
                  f"({inch_w:.1f}x{inch_h:.1f} inch @ {dpi_x}x{dpi_y} DPI)")

            # Auto-rotate: if image orientation doesn't match page orientation
            img_is_landscape = img_w > img_h
            page_is_landscape = printable_w > printable_h
            if img_is_landscape != page_is_landscape:
                pil_image = pil_image.rotate(90, expand=True)
                img_w, img_h = pil_image.size
                print(f"[PRINTER] Auto-rotatie: nu {img_w}x{img_h}px")

            # Scale image to fit printable area (keep aspect ratio)
            scale_x = printable_w / img_w
            scale_y = printable_h / img_h
            scale = min(scale_x, scale_y)
            dest_w = int(img_w * scale)
            dest_h = int(img_h * scale)

            # Center on page
            dest_x = (printable_w - dest_w) // 2
            dest_y = (printable_h - dest_h) // 2

            # Resize the image for the printer
            resized = pil_image.resize((dest_w, dest_h), Image.LANCZOS)

            # Convert to BMP data for Windows GDI
            bmp_info = _create_bmp_info(dest_w, dest_h)
            bmp_data = resized.tobytes("raw", "BGR")
            # Pad rows to 4-byte boundary
            row_size = dest_w * 3
            pad = (4 - (row_size % 4)) % 4
            if pad:
                padded_data = bytearray()
                for y in range(dest_h):
                    row_start = y * row_size
                    padded_data.extend(bmp_data[row_start:row_start + row_size])
                    padded_data.extend(b'\x00' * pad)
                bmp_data = bytes(padded_data)

            # DOCINFO structure for StartDocW
            class DOCINFOW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_int),
                    ("lpszDocName", wintypes.LPCWSTR),
                    ("lpszOutput", wintypes.LPCWSTR),
                    ("lpszDatatype", wintypes.LPCWSTR),
                    ("fwType", wintypes.DWORD),
                ]

            doc_info = DOCINFOW()
            doc_info.cbSize = ctypes.sizeof(DOCINFOW)
            doc_info.lpszDocName = os.path.basename(image_path)
            doc_info.lpszOutput = None
            doc_info.lpszDatatype = None
            doc_info.fwType = 0

            # Start print job — pure ctypes, no win32ui interference
            _gdi32.StartDocW.argtypes = [ctypes.c_void_p, ctypes.POINTER(DOCINFOW)]
            _gdi32.StartDocW.restype = ctypes.c_int
            job_id = _gdi32.StartDocW(hdc, ctypes.byref(doc_info))
            if job_id <= 0:
                raise PrinterError(f"StartDoc mislukt (ret={job_id})")

            _gdi32.StartPage.argtypes = [ctypes.c_void_p]
            _gdi32.StartPage.restype = ctypes.c_int
            _gdi32.StretchDIBits.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_void_p, ctypes.c_void_p,
                wintypes.UINT, wintypes.DWORD,
            ]
            _gdi32.StretchDIBits.restype = ctypes.c_int
            _gdi32.EndPage.argtypes = [ctypes.c_void_p]
            _gdi32.EndPage.restype = ctypes.c_int

            DIB_RGB_COLORS = 0
            SRCCOPY = 0x00CC0020

            # Print each copy as a separate page (for split mode: each
            # page fills one half of the sheet)
            for copy_nr in range(copies):
                if _gdi32.StartPage(hdc) <= 0:
                    raise PrinterError(f"StartPage mislukt (kopie {copy_nr+1})")

                _gdi32.StretchDIBits(
                    hdc,
                    dest_x, dest_y, dest_w, dest_h,
                    0, 0, dest_w, dest_h,
                    bmp_data,
                    bmp_info,
                    DIB_RGB_COLORS,
                    SRCCOPY,
                )

                _gdi32.EndPage(hdc)
                print(f"[PRINTER] Pagina {copy_nr+1}/{copies} verzonden")

            _gdi32.EndDoc.argtypes = [ctypes.c_void_p]
            _gdi32.EndDoc.restype = ctypes.c_int
            _gdi32.EndDoc(hdc)

        finally:
            _gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
            _gdi32.DeleteDC.restype = wintypes.BOOL
            _gdi32.DeleteDC(hdc)

    except PrinterError:
        raise
    except Exception as e:
        raise PrinterError(f"Print fout: {e}")

    print(f"[PRINTER] Verzonden naar {exact_name}")

    # 2. Monitor job status after sending (max 15s)
    import time as _t
    job_ok, job_msg = wait_for_job_completion(printer_name, _t.time(), timeout=15)
    if job_ok:
        print(f"[PRINTER] {job_msg}")
    else:
        print(f"[PRINTER] WAARSCHUWING: {job_msg}")
        raise PrinterError(f"Print probleem: {job_msg}")

    return True


def _create_bmp_info(width, height):
    """Create a BITMAPINFO structure for StretchDIBits."""

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height  # negative = top-down DIB
    header.biPlanes = 1
    header.biBitCount = 24
    header.biCompression = 0  # BI_RGB
    header.biSizeImage = 0
    header.biXPelsPerMeter = 0
    header.biYPelsPerMeter = 0
    header.biClrUsed = 0
    header.biClrImportant = 0

    return bytes(header)


def select_printer_dialog(parent=None):
    """Open the Windows printer selection dialog.

    Returns the selected printer name, or None if cancelled.
    """
    printer = QPrinter()
    dialog = QPrintDialog(printer, parent)
    dialog.setWindowTitle("Selecteer printer")
    if dialog.exec_() == QPrintDialog.Accepted:
        return printer.printerName()
    return None


class PrintThread(QThread):
    """Background thread for printing a photo with status monitoring."""

    print_complete = pyqtSignal()
    print_failed = pyqtSignal(str)
    print_status = pyqtSignal(str)  # Status updates during printing

    def __init__(self, image_path, printer_name, copies=1):
        super().__init__()
        self.image_path = image_path
        self.printer_name = printer_name
        self.copies = copies

    def run(self):
        try:
            # Pre-check printer status
            self.print_status.emit("Printer controleren...")
            ok, msg = check_printer_status(self.printer_name)
            if not ok:
                self.print_failed.emit(msg)
                return

            self.print_status.emit("Bezig met printen...")
            print_photo(self.image_path, self.printer_name, self.copies)
            self.print_complete.emit()
        except PrinterError as e:
            self.print_failed.emit(str(e))
        except Exception as e:
            self.print_failed.emit(f"Print fout: {e}")
