"""在黑悟空 exe 里定位 UObject 虚表, 并找出 ProcessEvent 的真实索引 (UE4SS VTableLayout 覆盖用)。

用法: python tools/find_processevent.py [exe路径]
需要: pip install pefile capstone
"""
import re
import struct
import sys

import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64
from capstone.x86 import X86_OP_MEM, X86_OP_IMM

EXE = sys.argv[1] if len(sys.argv) > 1 else r"E:\SteamLibrary\steamapps\common\BlackMythWukong\b1\Binaries\Win64\b1-Win64-Shipping.exe"

pe = pefile.PE(EXE, fast_load=True)
base = pe.OPTIONAL_HEADER.ImageBase
# Denuvo 保护的 exe 区段名被改了 (.shared/.code/.xtls...), 按“可执行”属性识别代码段
EXEC = 0x20000000
code_ranges = [(base + s.VirtualAddress, base + s.VirtualAddress + s.Misc_VirtualSize)
               for s in pe.sections if s.Characteristics & EXEC]
print("可执行区段:", [(hex(a), hex(b)) for a, b in code_ranges])
data = pe.get_memory_mapped_image()


def in_code(va):
    return any(lo <= va < hi for lo, hi in code_ranges)


def va2off(va):
    return va - base


def rd(va, n):
    o = va2off(va)
    return data[o:o + n]


def u64(va):
    return struct.unpack_from("<Q", data, va2va := va2off(va))[0]


def u32(va):
    return struct.unpack_from("<I", data, va2off(va))[0]


def find_vftable(class_mangled: bytes):
    """通过 MSVC RTTI 找类的主虚表 (offset 0 的 COL)。"""
    # TypeDescriptor: vfptr(8) + spare(8) + name
    idx = data.find(class_mangled + b"\0")
    if idx < 0:
        raise SystemExit(f"找不到 RTTI 名 {class_mangled}")
    td_va = base + idx - 16
    td_rva = td_va - base
    # COL: sig(4)=1, offset(4)=0, cdOffset(4), pTypeDescriptor(4 rva), pClassHierarchy(4), pSelf(4)
    pat = struct.pack("<III", 1, 0, 0) + struct.pack("<I", td_rva)
    cols = [m.start() for m in re.finditer(re.escape(pat), data)]
    if not cols:
        # cdOffset 可能非 0
        pat = struct.pack("<II", 1, 0)
        cols = [m.start() for m in re.finditer(re.escape(pat), data) if struct.unpack_from("<I", data, m.start() + 12)[0] == td_rva]
    if not cols:
        raise SystemExit("找不到 COL")
    for col_off in cols:
        col_va = base + col_off
        # vftable 前 8 字节是指向 COL 的指针
        p = struct.pack("<Q", col_va)
        i = data.find(p)
        while i >= 0:
            vft = base + i + 8
            first = u64(vft)
            if in_code(first):
                return vft
            i = data.find(p, i + 1)
    raise SystemExit("找不到 vftable")


def vtable_entries(vft):
    out = []
    va = vft
    while True:
        f = u64(va)
        if not in_code(f):
            break
        # 遇到下一个 COL 指针 (下一张虚表开始) 也停
        out.append(f)
        va += 8
        if len(out) > 400:
            break
    return out


md = Cs(CS_ARCH_X86, CS_MODE_64)
md.detail = True


def analyze(func_va, max_bytes=0x1200):
    """返回函数体特征: 是否读 [x+0xB8] (ReturnValueOffset), 是否 call [x+0xD8] (UFunction::Func), 是否 cmp 0xFFFF, 大小"""
    code = rd(func_va, max_bytes)
    feats = {"ret_off_b8": False, "call_func_d8": False, "cmp_ffff": False, "flags_b0": False, "size": 0, "rets": 0}
    n = 0
    for ins in md.disasm(code, func_va):
        n += 1
        feats["size"] = ins.address + ins.size - func_va
        if ins.mnemonic == "ret" and n > 30:
            feats["rets"] += 1
            # 粗略: 第一个 ret 后再多看一点, 遇到 int3 填充停
        if ins.mnemonic == "int3":
            break
        for op in ins.operands:
            if op.type == X86_OP_MEM and op.mem.base != 0:
                if op.mem.disp == 0xB8 and ins.mnemonic in ("movzx", "mov", "cmp"):
                    feats["ret_off_b8"] = True
                if op.mem.disp == 0xD8 and ins.mnemonic == "call":
                    feats["call_func_d8"] = True
                if op.mem.disp in (0xB0, 0xB1) and ins.mnemonic in ("test", "mov", "movzx"):
                    feats["flags_b0"] = True
            if op.type == X86_OP_IMM and op.imm == 0xFFFF and ins.mnemonic == "cmp":
                feats["cmp_ffff"] = True
    return feats


if __name__ == "__main__":
    vft = find_vftable(b".?AVUObject@@")
    ents = vtable_entries(vft)
    print(f"UObject vftable @ 0x{vft:X}, {len(ents)} 项")
    hits = []
    for i, f in enumerate(ents):
        ft = analyze(f)
        score = sum([ft["ret_off_b8"], ft["call_func_d8"], ft["cmp_ffff"], ft["flags_b0"]])
        mark = ""
        if score >= 3:
            mark = "  <== 疑似 ProcessEvent"
            hits.append(i)
        if score >= 2 or 60 <= i <= 80:
            print(f"[{i:3d}] 0x{f:X} size~{ft['size']:5d} b8={int(ft['ret_off_b8'])} d8={int(ft['call_func_d8'])} ffff={int(ft['cmp_ffff'])} b0={int(ft['flags_b0'])}{mark}")
    print("疑似 ProcessEvent 索引:", hits, "(UE4SS 5.0 模板默认 69)")
    if len(sys.argv) > 2:
        # 附加: 打印指定地址属于第几项
        want = int(sys.argv[2], 16)
        print("地址", hex(want), "在虚表索引:", [i for i, f in enumerate(ents) if f == want])
