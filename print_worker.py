"""
Subprocess print worker.

Runs print_photo() in an isolated process so Windows GDI calls
(CreateDC, StartDoc, StretchDIBits) cannot corrupt the main
application's HWND geometry when AA_EnableHighDpiScaling is on.

Usage:
    python print_worker.py <image_path> <printer_name> <copies> <data_dir>

Exit codes:
    0 = success
    1 = PrinterError (message on stderr)
    2 = unexpected error (message on stderr)

NOTE: In frozen exe mode, the --print-worker branch in splash_starter.pyw
removes _MEIPASS/cv2/ from sys.path before this module is loaded.
This prevents cv2/config.py and cv2/typing/ from shadowing the app's
config module and Python's stdlib typing module.
"""
import sys
import os


def main():
    if len(sys.argv) < 5:
        print("Usage: print_worker.py <image_path> <printer_name> <copies> <data_dir>",
              file=sys.stderr)
        sys.exit(2)

    image_path, printer_name, copies_str, data_dir = sys.argv[1:5]
    copies = int(copies_str)

    # Set DATA_DIR before importing printer (so load_saved_devmode finds DEVMODE).
    # Plain `import config` works here because splash_starter.pyw's --print-worker
    # handler already removed _MEIPASS/cv2/ from sys.path, so the FrozenImporter
    # finds the app's config (not cv2's config.py) correctly.
    import config
    config.DATA_DIR = data_dir

    from printer import print_photo, PrinterError
    try:
        print_photo(image_path, printer_name, copies)
    except PrinterError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
