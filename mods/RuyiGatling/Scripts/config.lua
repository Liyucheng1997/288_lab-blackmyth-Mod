--[[
    如意加特林 (RuyiGatling) —— 用户配置
    改完保存后, 在游戏里按 UE4SS 的热重载键 (默认 Ctrl+R) 即可生效, 不用重启游戏。

    按键名参考 UE4SS 的 Key 表 (F1..F12, A..Z, NUM_ONE.., LEFT_MOUSE_BUTTON, MIDDLE_MOUSE_BUTTON, XBUTTON_ONE 等)。
]]

local C = {}

-------------------------------------------------------------------------------
-- 按键
-------------------------------------------------------------------------------
C.Keys = {
    Toggle      = Key.F7,               -- 开/关 加特林模式
    AutoFire    = Key.F8,               -- 加特林模式下: 开/关 持续扫射 (UE4SS 无法检测“按住”, 所以是开关式)
    NextAmmo    = Key.F6,               -- 切换弹药类型 (见 C.Ammo)
    Diagnose    = Key.F9,               -- 打印诊断信息到 UE4SS 控制台/日志 (排错用)
    Burst       = Key.MIDDLE_MOUSE_BUTTON, -- 单独扣一次扳机 = 打一梭子 (BurstRounds 发)
}

-------------------------------------------------------------------------------
-- 射击手感
-------------------------------------------------------------------------------
C.Fire = {
    AutoFireOnEnable  = true,   -- 按 F7 开模式时直接开始持续扫射 (再按 F7 停)。false = 只开模式, 用 F8/中键/轻棍开火
    RoundsPerSecond   = 15,     -- 射速 (发/秒)。持续扫射与点射都用这个
    BurstRounds       = 12,     -- 一次点射打几发
    Range             = 3500,   -- 射程 (虚幻单位, 100 = 1 米)
    ConeDegrees       = 14,     -- 准星锥角 (度)。以镜头朝向为轴, 锥内最近的敌人被命中
    Penetrate         = false,  -- true = 一发子弹打穿锥内所有敌人
    AutoAimFallback   = true,   -- 锥内没有敌人时, 自动打射程内最近的敌人 (加特林式自瞄)
    PreferLockTarget  = true,   -- 锁定了目标时优先打锁定目标
    -- 轻棍触发: 加特林模式下, 每次普通攻击(玩家当前播放的蒙太奇名含以下关键字)自动补一梭子
    -- 实现: 开火循环里轮询 AnimInstance:GetCurrentActiveMontage(), 不依赖游戏事件钩子
    LightAttackBurst  = true,
    LightAttackMontageKeywords = { "comboa", "combo_a", "attack_a", "atk_a" }, -- 小写匹配
}

-------------------------------------------------------------------------------
-- 伤害 (直射弹幕/hitscan 模式使用; 投射物弹药由游戏自身结算)
-------------------------------------------------------------------------------
C.Damage = {
    -- 每发伤害 = 玩家当前攻击力(属性153) * Multiplier + Flat
    Multiplier        = 0.6,
    Flat              = 0,
    -- 血量降到 0 时调用 BGUGMDead 让敌人正常进入死亡流程
    KillWhenZero      = true,
}

-------------------------------------------------------------------------------
-- 弹药表: 按 NextAmmo 循环切换。
--   mode = "hitscan"    : 直接对锥内敌人扣血 (最稳, 一定能用)
--   mode = "projectile" : 用游戏内置的投射物配置 (BGWDataAsset_ProjectileSpawnConfig) 生成真子弹,
--                         有特效、有游戏本身的伤害结算; 若在你的版本上生成失败会自动回退 hitscan
-------------------------------------------------------------------------------
C.Ammo = {
    { name = "直射弹幕 (Hitscan)", mode = "hitscan" },
    { name = "飞龙棍雷 (Thunder Loong)", mode = "projectile",
      asset = "/Game/00Main/Design/Bullets/PlayerBullets/Wukong/Talent/BGW_Player_Wukong_Atk_Feilonggun.BGW_Player_Wukong_Atk_Feilonggun" },
    { name = "夜叉王地刺 (Yaksha Spikes)", mode = "projectile",
      asset = "/Game/00Main/Design/Bullets/PlayerBullets/Wukong/Talent/BGW_Player_Wukong_Atk_yechawang.BGW_Player_Wukong_Atk_yechawang" },
    { name = "亢金星君·星光激光 (Star Laser)", mode = "projectile",
      asset = "/Game/00Main/Design/Bullets/LYS/LYS_KJLWoman/DA/BGW_LYS_KJLWoman_StarLaser.BGW_LYS_KJLWoman_StarLaser" },
}
C.DefaultAmmoIndex = 1

-------------------------------------------------------------------------------
-- 表现
-------------------------------------------------------------------------------
C.Feel = {
    -- 每 N 发触发一次屏幕震动 (复用游戏内 buff 232 = 铜头铁臂弹反震屏)。0 = 关闭
    ShakeEveryNRounds = 6,
    ShakeBuffID       = 232,
    -- 模式切换时在屏幕上弹提示 (BGUShowDialogueUI)
    ShowMessages      = true,
    MessageSeconds    = 2.0,
}

-- 武器外观: 把手里的棍子拉粗压短, 变成“如意加特林”的枪管。关闭模式时恢复。
C.WeaponVisual = {
    Enabled = true,
    Scale   = { X = 3.2, Y = 3.2, Z = 0.55 },   -- 相对缩放 (相对原始)
    WeaponIndex = 0,                            -- BGUGetWeaponByIndex 的索引, 一般 0 = 主武器(棍)
}

-------------------------------------------------------------------------------
-- 调试
-------------------------------------------------------------------------------
C.Debug = {
    LogEveryShot        = false,  -- 每发子弹都打日志 (很吵)
    LogMontageNames     = false,  -- 打印玩家播放的所有技能蒙太奇名 (用于校准 LightAttackMontageKeywords)
    EnemyRefreshRounds  = 30,     -- 每打几发刷新一次敌人列表缓存 (FindAllOf 很重, 别太频繁)
}

return C
