"""wkmod 加载器端到端测试: 在临时目录伪造游戏树 + 伪造 UE4SS zip, 走一遍 install/deploy/clean/new。
用法: python tests/test_loader.py
"""
import io, json, os, sys, tempfile, zipfile, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import wkmod  # noqa

failures = []
def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond: failures.append(msg)

tmp = Path(tempfile.mkdtemp(prefix="wkmod_test_"))
game = tmp / "BlackMythWukong"
(game / "b1/Binaries/Win64").mkdir(parents=True)
(game / "b1/Content/Paks").mkdir(parents=True)
(game / wkmod.EXE_REL).write_bytes(b"MZ")

# 把加载器的配置/状态文件重定向到临时目录, 不污染项目
wkmod.CONFIG_FILE = tmp / "loader.config.json"
wkmod.STATE_FILE = tmp / "state.json"
wkmod.save_config({"game_dir": str(game)})

# 伪造 UE4SS experimental zip (新布局)
zpath = tmp / "UE4SS_vTEST.zip"
with zipfile.ZipFile(zpath, "w") as z:
    z.writestr("dwmapi.dll", b"proxy")
    z.writestr("ue4ss/UE4SS.dll", b"core")
    z.writestr("ue4ss/UE4SS-settings.ini", "[General]\nEnableHotReloadSystem = 0\n\n[Debug]\nConsoleEnabled = 0\nGuiConsoleEnabled = 0\nGuiConsoleVisible = 0\n")
    z.writestr("ue4ss/Mods/mods.txt", "CheatManagerEnablerMod : 1\nConsoleEnablerMod : 1\n\n; Built-in keybinds, do not move up!\nKeybinds : 1\n")
    z.writestr("ue4ss/Mods/Keybinds/Scripts/main.lua", "-- keybinds")

class A:  # 简易 args
    def __init__(self, **kw): self.__dict__.update(kw)

# 1. install
wkmod.cmd_ue4ss_install(A(zip=str(zpath), force=False, yes=True))
gp = wkmod.GamePaths(game)
check(gp.ue4ss_installed, "UE4SS 已安装 (新布局)")
check((game / "b1/Binaries/Win64/dwmapi.dll").exists(), "代理 dll 落位")
check(wkmod.ini_get(gp.ue4ss_settings, "General", "EnableHotReloadSystem") == "1", "热重载已开")
check(wkmod.ini_get(gp.ue4ss_settings, "Debug", "GuiConsoleVisible") == "0", "控制台默认隐藏")
check(gp.pak_mods.exists(), "~mods 已创建")

# 2. deploy (junction)
wkmod.cmd_deploy(A(copy=False))
dst = gp.ue4ss_mods_dir / "RuyiGatling"
check((dst / "Scripts/main.lua").exists(), "RuyiGatling 已部署")
check(wkmod.is_junction_or_symlink(dst), "使用目录联接")
txt = gp.ue4ss_mods_txt.read_text(encoding="utf-8")
lines = [l.strip() for l in txt.splitlines() if l.strip()]
check("RuyiGatling : 1" in lines, "mods.txt 含 RuyiGatling : 1")
check(lines[-1] == "Keybinds : 1", "Keybinds 仍在最后")
check("CheatManagerEnablerMod : 1" in lines, "原有条目保留")
check(txt.count("Keybinds : 1") == 1, "Keybinds 只出现一次")

# 3. 改源码 -> 联接下即时可见
src_main = ROOT / "mods/RuyiGatling/Scripts/main.lua"
check((dst / "Scripts/main.lua").read_text(encoding="utf-8") == src_main.read_text(encoding="utf-8"), "联接内容与源一致")

# 4. 重新 deploy 用 copy
wkmod.cmd_deploy(A(copy=True))
check(not wkmod.is_junction_or_symlink(dst) and (dst / "Scripts/main.lua").exists(), "--copy 模式为真实复制")
check(not (dst / "mod.json").exists(), "复制时不带 mod.json")
check(gp.ue4ss_mods_txt.read_text(encoding="utf-8").count("RuyiGatling : 1") == 1, "重复部署不重复条目")

# 5. pak 模组部署
pakmod = ROOT / "mods/_TestPakMod"
try:
    pakmod.mkdir()
    (pakmod / "TestMod_P.pak").write_bytes(b"pak")
    (pakmod / "TestMod_P.sig").write_bytes(b"sig")
    (pakmod / "mod.json").write_text(json.dumps({"name": "_TestPakMod", "type": "pak", "enabled": True}))
    # 以下划线开头会被忽略 -> 改名
    pakmod2 = ROOT / "mods/TestPakMod"
    pakmod.rename(pakmod2); pakmod = pakmod2
    (pakmod / "mod.json").write_text(json.dumps({"name": "TestPakMod", "type": "pak", "enabled": True}))
    wkmod.cmd_deploy(A(copy=False))
    check((gp.pak_mods / "TestMod_P.pak").exists() and (gp.pak_mods / "TestMod_P.sig").exists(), "pak+sig 部署到 ~mods")
    # 6. clean
    wkmod.cmd_clean(A(yes=True))
    check(not dst.exists(), "clean 移除 lua 模组")
    check(not (gp.pak_mods / "TestMod_P.pak").exists(), "clean 移除 pak")
    check("RuyiGatling" not in gp.ue4ss_mods_txt.read_text(encoding="utf-8"), "clean 后 mods.txt 无 RuyiGatling")
    check("Keybinds : 1" in gp.ue4ss_mods_txt.read_text(encoding="utf-8"), "clean 后 Keybinds 保留")
    check(src_main.exists(), "源文件未被 clean 误删 (联接安全)")
finally:
    shutil.rmtree(pakmod, ignore_errors=True)

# 7. new
newdir = ROOT / "mods/ZzTestNewMod"
try:
    wkmod.cmd_new(A(name="ZzTestNewMod"))
    check((newdir / "Scripts/main.lua").exists() and "ZzTestNewMod" in (newdir / "Scripts/main.lua").read_text(encoding="utf-8"), "new 从模板创建并替换名字")
finally:
    shutil.rmtree(newdir, ignore_errors=True)

# 8. console toggle
wkmod.cmd_ue4ss_console(A(state="on"))
check(wkmod.ini_get(gp.ue4ss_settings, "Debug", "GuiConsoleVisible") == "1", "console on")

shutil.rmtree(tmp, ignore_errors=True)
print("\n%d 项失败" % len(failures))
sys.exit(1 if failures else 0)
