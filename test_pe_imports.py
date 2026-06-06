"""Lees PE-imports van DPQW410UI.DLL — welke Windows API's roept hij aan?"""
import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
import struct

DLL = r"C:\Windows\System32\spool\drivers\x64\3\DPQW410UI.DLL"

with open(DLL, "rb") as f:
    data = f.read()

# PE parsing — heel beknopt
e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
sig = data[e_lfanew:e_lfanew+4]
assert sig == b"PE\x00\x00", f"Geen PE sig: {sig!r}"
file_hdr_off = e_lfanew + 4
opt_hdr_off = file_hdr_off + 20
# Magic: 0x10B = PE32, 0x20B = PE32+
magic = struct.unpack_from("<H", data, opt_hdr_off)[0]
is_64 = (magic == 0x20B)
# DataDirectories: bij PE32+ op offset 112, bij PE32 op 96
data_dir_off = opt_hdr_off + (112 if is_64 else 96)
# Import Table is DataDirectory[1]
imp_rva, imp_size = struct.unpack_from("<II", data, data_dir_off + 8)
# Number of sections
num_sects = struct.unpack_from("<H", data, file_hdr_off + 2)[0]
sect_hdr_size = struct.unpack_from("<H", data, file_hdr_off + 16)[0]
sect_off = opt_hdr_off + sect_hdr_size

# Build sections list voor RVA → file-offset mapping
sections = []
for i in range(num_sects):
    off = sect_off + i * 40
    name = data[off:off+8].rstrip(b"\x00").decode("ascii", errors="replace")
    vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, off + 8)
    sections.append((name, vaddr, vsize, raddr, rsize))

def rva_to_off(rva):
    for n, va, vs, ra, rs in sections:
        if va <= rva < va + max(vs, rs):
            return ra + (rva - va)
    return None

def read_cstr(off, max_len=128):
    end = data.find(b"\x00", off, off + max_len)
    if end < 0: end = off + max_len
    return data[off:end].decode("ascii", errors="replace")

# Import directory: array van IMAGE_IMPORT_DESCRIPTOR (20 bytes each)
imp_off = rva_to_off(imp_rva)
i = 0
while True:
    desc = data[imp_off + i*20 : imp_off + (i+1)*20]
    if desc == b"\x00" * 20: break
    olt_rva, ts, fwd, name_rva, ilt_rva = struct.unpack("<IIIII", desc)
    if name_rva == 0: break
    dll_name = read_cstr(rva_to_off(name_rva))
    print(f"\n=== Import: {dll_name} ===")
    # Walk thunk array
    thunk_rva = olt_rva or ilt_rva
    thunk_off = rva_to_off(thunk_rva)
    j = 0
    while True:
        if is_64:
            entry = struct.unpack_from("<Q", data, thunk_off + j*8)[0]
        else:
            entry = struct.unpack_from("<I", data, thunk_off + j*4)[0]
        if entry == 0: break
        # MSB set = ordinal import
        if is_64 and (entry & (1 << 63)):
            print(f"  ordinal {entry & 0xFFFF}")
        elif not is_64 and (entry & (1 << 31)):
            print(f"  ordinal {entry & 0xFFFF}")
        else:
            # entry is RVA naar IMAGE_IMPORT_BY_NAME (WORD hint + name)
            hint_off = rva_to_off(entry)
            if hint_off:
                name = read_cstr(hint_off + 2)
                # Highlight interessante imports
                interesting = any(kw in name for kw in [
                    "Printer", "Print", "Spool", "Status", "Bidi",
                    "Write", "Read", "Open", "Close", "Doc",
                    "Setup", "Device", "Job", "Reg"
                ])
                marker = " ⭐" if interesting else ""
                print(f"  {name}{marker}")
        j += 1
    i += 1

print("\n=== KLAAR ===")
