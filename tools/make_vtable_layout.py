"""生成黑悟空专用的 UE4SS VTableLayout.ini。

背景 (2026-08 实测, 见 docs/黑悟空_UE4SS_API笔记.md):
  黑悟空 (UE5.0 定制) 的 UObject 虚表比 UE4SS 内置 5.0 布局少 1 项 (在 ProcessEvent 之前)。
  UE4SS 默认按第 75 号调用 ProcessEvent, 而那格是个空函数; 真正的 ProcessEvent 在第 74 号。
  结果: 所有 UFunction 调用“执行了但没效果, 返回 0”。
做法:
  取 UE4SS 官方 5.0 模板 (assets/VTableLayoutTemplates/VTableLayout_5_00_Template.ini),
  在 [UObject] 段 ProcessEvent 之前删掉一个 UE4SS 不会用到的条目 (PostSaveRoot__bool),
  这样 ProcessEvent 及其后的条目整体前移 1 格。

用法: python tools/make_vtable_layout.py <模板ini> <输出ini>
"""
import sys
from pathlib import Path

REMOVE_IN_UOBJECT = "PostSaveRoot__bool"   # 必须位于 ProcessEvent 之前, 且 UE4SS 不调用

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
lines = src.read_text(encoding="utf-8", errors="ignore").splitlines()
out = []
section = None
removed = False
for line in lines:
    s = line.strip()
    if s.startswith("[") and s.endswith("]"):
        section = s[1:-1]
    if section == "UObject" and s == REMOVE_IN_UOBJECT and not removed:
        out.append(f"; [黑悟空] 已删除 {REMOVE_IN_UOBJECT}: 游戏虚表在 ProcessEvent 前少 1 项, 使 ProcessEvent 落到第 74 号 (0x250)")
        removed = True
        continue
    out.append(line)
if not removed:
    raise SystemExit(f"模板里没找到 {REMOVE_IN_UOBJECT}")
header = [
    "; 黑神话: 悟空 专用 VTableLayout.ini —— 由 tools/make_vtable_layout.py 生成",
    "; 基于 UE4SS 官方 VTableLayout_5_00_Template.ini, 仅 [UObject] 段少一项 (见文件内注释)。",
    "",
]
dst.write_text("\n".join(header + out) + "\n", encoding="utf-8")
# 校验: 计算 ProcessEvent 偏移
def count(sec):
    n = 0
    cur = None
    for line in out:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]; continue
        if cur == sec and s and not s.startswith(";"):
            n += 1
    return n
ub, ubu = count("UObjectBase") - 1, count("UObjectBaseUtility") - 1
idx = None
cur = None
i = -1
for line in out:
    s = line.strip()
    if s.startswith("[") and s.endswith("]"):
        cur = s[1:-1]; i = -1; continue
    if cur == "UObject" and s and not s.startswith(";"):
        i += 1
        if s == "ProcessEvent":
            idx = i
off = (idx + ub + ubu) * 8
print(f"写入 {dst}; ProcessEvent 在 [UObject] 第 {idx} 项, 最终虚表偏移 0x{off:X} (第 {off // 8} 号)")
