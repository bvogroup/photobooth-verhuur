"""Lees PE-exports van DPQW410UI.DLL — welke functies biedt hij aan?"""
import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
import struct

DLL = r"C:\Windows\System32\spool\drivers\x64\3\DPQW410UI.DLL"

with open(DLL, "rb") as f:
    data = f.read()

e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
file_hdr_off = e_lfanew + 4
opt_hdr_off = file_hdr_off + 20
magic = struct.unpack_from("<H", data, opt_hdr_off)[0]
is_64 = (magic == 0x20B)
data_dir_off = opt_hdr_off + (112 if is_64 else 96)
# Export Table is DataDirectory[0]
exp_rva, exp_size = struct.unpack_from("<II", data, data_dir_off)
num_sects = struct.unpack_from("<H", data, file_hdr_off + 2)[0]
sect_hdr_size = struct.unpack_from("<H", data, file_hdr_off + 16)[0]
sect_off = opt_hdr_off + sect_hdr_size

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

def read_cstr(off, max_len=256):
    end = data.find(b"\x00", off, off + max_len)
    if end < 0: end = off + max_len
    return data[off:end].decode("ascii", errors="replace")

if exp_rva == 0:
    print("Geen export-tabel")
    sys.exit(0)

exp_off = rva_to_off(exp_rva)
# IMAGE_EXPORT_DIRECTORY layout
characteristics, ts, mj, mn, name_rva, base, nfuncs, nnames, funcs_rva, names_rva, ord_rva = \
    struct.unpack_from("<IIHHIIIIIII", data, exp_off)
dll_name = read_cstr(rva_to_off(name_rva))
print(f"DLL name: {dll_name}")
print(f"Functions: {nfuncs}, Named: {nnames}")
print()
print("=== Exports ===")
names_off = rva_to_off(names_rva)
ord_off = rva_to_off(ord_rva)
funcs_off = rva_to_off(funcs_rva)
for i in range(nnames):
    name_rva_i = struct.unpack_from("<I", data, names_off + i*4)[0]
    name = read_cstr(rva_to_off(name_rva_i))
    ordinal = struct.unpack_from("<H", data, ord_off + i*2)[0]
    func_rva = struct.unpack_from("<I", data, funcs_off + ordinal*4)[0]
    # Highlight interesting names
    interesting = any(kw in name.lower() for kw in [
        "status", "get", "info", "media", "ribbon", "counter",
        "serial", "firmware", "query", "device", "bidi",
        "print", "doc", "prop", "drv"
    ])
    marker = " ⭐" if interesting else ""
    print(f"  {name} (ord {ordinal}, RVA 0x{func_rva:x}){marker}")
