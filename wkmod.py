#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wkmod —— 黑神话: 悟空 Mod 加载器 / 管理器 (只依赖 Python 标准库)

项目结构:
    mods/<ModName>/mod.json           模组清单 (type: lua | pak)
    mods/<ModName>/Scripts/main.lua   lua 模组脚本 (UE4SS)
    mods/<ModName>/*.pak              pak 模组 (放 ~mods)
    loader.config.json                加载器配置 (游戏路径等, 自动生成)
    .wkmod-state.json                 部署记录 (用于 clean)

常用:
    wkmod detect                  找游戏目录, 看 UE4SS 状态
    wkmod ue4ss install [zip]     安装 UE4SS (给 zip 路径, 或不给则从 GitHub 下载 experimental 版)
    wkmod ue4ss console on|off    开关 UE4SS 图形控制台窗口 (看日志方便)
    wkmod list                    列出模组
    wkmod enable/disable <name>   启用/禁用
    wkmod deploy [--copy]         把启用的模组部署到游戏 (默认目录联接 junction, 改代码即时生效; --copy 改为复制)
    wkmod clean                   从游戏里移除本工具部署的东西 (不动 UE4SS 本体)
    wkmod launch [--no-deploy]    部署并启动游戏 (Steam)
    wkmod new <name>              从模板新建一个 lua 模组
    wkmod log [-n 80] [-f]        看 UE4SS.log
"""
import argparse
import ctypes
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODS_DIR = ROOT / "mods"
CONFIG_FILE = ROOT / "loader.config.json"
STATE_FILE = ROOT / ".wkmod-state.json"
TEMPLATE_DIR = MODS_DIR / "_template"

STEAM_APPID = "2358720"
GAME_SUBDIR = "steamapps/common/BlackMythWukong"
EXE_REL = "b1/Binaries/Win64/b1-Win64-Shipping.exe"
UE4SS_RELEASE_API = "https://api.github.com/repos/UE4SS-RE/RE-UE4SS/releases/tags/experimental-latest"

# UE4SS 自带、必须保留在 mods.txt 里的条目 (顺序无所谓, 但 Keybinds 必须最后)
UE4SS_BUILTIN_MODS = [
    "CheatManagerEnablerMod", "ActorDumperMod", "ConsoleCommandsMod", "ConsoleEnablerMod",
    "SplitScreenMod", "LineTraceMod", "BPML_GenericFunctions", "BPModLoaderMod", "jsbLuaProfilerMod",
]

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ----------------------------------------------------------------------------- 基础
def info(msg): print(f"[wkmod] {msg}")
def warn(msg): print(f"[wkmod] 警告: {msg}")
def die(msg, code=1):
    print(f"[wkmod] 错误: {msg}", file=sys.stderr)
    sys.exit(code)


def load_json(p: Path, default):
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            warn(f"读取 {p.name} 失败: {e}")
    return default


def save_json(p: Path, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config():
    return load_json(CONFIG_FILE, {})


def save_config(cfg):
    save_json(CONFIG_FILE, cfg)


# ----------------------------------------------------------------------------- 游戏定位
def steam_libraries():
    libs = []
    candidates = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for name in ("SteamPath", "InstallPath"):
                            try:
                                v, _ = winreg.QueryValueEx(k, name)
                                candidates.append(Path(v))
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
        candidates += [Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam")]
    for c in candidates:
        if c.exists() and c not in libs:
            libs.append(c)
        vdf = c / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="ignore")):
                p = Path(m.group(1).replace("\\\\", "\\"))
                if p.exists() and p not in libs:
                    libs.append(p)
    return libs


def find_game_dir(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    if cfg.get("game_dir"):
        g = Path(cfg["game_dir"])
        if (g / EXE_REL).exists():
            return g
        warn(f"配置里的 game_dir 无效: {g}")
    for lib in steam_libraries():
        g = lib / GAME_SUBDIR
        if (g / EXE_REL).exists():
            return g
    # 兜底: 扫常见盘符
    for drive in "CDEFGHIJ":
        for sub in ("SteamLibrary", "Steam", "Program Files (x86)/Steam", "Games/Steam"):
            g = Path(f"{drive}:/{sub}") / GAME_SUBDIR
            if (g / EXE_REL).exists():
                return g
    return None


def require_game_dir():
    g = find_game_dir()
    if not g:
        die("找不到游戏目录。用  wkmod detect --game-dir \"X:\\...\\BlackMythWukong\"  手动指定。")
    return g


class GamePaths:
    def __init__(self, game_dir: Path):
        self.game_dir = game_dir
        self.win64 = game_dir / "b1" / "Binaries" / "Win64"
        self.paks = game_dir / "b1" / "Content" / "Paks"
        self.pak_mods = self.paks / "~mods"
        self.exe = game_dir / EXE_REL

    # UE4SS 新版 (experimental/3.x 之后) 布局: Win64/dwmapi.dll + Win64/ue4ss/{UE4SS.dll, UE4SS-settings.ini, Mods/}
    # 旧版布局: Win64/{xinput1_3.dll, UE4SS.dll, UE4SS-settings.ini, Mods/}
    @property
    def ue4ss_dir(self):
        new = self.win64 / "ue4ss"
        if (new / "UE4SS.dll").exists() or (new / "Mods").exists():
            return new
        if (self.win64 / "UE4SS.dll").exists():
            return self.win64
        return new  # 默认按新布局

    @property
    def ue4ss_installed(self):
        return (self.ue4ss_dir / "UE4SS.dll").exists()

    @property
    def ue4ss_mods_dir(self):
        return self.ue4ss_dir / "Mods"

    @property
    def ue4ss_mods_txt(self):
        return self.ue4ss_mods_dir / "mods.txt"

    @property
    def ue4ss_settings(self):
        return self.ue4ss_dir / "UE4SS-settings.ini"

    @property
    def ue4ss_log(self):
        return self.ue4ss_dir / "UE4SS.log"

    @property
    def proxy_dll(self):
        for n in ("dwmapi.dll", "xinput1_3.dll", "version.dll"):
            if (self.win64 / n).exists():
                return self.win64 / n
        return None


# ----------------------------------------------------------------------------- 模组清单
def iter_mods():
    if not MODS_DIR.exists():
        return []
    out = []
    for d in sorted(MODS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name.startswith("."):
            continue
        meta = load_json(d / "mod.json", None)
        if meta is None:
            # 没有 mod.json 也能识别: 有 Scripts/main.lua 当 lua, 有 .pak 当 pak
            if (d / "Scripts" / "main.lua").exists():
                meta = {"name": d.name, "type": "lua", "enabled": True}
            elif any(d.glob("*.pak")):
                meta = {"name": d.name, "type": "pak", "enabled": True}
            else:
                continue
        meta.setdefault("name", d.name)
        meta.setdefault("type", "lua")
        meta.setdefault("enabled", True)
        meta["_dir"] = d
        out.append(meta)
    return out


def get_mod(name):
    for m in iter_mods():
        if m["name"].lower() == name.lower() or m["_dir"].name.lower() == name.lower():
            return m
    die(f"没有叫 {name} 的模组 (mods/ 下)。")


def set_mod_enabled(name, enabled):
    m = get_mod(name)
    p = m["_dir"] / "mod.json"
    meta = load_json(p, {"name": m["name"], "type": m["type"]})
    meta["enabled"] = enabled
    save_json(p, meta)
    info(f"{m['name']} -> {'启用' if enabled else '禁用'} (记得 wkmod deploy)")


# ----------------------------------------------------------------------------- 文件系统工具
def is_junction_or_symlink(p: Path):
    try:
        if p.is_symlink():
            return True
        if sys.platform == "win32" and p.exists():
            FILE_ATTRIBUTE_REPARSE_POINT = 0x400
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(p))
            return attrs != -1 and bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        pass
    return False


def remove_path(p: Path):
    if not p.exists() and not p.is_symlink():
        return
    if is_junction_or_symlink(p):
        # 目录联接/软链接: 只删链接本身, 不碰目标
        if p.is_dir():
            os.rmdir(p)
        else:
            p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()


def make_junction(link: Path, target: Path):
    """Windows 目录联接 (不需要管理员权限)。失败抛异常。"""
    if sys.platform != "win32":
        os.symlink(target, link, target_is_directory=True)
        return
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip())


def copy_tree(src: Path, dst: Path):
    if dst.exists():
        remove_path(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("mod.json", "README.md", ".git", "__pycache__"))


# ----------------------------------------------------------------------------- mods.txt
def read_mods_txt(p: Path):
    entries = []  # (name, enabled) 或 (None, rawline)
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            m = re.match(r"^\s*([A-Za-z0-9_\-\. ]+?)\s*:\s*([01])\s*$", line)
            if m:
                entries.append((m.group(1).strip(), m.group(2) == "1"))
            else:
                entries.append((None, line))
    return entries


def write_mods_txt(p: Path, our_mods: dict):
    """our_mods: {name: enabled}. 保留原有其它条目, Keybinds 放最后。"""
    entries = read_mods_txt(p)
    names_seen = set()
    out = []
    keybinds_line = None
    for name, val in entries:
        if name is None:
            if isinstance(val, str) and val.strip().startswith(";") and "keybinds" in val.lower():
                continue  # 我们自己重写这条注释
            out.append(val)
            continue
        if name == "Keybinds":
            keybinds_line = f"Keybinds : {1 if val else 1}"
            continue
        if name in our_mods:
            out.append(f"{name} : {1 if our_mods[name] else 0}")
            names_seen.add(name)
        else:
            out.append(f"{name} : {1 if val else 0}")
    for name, en in our_mods.items():
        if name not in names_seen:
            out.append(f"{name} : {1 if en else 0}")
    # 去掉尾部空行, 追加 Keybinds
    while out and not str(out[-1]).strip():
        out.pop()
    out += ["", "; Built-in keybinds, do not move up!", keybinds_line or "Keybinds : 1"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------- UE4SS-settings.ini
def ini_set(path: Path, section: str, key: str, value: str):
    lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines() if path.exists() else []
    out, cur, done, sec_found = [], None, False, False
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if cur == section and not done:
                out.append(f"{key} = {value}")
                done = True
            cur = s[1:-1]
            if cur == section:
                sec_found = True
        elif cur == section and re.match(rf"^\s*{re.escape(key)}\s*=", line):
            if not done:
                out.append(f"{key} = {value}")
                done = True
            continue
        out.append(line)
    if not done:
        if not sec_found:
            out += ["", f"[{section}]"]
        out.append(f"{key} = {value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def ini_get(path: Path, section: str, key: str):
    if not path.exists():
        return None
    cur = None
    for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
        elif cur == section:
            m = re.match(rf"^\s*{re.escape(key)}\s*=\s*(.*)$", line)
            if m:
                return m.group(1).strip()
    return None


# ----------------------------------------------------------------------------- 命令: detect
def cmd_detect(args):
    cfg = load_config()
    if args.game_dir:
        g = Path(args.game_dir)
        if not (g / EXE_REL).exists():
            die(f"{g} 下没有 {EXE_REL}")
        cfg["game_dir"] = str(g)
        save_config(cfg)
        info(f"已保存 game_dir = {g}")
    g = find_game_dir(cfg)
    if not g:
        die("找不到游戏目录, 请用 --game-dir 指定。")
    gp = GamePaths(g)
    print(f"游戏目录 : {g}")
    print(f"可执行   : {gp.exe}  ({'存在' if gp.exe.exists() else '缺失'})")
    print(f"UE4SS    : {'已安装' if gp.ue4ss_installed else '未安装'}  ({gp.ue4ss_dir})")
    if gp.ue4ss_installed:
        print(f"  代理DLL: {gp.proxy_dll}")
        print(f"  Mods   : {gp.ue4ss_mods_dir}")
        print(f"  控制台 : GuiConsoleEnabled={ini_get(gp.ue4ss_settings, 'Debug', 'GuiConsoleEnabled')} "
              f"GuiConsoleVisible={ini_get(gp.ue4ss_settings, 'Debug', 'GuiConsoleVisible')}")
    print(f"~mods    : {'存在' if gp.pak_mods.exists() else '不存在'}  ({gp.pak_mods})")
    st = load_json(STATE_FILE, {})
    if st.get("deployed"):
        print("已部署   : " + ", ".join(st["deployed"].keys()))
    if not cfg.get("game_dir"):
        cfg["game_dir"] = str(g)
        save_config(cfg)


# ----------------------------------------------------------------------------- 命令: ue4ss
def download(url, dest: Path):
    info(f"下载 {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "wkmod"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                print(f"\r  {got / 1048576:.1f} / {total / 1048576:.1f} MB", end="", flush=True)
        print()


def resolve_ue4ss_asset():
    req = urllib.request.Request(UE4SS_RELEASE_API, headers={"User-Agent": "wkmod", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rel = json.load(r)
    for a in rel.get("assets", []):
        n = a["name"]
        if re.match(r"^UE4SS_v.*\.zip$", n) and not n.startswith("zDEV"):
            return a["browser_download_url"], n, a.get("size", 0)
    die("GitHub release 里没找到 UE4SS_v*.zip")


def cmd_ue4ss_install(args):
    gp = GamePaths(require_game_dir())
    if gp.ue4ss_installed and not args.force:
        info(f"UE4SS 已安装在 {gp.ue4ss_dir}, 加 --force 覆盖安装。")
        return
    zip_path = Path(args.zip) if args.zip else None
    tmp = None
    if zip_path is None:
        url, name, size = resolve_ue4ss_asset()
        print(f"将从 GitHub 下载 UE4SS: {name} ({size / 1048576:.1f} MB)\n  {url}")
        if not args.yes and input("继续? [y/N] ").strip().lower() != "y":
            die("已取消。也可以手动下载后:  wkmod ue4ss install <zip路径>")
        tmp = ROOT / "_downloads"
        tmp.mkdir(exist_ok=True)
        zip_path = tmp / name
        download(url, zip_path)
    if not zip_path.exists():
        die(f"zip 不存在: {zip_path}")
    info(f"解压 {zip_path.name} -> {gp.win64}")
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        # 兼容 zip 内多一层顶级目录的情况
        prefix = ""
        tops = {n.split("/")[0] for n in names if "/" in n}
        if len(tops) == 1 and not any(n.lower().endswith(".dll") and "/" not in n for n in names):
            t = next(iter(tops))
            if not any(n.lower() in ("dwmapi.dll", "xinput1_3.dll", "ue4ss.dll") for n in names):
                prefix = t + "/"
        for n in names:
            if prefix and not n.startswith(prefix):
                continue
            rel = n[len(prefix):]
            if not rel or rel.endswith("/"):
                continue
            dst = gp.win64 / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            with z.open(n) as src, open(dst, "wb") as f:
                shutil.copyfileobj(src, f)
    if not gp.ue4ss_installed:
        die("解压完但没找到 UE4SS.dll, 请检查 zip 内容。")
    info("UE4SS 安装完成。")
    # 推荐设置: 开热重载, 控制台默认关 (需要时 wkmod ue4ss console on)
    ini_set(gp.ue4ss_settings, "General", "EnableHotReloadSystem", "1")
    # 黑悟空专用: 实验版默认 false 会在进关卡时闪退 (UE4SS issue #632), 必须 true
    ini_set(gp.ue4ss_settings, "General", "bUseUObjectArrayCache", "true")
    # 黑悟空专用 (2026-08 实测二分定位): 这两个引擎钩子在当前版本会在主菜单后几秒 ACCESS_VIOLATION 闪退, 必须关。
    # Lua 模组不需要它们 (只影响 RegisterInitGameStatePreHook / RegisterLoadMapPreHook)。
    ini_set(gp.ue4ss_settings, "Hooks", "HookInitGameState", "0")
    ini_set(gp.ue4ss_settings, "Hooks", "HookLoadMap", "0")
    apply_wukong_vtable_layout(gp)


VTABLE_LAYOUT_SRC = ROOT / "tools" / "ue4ss_config" / "VTableLayout.ini"


def apply_wukong_vtable_layout(gp: "GamePaths"):
    """黑悟空的 UObject 虚表比 UE4SS 内置 5.0 布局少 1 项, 不覆盖的话所有 UFunction 调用都是空操作/返回 0。
    tools/ue4ss_config/VTableLayout.ini 由 tools/make_vtable_layout.py 从官方 5.0 模板生成。"""
    if VTABLE_LAYOUT_SRC.exists():
        shutil.copy2(VTABLE_LAYOUT_SRC, gp.ue4ss_dir / "VTableLayout.ini")
        info("已写入黑悟空专用 VTableLayout.ini (修正 ProcessEvent 虚表位置)")
    else:
        warn(f"缺少 {VTABLE_LAYOUT_SRC}, UFunction 调用会失效 (返回 0)")
    ini_set(gp.ue4ss_settings, "Debug", "ConsoleEnabled", "0")
    ini_set(gp.ue4ss_settings, "Debug", "GuiConsoleEnabled", "1")
    ini_set(gp.ue4ss_settings, "Debug", "GuiConsoleVisible", "0")
    info(f"已写入推荐设置 -> {gp.ue4ss_settings.name} (热重载开, 图形控制台默认隐藏)")
    if not gp.pak_mods.exists():
        gp.pak_mods.mkdir(parents=True, exist_ok=True)
        info(f"已创建 {gp.pak_mods}")


def cmd_ue4ss_console(args):
    gp = GamePaths(require_game_dir())
    if not gp.ue4ss_installed:
        die("UE4SS 未安装。")
    on = args.state == "on"
    ini_set(gp.ue4ss_settings, "Debug", "GuiConsoleEnabled", "1")
    ini_set(gp.ue4ss_settings, "Debug", "GuiConsoleVisible", "1" if on else "0")
    info(f"UE4SS 图形控制台: {'显示' if on else '隐藏'} (下次启动生效)")


def cmd_ue4ss_uninstall(args):
    gp = GamePaths(require_game_dir())
    if not gp.ue4ss_installed and not gp.proxy_dll:
        info("UE4SS 本来就没装。")
        return
    print(f"将删除: {gp.proxy_dll}  和  {gp.ue4ss_dir}")
    if not args.yes and input("确认? [y/N] ").strip().lower() != "y":
        die("已取消")
    cmd_clean(argparse.Namespace(yes=True))
    if gp.proxy_dll:
        gp.proxy_dll.unlink()
    if gp.ue4ss_dir != gp.win64:
        remove_path(gp.ue4ss_dir)
    else:
        for n in ("UE4SS.dll", "UE4SS-settings.ini", "UE4SS.log", "Mods"):
            remove_path(gp.win64 / n)
    info("UE4SS 已卸载。")


# ----------------------------------------------------------------------------- 命令: list / enable / disable
def cmd_list(args):
    mods = iter_mods()
    if not mods:
        info("mods/ 下没有模组。用  wkmod new <名字>  创建一个。")
        return
    st = load_json(STATE_FILE, {}).get("deployed", {})
    print(f"{'名称':<20} {'类型':<5} {'启用':<4} {'已部署':<6} 说明")
    for m in mods:
        dep = "是" if m["name"] in st else "-"
        print(f"{m['name']:<20} {m['type']:<5} {'是' if m['enabled'] else '否':<4} {dep:<6} {m.get('description', '')}")


def cmd_enable(args): set_mod_enabled(args.name, True)
def cmd_disable(args): set_mod_enabled(args.name, False)


# ----------------------------------------------------------------------------- 命令: deploy / clean
def cmd_deploy(args):
    gp = GamePaths(require_game_dir())
    mods = iter_mods()
    lua_mods = [m for m in mods if m["type"] == "lua"]
    pak_mods = [m for m in mods if m["type"] == "pak"]
    if lua_mods and not gp.ue4ss_installed:
        die("有 lua 模组但 UE4SS 未安装。先跑:  wkmod ue4ss install")
    if gp.ue4ss_installed and not (gp.ue4ss_dir / "VTableLayout.ini").exists():
        apply_wukong_vtable_layout(gp)

    state = load_json(STATE_FILE, {})
    deployed = state.get("deployed", {})
    # 先清掉上一次部署的
    _clean_deployed(gp, deployed, quiet=True)
    deployed = {}
    use_link = not args.copy

    for m in lua_mods:
        if not m["enabled"]:
            continue
        src = m["_dir"]
        dst = gp.ue4ss_mods_dir / m["name"]
        gp.ue4ss_mods_dir.mkdir(parents=True, exist_ok=True)
        remove_path(dst)
        mode = "copy"
        if use_link:
            try:
                make_junction(dst, src)
                mode = "junction"
            except Exception as e:
                warn(f"目录联接失败 ({e}), 改用复制。")
        if mode == "copy":
            copy_tree(src, dst)
        deployed[m["name"]] = {"type": "lua", "path": str(dst), "mode": mode}
        info(f"lua  {m['name']:<18} -> {dst}  [{mode}]")

    for m in pak_mods:
        if not m["enabled"]:
            continue
        gp.pak_mods.mkdir(parents=True, exist_ok=True)
        files = []
        for pak in sorted(m["_dir"].glob("*.pak")):
            for ext in (".pak", ".ucas", ".utoc", ".sig"):
                f = pak.with_suffix(ext)
                if f.exists():
                    dst = gp.pak_mods / f.name
                    shutil.copy2(f, dst)
                    files.append(str(dst))
        deployed[m["name"]] = {"type": "pak", "files": files}
        info(f"pak  {m['name']:<18} -> {gp.pak_mods}  ({len(files)} 个文件)")

    if gp.ue4ss_installed:
        ours = {m["name"]: bool(m["enabled"]) for m in lua_mods}
        # 已禁用且未部署的从 mods.txt 里去掉 (避免 UE4SS 报找不到)
        ours = {k: v for k, v in ours.items() if v}
        write_mods_txt(gp.ue4ss_mods_txt, ours)
        info(f"mods.txt 已更新: {', '.join(ours) or '(无)'}")

    state["deployed"] = deployed
    state["game_dir"] = str(gp.game_dir)
    save_json(STATE_FILE, state)
    info("部署完成。")


def _clean_deployed(gp: GamePaths, deployed: dict, quiet=False):
    for name, d in deployed.items():
        if d.get("type") == "lua":
            remove_path(Path(d["path"]))
        else:
            for f in d.get("files", []):
                remove_path(Path(f))
        if not quiet:
            info(f"已移除 {name}")
    if deployed and gp.ue4ss_mods_txt.exists():
        # 从 mods.txt 里剔除我们部署过的条目, 其它原样保留
        out = []
        for n, v in read_mods_txt(gp.ue4ss_mods_txt):
            if n in deployed:
                continue
            out.append(v if n is None else f"{n} : {1 if v else 0}")
        gp.ue4ss_mods_txt.write_text("\n".join(out) + "\n", encoding="utf-8")


def cmd_clean(args):
    state = load_json(STATE_FILE, {})
    deployed = state.get("deployed", {})
    if not deployed:
        info("没有部署记录。")
        return
    gp = GamePaths(Path(state.get("game_dir") or require_game_dir()))
    _clean_deployed(gp, deployed)
    state["deployed"] = {}
    save_json(STATE_FILE, state)
    info("已清理。")


# ----------------------------------------------------------------------------- 命令: launch / log
def cmd_launch(args):
    gp = GamePaths(require_game_dir())
    if not args.no_deploy:
        cmd_deploy(argparse.Namespace(copy=args.copy))
    if args.exe:
        info(f"直接启动 {gp.exe}")
        subprocess.Popen([str(gp.exe)], cwd=str(gp.exe.parent))
    else:
        url = f"steam://rungameid/{STEAM_APPID}"
        info(f"通过 Steam 启动: {url}")
        if sys.platform == "win32":
            os.startfile(url)
        else:
            subprocess.Popen(["xdg-open", url])
    if args.log:
        time.sleep(8)
        cmd_log(argparse.Namespace(n=40, follow=True))


def cmd_log(args):
    gp = GamePaths(require_game_dir())
    p = gp.ue4ss_log
    if not p.exists():
        die(f"日志不存在: {p} (游戏还没跑过 UE4SS?)")
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
        for line in lines[-args.n:]:
            print(line.rstrip())
        if args.follow:
            info("--- 跟随中, Ctrl+C 退出 ---")
            try:
                while True:
                    line = f.readline()
                    if line:
                        print(line.rstrip())
                    else:
                        time.sleep(0.5)
            except KeyboardInterrupt:
                pass


# ----------------------------------------------------------------------------- 命令: new
def cmd_new(args):
    name = re.sub(r"[^A-Za-z0-9_]", "", args.name)
    if not name:
        die("名字只能含字母数字下划线")
    dst = MODS_DIR / name
    if dst.exists():
        die(f"{dst} 已存在")
    if TEMPLATE_DIR.exists():
        shutil.copytree(TEMPLATE_DIR, dst)
        main = dst / "Scripts" / "main.lua"
        if main.exists():
            main.write_text(main.read_text(encoding="utf-8").replace("__MODNAME__", name), encoding="utf-8")
    else:
        (dst / "Scripts").mkdir(parents=True)
        (dst / "Scripts" / "main.lua").write_text(f'print("[{name}] loaded\\n")\n', encoding="utf-8")
    save_json(dst / "mod.json", {"name": name, "version": "0.1.0", "type": "lua", "enabled": True,
                                 "description": ""})
    info(f"已创建 {dst}。编辑 Scripts/main.lua 后  wkmod deploy")


# ----------------------------------------------------------------------------- 入口
def build_parser():
    ap = argparse.ArgumentParser(prog="wkmod", description="黑神话: 悟空 Mod 加载器",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd")

    p = sp.add_parser("detect", help="定位游戏 / 查看状态"); p.add_argument("--game-dir"); p.set_defaults(fn=cmd_detect)

    u = sp.add_parser("ue4ss", help="安装/管理 UE4SS"); us = u.add_subparsers(dest="sub")
    p = us.add_parser("install"); p.add_argument("zip", nargs="?"); p.add_argument("--force", action="store_true"); p.add_argument("-y", "--yes", action="store_true"); p.set_defaults(fn=cmd_ue4ss_install)
    p = us.add_parser("console"); p.add_argument("state", choices=["on", "off"]); p.set_defaults(fn=cmd_ue4ss_console)
    p = us.add_parser("uninstall"); p.add_argument("-y", "--yes", action="store_true"); p.set_defaults(fn=cmd_ue4ss_uninstall)

    p = sp.add_parser("list", help="列出模组"); p.set_defaults(fn=cmd_list)
    p = sp.add_parser("enable"); p.add_argument("name"); p.set_defaults(fn=cmd_enable)
    p = sp.add_parser("disable"); p.add_argument("name"); p.set_defaults(fn=cmd_disable)
    p = sp.add_parser("deploy", help="部署启用的模组到游戏"); p.add_argument("--copy", action="store_true", help="复制而不是目录联接"); p.set_defaults(fn=cmd_deploy)
    p = sp.add_parser("clean", help="移除本工具部署的模组"); p.add_argument("-y", "--yes", action="store_true"); p.set_defaults(fn=cmd_clean)
    p = sp.add_parser("launch", help="部署并启动游戏"); p.add_argument("--no-deploy", action="store_true"); p.add_argument("--copy", action="store_true"); p.add_argument("--exe", action="store_true", help="直接跑 exe 而不是走 Steam"); p.add_argument("--log", action="store_true", help="启动后跟随日志"); p.set_defaults(fn=cmd_launch)
    p = sp.add_parser("log", help="查看 UE4SS.log"); p.add_argument("-n", type=int, default=80); p.add_argument("-f", "--follow", action="store_true"); p.set_defaults(fn=cmd_log)
    p = sp.add_parser("new", help="新建 lua 模组"); p.add_argument("name"); p.set_defaults(fn=cmd_new)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 0
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
