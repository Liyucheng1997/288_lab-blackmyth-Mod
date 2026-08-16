"""运行时扫描: 读正在运行的 b1-Win64-Shipping.exe 内存, 通过 RTTI 找 UObject 虚表,
用 ProcessEvent 的指令特征找出它在虚表里的真实索引, 顺便与 UE4SS 5.0 模板对比。

只读内存 (PROCESS_VM_READ), 不写不注入。
用法: 先开游戏进主菜单/关卡, 再  python tools/find_processevent_live.py
需要: pip install capstone psutil
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys
import re

from capstone import Cs, CS_ARCH_X86, CS_MODE_64
from capstone.x86 import X86_OP_MEM, X86_OP_IMM

PROC = "b1-Win64-Shipping.exe"
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
EXEC_PROTS = {0x10, 0x20, 0x40, 0x80}

k32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wt.DWORD), ("PartitionId", wt.WORD),
                ("RegionSize", ctypes.c_size_t), ("State", wt.DWORD),
                ("Protect", wt.DWORD), ("Type", wt.DWORD)]


def find_pid():
    import psutil
    for p in psutil.process_iter(["name", "pid"]):
        if p.info["name"] and p.info["name"].lower() == PROC.lower():
            return p.info["pid"]
    raise SystemExit(f"{PROC} 没在运行, 先开游戏。")


pid = find_pid()
h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
if not h:
    raise SystemExit(f"OpenProcess 失败 (err {k32.GetLastError()}), 试试用管理员运行。")

k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]


def rpm(addr, n):
    """分块读, 读不到的页用 0 填 (避免整段失败)。"""
    out = bytearray()
    CH = 0x10000
    off = 0
    while off < n:
        sz = min(CH, n - off)
        buf = ctypes.create_string_buffer(sz)
        got = ctypes.c_size_t()
        if k32.ReadProcessMemory(h, addr + off, buf, sz, ctypes.byref(got)) and got.value:
            out += buf.raw[:got.value]
            if got.value < sz:
                out += b"\0" * (sz - got.value)
        else:
            out += b"\0" * sz
        off += sz
    return bytes(out)


# 主模块基址
psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p), wt.DWORD, ctypes.POINTER(wt.DWORD), wt.DWORD]
psapi.GetModuleBaseNameW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.LPWSTR, wt.DWORD]
hmods = (ctypes.c_void_p * 1024)()
needed = wt.DWORD()
psapi.EnumProcessModulesEx(h, hmods, ctypes.sizeof(hmods), ctypes.byref(needed), 0x03)
mod_base = None
for i in range(needed.value // ctypes.sizeof(ctypes.c_void_p)):
    name = ctypes.create_unicode_buffer(260)
    psapi.GetModuleBaseNameW(h, hmods[i], name, 260)
    if name.value.lower() == PROC.lower():
        mod_base = hmods[i]
        break
if mod_base is None:
    raise SystemExit("找不到主模块")
mod_base = int(mod_base)
print(f"进程 {pid}, 主模块基址 0x{mod_base:X}")

# 枚举模块内存区域
regions = []
addr = mod_base
mbi = MBI()
while addr < mod_base + 0x40000000:
    if not k32.VirtualQueryEx(h, addr, ctypes.byref(mbi), ctypes.sizeof(mbi)):
        break
    if mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_GUARD) and mbi.Protect != PAGE_NOACCESS:
        regions.append((mbi.BaseAddress, mbi.RegionSize, mbi.Protect))
    addr = mbi.BaseAddress + mbi.RegionSize
    if mbi.AllocationBase and mbi.AllocationBase != mod_base and addr > mod_base + 0x1000:
        # 出了主模块分配范围
        if mbi.AllocationBase > mod_base:
            break
print(f"扫描 {len(regions)} 个内存区域 ...")

exec_ranges = [(b, b + s) for b, s, p in regions if (p & 0xF0)]


def in_code(va):
    return any(lo <= va < hi for lo, hi in exec_ranges)


# 0. 如果给了 --obj <UObject地址> (从 UE4SS 日志/GetAddress 拿), 直接读它的虚表指针, 跳过 RTTI
vft = None
if "--obj" in sys.argv:
    obj_addr = int(sys.argv[sys.argv.index("--obj") + 1], 16)
    vp = rpm(obj_addr, 8)
    if len(vp) == 8:
        vft = struct.unpack("<Q", vp)[0]
        print(f"对象 0x{obj_addr:X} 的虚表指针 = 0x{vft:X}")
    else:
        raise SystemExit("读不到该对象地址")

if vft is None:
    # 1. 找 RTTI TypeDescriptor 名字 (可能有多个副本, 逐个试)
    td_cands = []
    blobs = {}
    for b, s, p in regions:
        d = rpm(b, s)
        if not d:
            continue
        blobs[b] = d
        for m in re.finditer(re.escape(b".?AVUObject@@\0"), d):
            td_cands.append(b + m.start() - 16)
    if not td_cands:
        raise SystemExit("内存里也找不到 .?AVUObject@@")
    print("TypeDescriptor 候选:", [hex(x) for x in td_cands])

    # 2. 找 COL (sig=1, offset=0, td rva)
    col_va = None
    td_va = None
    for cand in td_cands:
        rva = cand - mod_base
        for b, d in blobs.items():
            for m in re.finditer(re.escape(struct.pack("<II", 1, 0)), d):
                o = m.start()
                if o + 16 <= len(d) and struct.unpack_from("<I", d, o + 12)[0] == rva:
                    col_va = b + o
                    td_va = cand
                    break
            if col_va:
                break
        if col_va:
            break
    td_rva = (td_va or td_cands[0]) - mod_base
    if col_va is None:
        total = sum(len(d) for d in blobs.values())
        print(f"(调试) 已读 {len(blobs)} 段共 {total/1048576:.0f} MB; 区域范围 0x{min(blobs):X}..0x{max(b+len(d) for b,d in blobs.items()):X}")
        # 放宽: 任何位置出现 td_rva, 且前 12 字节以 01 00 00 00 开头
        for b, d in blobs.items():
            for m in re.finditer(re.escape(struct.pack("<I", td_rva)), d):
                o = m.start() - 12
                if o >= 0 and d[o:o + 4] == b"\x01\x00\x00\x00":
                    col_va = b + o
                    print(f"(放宽匹配) COL 候选 @ 0x{col_va:X} offset={struct.unpack_from('<I', d, o+4)[0]}")
                    break
            if col_va:
                break
    if col_va is None:
        raise SystemExit("找不到 COL")
    print(f"UObject COL @ 0x{col_va:X}")

    # 3. 找 vftable (COL 指针后面紧跟第一项)
    vft = None
    p = struct.pack("<Q", col_va)
    for b, d in blobs.items():
        i = d.find(p)
        while i >= 0:
            cand = b + i + 8
            first = struct.unpack_from("<Q", d, i + 8)[0] if i + 16 <= len(d) else 0
            if in_code(first):
                vft = cand
                break
            i = d.find(p, i + 1)
        if vft:
            break
    if vft is None:
        raise SystemExit("找不到 vftable")


ents = []
raw = rpm(vft, 8 * 400)
for i in range(0, len(raw) - 7, 8):
    f = struct.unpack_from("<Q", raw, i)[0]
    if not in_code(f):
        break
    ents.append(f)
print(f"UObject vftable @ 0x{vft:X}, 共 {len(ents)} 项")

md = Cs(CS_ARCH_X86, CS_MODE_64)
md.detail = True


def analyze(func_va, max_bytes=0x1400):
    code = rpm(func_va, max_bytes)
    feats = {"b8": 0, "d8": 0, "ffff": 0, "b0": 0, "b6": 0, "size": 0}
    for ins in md.disasm(code, func_va):
        feats["size"] = ins.address + ins.size - func_va
        if ins.mnemonic == "int3":
            break
        for op in ins.operands:
            if op.type == X86_OP_MEM and op.mem.base != 0:
                if op.mem.disp == 0xB8 and ins.mnemonic in ("movzx", "mov", "cmp"):
                    feats["b8"] = 1
                if op.mem.disp == 0xD8 and ins.mnemonic == "call":
                    feats["d8"] = 1
                if op.mem.disp in (0xB0, 0xB1) and ins.mnemonic in ("test", "mov", "movzx"):
                    feats["b0"] = 1
                if op.mem.disp == 0xB6 and ins.mnemonic in ("movzx", "mov"):
                    feats["b6"] = 1
            if op.type == X86_OP_IMM and op.imm == 0xFFFF and ins.mnemonic == "cmp":
                feats["ffff"] = 1
    return feats


hits = []
rows = []
for i, f in enumerate(ents):
    ft = analyze(f)
    score = ft["b8"] + ft["d8"] + ft["ffff"] + ft["b0"] + ft["b6"]
    rows.append((i, f, ft, score))
    if score >= 3:
        hits.append(i)
for i, f, ft, score in rows:
    if score >= 2 or 60 <= i <= 80:
        mark = "  <== 疑似 ProcessEvent" if i in hits else ""
        print(f"[{i:3d}] 0x{f:X} size~{ft['size']:5d} b8={ft['b8']} d8={ft['d8']} ffff={ft['ffff']} b0={ft['b0']} b6={ft['b6']}{mark}")
print("疑似 ProcessEvent 索引:", hits, "(UE4SS 5.0 模板默认 69, 即 UE4SS 现在调用的那个)")
if len(sys.argv) > 1:
    want = int(sys.argv[1], 16)
    print("地址", hex(want), "在虚表索引:", [i for i, f in enumerate(ents) if f == want])
