# 黑神话: 悟空 —— UE4SS Lua 开发笔记

以后加新功能主要查这里。`docs/types/` 里是从游戏 shipping 版导出的 UE4SS 类型注解
(来源: github.com/Ferrochrom3/Black-Myth-Wukong-Lua-Mods, shared/types, 2025-01), 在编辑器里
`grep "^function UBGUFunctionLibraryCS"` 就能看到全部可调函数。

## 基本事实

- 引擎: UE5 (自定义), 主模块 `b1`, 游戏逻辑大多在 C# 桥接模块 **`b1-Managed`** 里 (类名 `BGU*`, `BUS_*`, `BGW*`)。
- UE4SS: 用 **experimental** 版 (v3.0.1-1028+); 布局 `b1/Binaries/Win64/dwmapi.dll` + `Win64/ue4ss/{UE4SS.dll, UE4SS-settings.ini, Mods/}`。
- 模组脚本: `ue4ss/Mods/<Name>/Scripts/main.lua`, 在 `ue4ss/Mods/mods.txt` 里 `<Name> : 1` (Keybinds 必须最后)。
- pak 模组: `b1/Content/Paks/~mods/*.pak`。
- 热重载: `UE4SS-settings.ini` `[General] EnableHotReloadSystem = 1`, 游戏内 Ctrl+R。
- `[General] bUseUObjectArrayCache = true` (社区经验, issue #632)。
- **必须** `[Hooks] HookInitGameState = 0` 和 `HookLoadMap = 0` —— 2026-08 实测: 这两个钩子任一开着, 游戏在主菜单出现后 3-10 秒 `EXCEPTION_ACCESS_VIOLATION reading 0x2` 闪退 (二分法逐个钩子试出来的; 游戏无 UE4SS 正常, 全钩子关正常)。Lua 模组不需要它们。wkmod 安装时自动写。
- **必须** `ue4ss/VTableLayout.ini` 用黑悟空专用版 (`tools/ue4ss_config/VTableLayout.ini`, 由 `tools/make_vtable_layout.py` 从官方 5.0 模板生成)。
  原因: 黑悟空的 `UObject` 虚表比 UE4SS 内置 5.0 布局**少 1 项** (ProcessEvent 之前), UE4SS 默认按第 75 号 (0x258) 调 ProcessEvent, 那格是个空函数;
  真 ProcessEvent 在第 74 号 (0x250)。症状: 所有 UFunction 调用无效果、返回 0 (连 `KismetMathLibrary:Add_IntInt(2,3)` 都是 0), 属性读取却正常。
  定位方法: `tools/find_processevent_live.py --obj <任意UObject的GetAddress()>` 在游戏运行时读内存, 按 ProcessEvent 的指令特征
  (读 UFunction+0xB0 FunctionFlags / +0xB6 ParmsSize / +0xB8 ReturnValueOffset) 找真实索引。exe 有 Denuvo, 磁盘静态分析不可用。
  模组开机 8 秒会自检 (`自检通过: KismetMath 2+3=5`), 失败会在日志提示。
- 排查方法: 崩溃报告在 `%LOCALAPPDATA%\b1\Saved\Crashes\`; UE4SS 日志在 `ue4ss/UE4SS.log`。
- 日志: `ue4ss/UE4SS.log`; 图形控制台 `[Debug] GuiConsoleEnabled/GuiConsoleVisible`。
- Steam AppID 2358720。

## 拿到关键对象

```lua
local UEHelpers = require("UEHelpers")
local pc     = UEHelpers.GetPlayerController()
local player = pc.Pawn                              -- 类名 Unit_Player_Wukong_C (大圣: Unit_player_dasheng_C / dashengg)
-- 或 FindFirstOf("Unit_Player_Wukong_C")
local lib    = StaticFindObject("/Script/b1-Managed.Default__BGUFunctionLibraryCS")   -- 万能函数库
local nonrt  = StaticFindObject("/Script/b1-Managed.Default__BGUFuncLibNonRuntime")   -- 含 ProjectileSpawnTest
local units  = FindAllOf("BGU_CharacterAI")         -- 所有单位 (含玩家/怪/NPC)
```
换图/复活后 Pawn 会变, 挂 `RegisterHook("/Script/Engine.PlayerController:ClientRestart", function(self, NewPawn) ... end)` 刷新。

## BGUFunctionLibraryCS 常用函数 (全部 `lib:Xxx(...)`)

| 函数 | 说明 |
|---|---|
| `GetAttrValue(Unit, AttrID)` / `BGUGetFloatAttr` | 读属性 |
| `BGUSetAttrValue(Unit, AttrID, Value)` | 写属性 |
| `GM_AddAttr(Unit, AttrType, Add)` | 加属性 |
| `BGUAddBuff(Caster, Target, BuffID, SourceType=1, DurationMs)` | 加 buff (震屏 232, 慢动作 240, 无敌 114) |
| `BGURemoveBuff / BGUHasBuff / BGURemoveAllBuff` | buff |
| `BGUIsEnemyTeam(Self, Other)` / `BGUIsUnitDead(Unit)` | 敌我 / 死亡 |
| `BGUGMDead(Unit)` / `UnitSuicide(Unit)` | 直接杀 |
| `GetUnitLockTargetActor(Unit)` / `BGUGetTarget` / `BGUClosestPerceivedTarget` | 目标 |
| `BGUGetWeaponNum(Unit)` / `BGUGetWeaponByIndex(Owner, i)` → `ABGUWeaponBase{SkeletalMeshComp}` | 武器 |
| `BGUTryCastSpell(Unit, SkillID, SourceType, IsUseComboingSection)` | 按 ID 放技能 |
| `TriggerEffect(Unit, EffectID)` / `TriggerEffectToTarget(Unit, EffectID, Target)` | 效果 |
| `BGUSpawnActor(World, ActorClass, Location, Rotation)` | 生成 actor |
| `KJLSpawnProjectile(Spawner, Target, Tag, BulletID, ...)` | 亢金龙那套子弹生成 (参数多, 见 types) |
| `DestroyAllProjectile(Unit)` | 清子弹 |
| `BGUShowDialogueUI(Unit, Text, Duration)` | 屏幕提示 |
| `BGUAISetSpeedRate(Unit, Rate)` / `BGUSetUnitCritRateBase(Unit, Rate)` | 速度 / 暴击 |
| `BGUUnitEquipFaBao(Unit, ID)` / `BGUUnitCastFaBaoSkill(Unit)` | 法宝 |
| `EnterPlayerSkillCamera(WorldContext, CameraID)` | 技能镜头 |

`BGUFuncLibNonRuntime:ProjectileSpawnTest(Spawner, Target, UBGWDataAsset_ProjectileSpawnConfig)` —— 按投射物配置资源生成子弹 (本模组的“投射物弹药”走这个)。

## 属性 ID (AttrID)

| ID | 含义 | | ID | 含义 |
|---|---|---|---|---|
| 1 | 最大生命 | | 151 | 当前生命 |
| 2 | 最大法力 | | 152 | 当前法力 |
| 8 | 最大体力 | | 158 | 当前体力 |
| 103 | 攻击 (基础) | | 153 | 攻击 (当前) |
| 104 | 防御 (基础) | | 154 | 防御 (当前) |
| 39 | 棍势 | | 191 | 当前棍势 |
| 119 | 伤害加成? | | 120 | 减伤 |
| 132-135 | 抗性 (冻/烧/毒/雷) | | 182-185 | 当前抗性 |
| 159 | 体力恢复速率 | | 161 | 暴击率 |
| 201 | 当前葫芦 | | 202 | 当前元气 |

## 已知资源路径

- 投射物配置 (BGWDataAsset_ProjectileSpawnConfig):
  - `/Game/00Main/Design/Bullets/PlayerBullets/Wukong/Talent/BGW_Player_Wukong_Atk_Feilonggun.BGW_Player_Wukong_Atk_Feilonggun` (飞龙棍雷)
  - `/Game/00Main/Design/Bullets/PlayerBullets/Wukong/Talent/BGW_Player_Wukong_Atk_yechawang.BGW_Player_Wukong_Atk_yechawang` (夜叉王地刺)
  - `/Game/00Main/Design/Bullets/LYS/LYS_KJLWoman/DA/BGW_LYS_KJLWoman_StarLaser.BGW_LYS_KJLWoman_StarLaser` (星光激光)
- 数据表 (改数值/加 buff 效果要打 pak): `b1/Content/00Main/PBTable/NoneRuntime/*.data` (FUStBuffDesc-*.data, EquipDesc.data, TalentSDesc.data ...)

## 有用的事件钩子

```lua
-- 玩家/单位放技能 (蒙太奇名可判断哪一招)
RegisterHook("/Script/b1-Managed.BUS_GSEventCollection:Evt_CastSkillWithAnimMontageMultiCast",
  function(Context, Montage, PlayTimeRate, MontagePosOffset, StartSectionName, Reason)
    local name  = Montage:get():GetFullName()   -- 例: AM_Dasheng_ComboA_01, AM_Wukong_xuli_attack_4
    local owner = Context:get():GetOuter()      -- 施法单位
  end)
-- 加 buff
RegisterHook("/Script/b1-Managed.BUS_GSEventCollection:Evt_BuffAdd_Multicast_Invoke",
  function(Context, BuffID, Caster, RootCaster, Duration, BuffSourceType, bRecursed, Snapshot) end)
```
已知蒙太奇关键字: `xuli_attack_4` (4豆蓄力劈), `AM_Wukong_xuli_B_attack_4` (戳), `AM_Wukong_Xuli_C_attack_4` (立),
`AM_wukong_comboc_z_01_start` (风云转), `AM_wukong_comboc_z_02` (江海翻), `AM_Dasheng_ComboA_01..05` (大圣普攻)。

## 线程注意

- `LoopAsync` / `ExecuteWithDelay` / `ExecuteAsync` 的回调**不在游戏线程**, 里面调游戏对象要包 `ExecuteInGameThread(function() ... end)`。
- 在 RegisterHook 回调里用 `ExecuteWithDelay` 曾导致换图崩溃 (社区经验), 尽量避免。
- 所有对游戏对象的调用包 `pcall`。

## 参考

- UE4SS 文档: https://docs.ue4ss.com/  (Lua API: RegisterKeyBind, RegisterHook, FindAllOf, StaticFindObject, LoadAsset, LoopAsync, ExecuteInGameThread ...)
- Nexus: RE-UE4SS for Black Myth (mod 19)
- github.com/KenGoossens/GodModeFix_WuKong (属性 ID)
- github.com/Ferrochrom3/Black-Myth-Wukong-Lua-Mods (MoreEffects: buff/投射物; shared/types 类型导出)
