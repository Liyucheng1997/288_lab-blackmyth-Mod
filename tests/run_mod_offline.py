"""离线跑 RuyiGatling 的 Lua 逻辑 (需要 pip install lupa)。
不是真实引擎, 只用来抓 Lua 层面的运行时错误和逻辑回归。
用法: python tests/run_mod_offline.py
"""
import os, sys
from lupa import LuaRuntime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "mods", "RuyiGatling", "Scripts").replace("\\", "/")
TESTS = os.path.join(ROOT, "tests").replace("\\", "/")

lua = LuaRuntime(unpack_returned_tuples=True)
lua.execute(f'package.path = "{SCRIPTS}/?.lua;{TESTS}/?.lua;" .. package.path')
lua.execute('dofile("%s/mock_ue4ss.lua")' % TESTS)

printed = []
def _s(x):
    return x.decode("utf-8", "replace") if isinstance(x, bytes) else str(x)
lua.globals()["__pyprint"] = lambda *a: printed.append(" ".join(_s(x) for x in a))
# Lua 报错信息会把含中文的路径截断成 "...", 可能切在 UTF-8 中间, 先在 Lua 侧清洗再交给 Python
lua.execute('function print(...) local t={} for i,v in ipairs({...}) do v=tostring(v) if utf8.len(v)==nil then v=v:gsub("[\\128-\\255]","?") end t[#t+1]=v end __pyprint(table.concat(t," ")) end')

lua.execute('dofile("%s/main.lua")' % SCRIPTS)
M = lua.globals().MockRT

def lua_len(t):
    return len(list(t.values())) if hasattr(t, "values") else 0

failures = []
def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)

# 1. 加载后注册了按键
check(M.keybinds["F7"] is not None, "F7 绑定")
check(M.keybinds["F9"] is not None, "F9 绑定")

# 2. 注册钩子 (ExecuteWithDelay -> registerHooks)
M.flushDelayed()
check(M.hooks["/Script/b1-Managed.BUS_GSEventCollection:Evt_CastSkillWithAnimMontageMultiCast"] is not None, "蒙太奇钩子已注册")

# 3. 开启模式: 武器缩放 + 开火循环
M.press("F7")
check(abs(M.weaponComp.RelativeScale3D.X - 3.2) < 1e-6, "武器外观缩放已应用 X=3.2")
check(lua_len(M.loops) == 2, "开火循环已启动 (加上钩子重试循环共 2 个)")
M.press("F8")  # F7 默认直接开始扫射, 这里先关掉自动扫射以便测点射

# 4. 点射: 中键 -> 12 发, 只命中前方最近敌人 Wolf_1 (800), 不打身后/友军
M.press("MIDDLE_MOUSE_BUTTON")
M.tick(12)
wolf1, wolf2, behind, friend = [M.enemies[i] for i in (1, 2, 3, 4)]
dmg_per = 200 * 0.6
check(abs(wolf1.attrs[151] - (5000 - 12 * dmg_per)) < 1e-6, f"Wolf_1 受到 12 发伤害 hp={wolf1.attrs[151]}")
check(wolf2.attrs[151] == 90, "Wolf_2 (被 Wolf_1 挡住) 未受伤")
check(behind.attrs[151] == 50000, "身后敌人未受伤 (点射只打锥内)")
check(friend.attrs[151] == 500, "友军未受伤")

# 5. 继续扫射把 Wolf_1 打死 -> BGUGMDead, 然后转到 Wolf_2
M.press("F8")
M.tick(40)
check(any("Wolf_1" in n for n in M.deadCalls.values()), "Wolf_1 死亡触发 BGUGMDead")
check(wolf2.attrs[151] < 90, f"Wolf_2 接着被打 hp={wolf2.attrs[151]}")

# 6. 锁定目标优先
M.lockTarget = behind
M.tick(3)
check(behind.attrs[151] < 50000, "锁定目标(身后)优先被命中")

# 7. 换弹药 -> 投射物模式调用 ProjectileSpawnTest; 失败时回退 hitscan
M.press("F6")
before = M.projectileCalls
M.tick(3)
check(M.projectileCalls == before + 3, "投射物弹药调用 ProjectileSpawnTest")
M.projectileFail = True
hp_before = behind.attrs[151]
M.tick(3)
check(behind.attrs[151] < hp_before, "投射物失败回退到直射弹幕")
M.projectileFail = False

# 8. 轻棍钩子触发点射
M.press("F8")  # 关闭自动扫射
M.press("F6"); M.press("F6"); M.press("F6")  # 回到 hitscan
hook = M.hooks["/Script/b1-Managed.BUS_GSEventCollection:Evt_CastSkillWithAnimMontageMultiCast"]
ctx = lua.eval('setmetatable({}, {__index = function(_, k) if k == "get" then return function(s) return {GetOuter = function() return MockRT.player end} end end end})')
montage = lua.eval('{get = function() return {GetFullName = function() return "AnimMontage /Game/AM_Wukong_ComboA_02" end} end}')
behind.attrs[151] = 5000; behind.__dead = None
hp_before = behind.attrs[151]
hook(ctx, montage, None, None, None, None)
M.tick(2)
check(behind.attrs[151] < hp_before, "轻棍蒙太奇触发点射 (钩子)")
# 8b. 轮询蒙太奇触发点射 (无钩子路径)
M.tick(20)  # 打完上一梭子
hp_before = behind.attrs[151]
M.currentMontage = lua.eval('{IsValid = function() return true end, GetFullName = function() return "AnimMontage /Game/AM_Wukong_ComboA_03" end}')
M.tick(3)
check(behind.attrs[151] < hp_before, "轮询到轻棍蒙太奇触发点射")
M.currentMontage = None
M.tick(20)
M.lockTarget = None

# 9. 诊断不报错; 关闭恢复缩放
M.press("F9")
M.press("F7")
check(abs(M.weaponComp.RelativeScale3D.X - 1.0) < 1e-6, "关闭后武器缩放恢复")
M.tick(1)
check(lua_len(M.loops) == 0, "关闭后开火循环退出 (钩子已注册, 重试循环也退出)")

errs = [l for l in printed if ("异常" in l or "出错" in l or "nil value" in l) and " !! " not in l]
check(not errs, "无 Lua 错误日志: " + "; ".join(errs[:3]))

print("\n--- 日志 ---")
print("\n".join(printed[-12:]))
print("\n%d 项失败" % len(failures))
sys.exit(1 if failures else 0)
