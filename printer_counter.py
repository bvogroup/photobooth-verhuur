"""
Printer ribbon/paper counter for HiTi P525L dye-sublimation printer.

Tracks remaining prints on the current ribbon cartridge.
HiTi P525L uses ribbon cartridges with 500 prints (4x6 inch).

Two modes:
1. Software counter: stored in JSON, decremented on each print
2. USB hardware query: reads actual remaining count via HiTi USB protocol
   (based on Gutenprint reverse-engineering of HiTi protocol)
"""

import json
import os
from datetime import datetime

import config

# HiTi USB constants (from Gutenprint backend_hiti.c)
HITI_VID = 0x0D16          # HiTi USB Vendor ID
HITI_P525L_PIDS = [
    0x0309,                 # P525L (common PID)
    0x030A,                 # P525L variant
    0x0007,                 # P520L / P525L alternate
]
CMD_HEADER = 0xA5           # Command header byte
CMD_RDS_RSUS = 0x040C       # Read Device Status: Request Supply Status


def _load_counter():
    """Load the printer counter from file."""
    if os.path.isfile(config.PRINTER_COUNTER_FILE):
        try:
            with open(config.PRINTER_COUNTER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "ribbon_capacity": config.PRINTER_RIBBON_CAPACITY,
        "prints_used": 0,
        "last_reset": datetime.now().isoformat(),
        "total_prints_ever": 0,
    }


def _save_counter(data):
    """Save the printer counter to file."""
    try:
        with open(config.PRINTER_COUNTER_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[PRINTER] Fout bij opslaan teller: {e}")


def read_usb_remaining():
    """Try to read remaining prints directly from HiTi printer via USB.

    Uses the protocol reverse-engineered by the Gutenprint project.
    Requires pyusb + libusb to be installed.

    Returns:
        int or None: Number of remaining prints, or None if reading failed.
    """
    try:
        import usb.core
        import usb.util
    except ImportError:
        print("[PRINTER] pyusb niet geinstalleerd - kan printer niet direct uitlezen")
        return None

    try:
        # Find HiTi printer
        dev = None
        for pid in HITI_P525L_PIDS:
            dev = usb.core.find(idVendor=HITI_VID, idProduct=pid)
            if dev:
                break

        if not dev:
            # Try to find any HiTi printer
            dev = usb.core.find(idVendor=HITI_VID)

        if not dev:
            print("[PRINTER] Geen HiTi printer gevonden via USB")
            return None

        print(f"[PRINTER] HiTi printer gevonden: VID={dev.idVendor:#06x} PID={dev.idProduct:#06x}")

        # Try to claim the interface
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except (usb.core.USBError, NotImplementedError):
            pass

        # Build CMD_RDS_RSUS command
        # Protocol: header(1) + pad(1) + cmd_hi(1) + cmd_lo(1) + len_hi(1) + len_lo(1) + data
        cmd_hi = (CMD_RDS_RSUS >> 8) & 0xFF  # 0x04
        cmd_lo = CMD_RDS_RSUS & 0xFF          # 0x0C
        payload = bytes([0x00])                # 1-byte argument
        data_len = len(payload)
        cmd_packet = bytes([
            CMD_HEADER,
            0x00,                              # padding
            cmd_hi,
            cmd_lo,
            (data_len >> 8) & 0xFF,
            data_len & 0xFF,
        ]) + payload

        # Find bulk OUT and IN endpoints
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]

        ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )
        ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
        )

        if not ep_out or not ep_in:
            print("[PRINTER] USB endpoints niet gevonden")
            return None

        # Send command
        ep_out.write(cmd_packet, timeout=2000)

        # Read response (expect header + 5-byte supply data)
        resp = ep_in.read(64, timeout=2000)

        if len(resp) >= 11:  # header(6) + supplies(5)
            supplies = resp[6:11]
            # supplies[0-1] typically contain remaining count (big-endian)
            remaining = (supplies[0] << 8) | supplies[1]
            ribbon_type = supplies[2]
            print(f"[PRINTER] USB uitlezing: {remaining} prints, ribbon type={ribbon_type:#04x}")
            return remaining

        print(f"[PRINTER] Onverwacht antwoord: {resp.hex()}")
        return None

    except Exception as e:
        print(f"[PRINTER] USB uitlezing mislukt: {e}")
        return None


def get_remaining_prints():
    """Get the number of remaining prints on the current ribbon.

    First tries to read from printer hardware via USB.
    Falls back to software counter if USB reading fails.
    """
    # Try hardware reading first (non-blocking, fast timeout)
    hw_remaining = read_usb_remaining()
    if hw_remaining is not None:
        # Sync software counter with hardware reading
        data = _load_counter()
        data["prints_used"] = data["ribbon_capacity"] - hw_remaining
        _save_counter(data)
        return hw_remaining

    # Fall back to software counter
    data = _load_counter()
    remaining = data["ribbon_capacity"] - data["prints_used"]
    return max(0, remaining)


def get_ribbon_capacity():
    """Get the total ribbon capacity."""
    data = _load_counter()
    return data["ribbon_capacity"]


def get_prints_used():
    """Get the number of prints used from current ribbon."""
    data = _load_counter()
    return data["prints_used"]


def decrement(copies=1):
    """Decrement the counter after a successful print.

    Args:
        copies: Number of copies printed (default 1).
    """
    data = _load_counter()
    data["prints_used"] += copies
    data["total_prints_ever"] += copies
    _save_counter(data)
    remaining = data["ribbon_capacity"] - data["prints_used"]
    print(f"[PRINTER] Print geteld: {copies}x | Resterend: {max(0, remaining)} / {data['ribbon_capacity']}")
    return max(0, remaining)


def reset_ribbon(capacity=None):
    """Reset the counter for a new ribbon cartridge.

    Args:
        capacity: Override ribbon capacity (default uses config value).
    """
    data = _load_counter()
    data["prints_used"] = 0
    data["ribbon_capacity"] = capacity or config.PRINTER_RIBBON_CAPACITY
    data["last_reset"] = datetime.now().isoformat()
    _save_counter(data)
    print(f"[PRINTER] Ribbon gereset: {data['ribbon_capacity']} prints beschikbaar")
    return data["ribbon_capacity"]


def set_remaining(remaining):
    """Manually set the remaining print count.

    Args:
        remaining: Number of prints remaining.
    """
    data = _load_counter()
    data["prints_used"] = data["ribbon_capacity"] - remaining
    _save_counter(data)
    print(f"[PRINTER] Handmatig ingesteld: {remaining} prints resterend")


def get_status_text():
    """Get a human-readable status string."""
    remaining = get_remaining_prints()
    capacity = get_ribbon_capacity()
    if remaining <= 0:
        return f"⚠ Ribbon leeg! (0/{capacity})"
    elif remaining <= 20:
        return f"⚠ Ribbon bijna op: {remaining}/{capacity}"
    else:
        return f"Ribbon: {remaining}/{capacity} prints"
