# 如意加特林 (RuyiGatling)

黑神话: 悟空 的 UE4SS Lua 模组。开启后猴子手里的棍子被拉粗压短成“枪管”, 轻棍/中键/持续扫射都会向准星方向喷洒弹幕, 炮轰妖魔鬼怪满天神佛。

## 按键 (可在 `Scripts/config.lua` 改)

| 键 | 作用 |
|---|---|
| F7 | 开 / 关 加特林模式 |
| F8 | 开 / 关 持续扫射 (UE4SS 无法感知“按住”, 所以是开关) |
| F6 | 切换弹药: 直射弹幕 → 飞龙棍雷 → 夜叉王地刺 → 星光激光 |
| 鼠标中键 | 打一梭子 (默认 12 发) |
| F9 | 诊断: 把玩家/敌人/接口可用性写进 UE4SS 日志 |
| 轻棍 | 加特林模式下每次普攻自动补一梭子 |

## 原理

- 全部走游戏自带的 C# 桥接函数库 `BGUFunctionLibraryCS` / `BGUFuncLibNonRuntime`, **不需要**替换任何资源包, 也不改存档数据。
- **直射弹幕 (hitscan)**: 以镜头朝向为轴, 找锥角内最近的敌对单位 (`BGU_CharacterAI` + `BGUIsEnemyTeam`), 每发扣 `攻击力(属性153) × 0.6` 的血 (`BGUSetAttrValue(单位, 151, ...)`), 归零时调 `BGUGMDead` 走正常死亡流程。这一条路径最稳。
- **投射物弹药**: 调 `BGUFuncLibNonRuntime:ProjectileSpawnTest(玩家, 目标, 投射物配置资源)` 让游戏生成真正的子弹 (有特效, 走游戏本身的伤害结算)。这条接口在 shipping 版里是否可用需要实机验证; 失败会自动回退到直射弹幕并在日志里说明。
- 武器外观: `BGUGetWeaponByIndex(玩家, 0).SkeletalMeshComp:SetRelativeScale3D(...)`, 关闭时恢复。
- 屏幕震动: 复用游戏 buff 232 (弹反震屏)。

## 调参 / 排错

1. 改 `Scripts/config.lua`, 游戏里 Ctrl+R (UE4SS 热重载) 生效。
2. 有问题先按 F9, 然后看 `b1/Binaries/Win64/ue4ss/UE4SS.log` (或用 `wkmod log`)。
3. 轻棍不触发点射: 把 `Debug.LogMontageNames = true`, 打几下, 看日志里蒙太奇名字, 把关键字填进 `Fire.LightAttackMontageKeywords`。
4. 投射物弹药无效: 看日志里 `ProjectileSpawnTest 失败` 那一行的错误信息。

## 离线测试

```bash
pip install lupa
python tests/run_mod_offline.py
```
用 `tests/mock_ue4ss.lua` 模拟 UE4SS 环境跑一遍逻辑 (不是真引擎, 只抓 Lua 层错误)。
