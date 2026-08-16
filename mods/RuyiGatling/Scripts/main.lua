--[[
    如意加特林 (RuyiGatling) —— 黑神话: 悟空 UE4SS Lua 模组
    ---------------------------------------------------------------
    F7  开/关 加特林模式 (棍子变粗成枪管, 轻棍变成弹幕)
    F8  开/关 持续扫射
    F6  切换弹药 (直射弹幕 / 飞龙棍雷 / 夜叉王地刺 / 星光激光)
    F9  诊断 (把玩家/敌人/接口可用性打到 UE4SS 日志)
    中键 打一梭子

    实现思路 (全部通过游戏自带的 C# 桥接函数库, 不需要额外资源包):
      * /Script/b1-Managed.Default__BGUFunctionLibraryCS  —— 属性读写、Buff、锁定目标、武器句柄……
      * /Script/b1-Managed.Default__BGUFuncLibNonRuntime  —— ProjectileSpawnTest(发射者, 目标, 投射物配置)
      * 敌人 = 所有 BGU_CharacterAI 里 BGUIsEnemyTeam(玩家, 单位) 为真且未死亡的
    所有对游戏对象的调用都包在 pcall 里, 出错只写日志不崩游戏。
]]

local UEHelpers = require("UEHelpers")
local C = require("config")

local MOD = "[如意加特林]"
local function log(fmt, ...)
    local ok, s = pcall(string.format, fmt, ...)
    print(string.format("%s %s\n", MOD, ok and s or tostring(fmt)))
end

-------------------------------------------------------------------------------
-- 状态
-------------------------------------------------------------------------------
local State = {
    enabled     = false,
    autoFire    = false,
    burstLeft   = 0,
    ammoIndex   = C.DefaultAmmoIndex or 1,
    roundsFired = 0,
    loopRunning = false,
    fallbackWarned = {},
    weaponOrigScale = nil,
    weaponComp  = nil,
    enemies     = {},
    enemiesStamp = 0,
    hooksRegistered = false,
}

-------------------------------------------------------------------------------
-- 工具
-------------------------------------------------------------------------------
local function valid(o) return o ~= nil and o.IsValid ~= nil and o:IsValid() end

local ATTR = { MaxHP = 1, HP = 151, Attack = 153 }

local Lib, NonRT
local function lib()
    if not valid(Lib) then Lib = StaticFindObject("/Script/b1-Managed.Default__BGUFunctionLibraryCS") end
    return Lib
end
local function nonrt()
    if not valid(NonRT) then NonRT = StaticFindObject("/Script/b1-Managed.Default__BGUFuncLibNonRuntime") end
    return NonRT
end

-- 主菜单时控制器的 Pawn 是 DefaultEmptyPawn, 进关卡后才变成 Unit_Player_*; 所以不能缓存, 每次都从控制器取
local Player
local function isRealPlayerPawn(pawn)
    if not valid(pawn) then return false end
    local ok, name = pcall(function() return pawn:GetFullName() end)
    if not ok or not name then return false end
    if name:find("DefaultEmptyPawn") or name:find("SpectatorPawn") or name:find("Default__") then return false end
    return true
end
local function player()
    local ok, pc = pcall(UEHelpers.GetPlayerController)
    if ok and valid(pc) then
        local ok2, pawn = pcall(function() return pc.Pawn end)
        if ok2 and isRealPlayerPawn(pawn) then Player = pawn; return Player end
    end
    if isRealPlayerPawn(Player) then return Player end
    for _, cls in ipairs({ "Unit_Player_Wukong_C", "Unit_player_dasheng_C", "Unit_player_dashengg_C" }) do
        local p = FindFirstOf(cls)
        if isRealPlayerPawn(p) then Player = p; return Player end
    end
    return nil
end

local function controller()
    local ok, pc = pcall(UEHelpers.GetPlayerController)
    if ok and valid(pc) then return pc end
    return nil
end

local function safe(name, f, ...)
    local ok, r = pcall(f, ...)
    if not ok then log("%s 失败: %s", name, tostring(r)) end
    return ok, r
end

local function getAttr(unit, id)
    local ok, v = pcall(function() return lib():GetAttrValue(unit, id) end)
    if ok then return tonumber(v) end
    return nil
end
local function setAttr(unit, id, v)
    return pcall(function() lib():BGUSetAttrValue(unit, id, v) end)
end

local function showMsg(text)
    if not C.Feel.ShowMessages then return end
    local p = player()
    if not valid(p) then return end
    pcall(function() lib():BGUShowDialogueUI(p, text, C.Feel.MessageSeconds or 2.0) end)
end

local function vec(x, y, z) return { X = x, Y = y, Z = z } end
local function vsub(a, b) return vec(a.X - b.X, a.Y - b.Y, a.Z - b.Z) end
local function vlen(a) return math.sqrt(a.X * a.X + a.Y * a.Y + a.Z * a.Z) end
local function vdot(a, b) return a.X * b.X + a.Y * b.Y + a.Z * b.Z end
local function vnorm(a) local l = vlen(a); if l < 1e-6 then return vec(0, 0, 0) end return vec(a.X / l, a.Y / l, a.Z / l) end

local function actorLoc(a)
    local ok, l = pcall(function() return a:K2_GetActorLocation() end)
    if ok and l then return vec(l.X, l.Y, l.Z) end
    return nil
end

-- 镜头朝向 (控制器旋转) -> 前向单位向量; 读不到/退化时用角色朝向; 再不行返回 nil (调用方自动瞄准)
local function aimDir()
    local pc = controller()
    if valid(pc) then
        local ok, rot = pcall(function() return pc:GetControlRotation() end)
        if ok and rot and rot.Pitch and rot.Yaw then
            local pitch, yaw = math.rad(rot.Pitch), math.rad(rot.Yaw)
            local cp = math.cos(pitch)
            local d = vec(cp * math.cos(yaw), cp * math.sin(yaw), math.sin(pitch))
            if vlen(d) > 0.5 then return d, "camera" end
        end
    end
    local p = player()
    if valid(p) then
        local ok, f = pcall(function() return p:GetActorForwardVector() end)
        if ok and f and f.X then
            local d = vec(f.X, f.Y, f.Z)
            if vlen(d) > 0.5 then return vnorm(d), "actor" end
        end
    end
    return nil, "none"
end

-------------------------------------------------------------------------------
-- 敌人搜索
-------------------------------------------------------------------------------
local function refreshEnemies(force)
    -- 以“已发射弹数”作为时钟缓存敌人列表, 避免每发都遍历全部单位
    local every = C.Debug.EnemyRefreshRounds or 6
    if not force and State.enemiesStamp > 0 and (State.roundsFired - State.enemiesStamp) < every then return State.enemies end
    State.enemiesStamp = math.max(State.roundsFired, 1)
    local p = player()
    local out = {}
    if not valid(p) then State.enemies = out; return out end
    local L = lib()
    local all
    for _, cls in ipairs({ "BGU_CharacterAI", "BGUCharacterCS", "BGUCharacter" }) do
        local ok, r = pcall(FindAllOf, cls)
        if ok and r and #r > 0 then all = r; State.unitClassUsed = cls; break end
    end
    State.unitTotal = all and #all or 0
    if all then
        for _, u in ipairs(all) do
            if valid(u) and u:GetAddress() ~= p:GetAddress() then
                local okE, isEnemy = pcall(function() return L:BGUIsEnemyTeam(p, u) end)
                if okE and isEnemy then
                    local okD, dead = pcall(function() return L:BGUIsUnitDead(u) end)
                    if not (okD and dead) then out[#out + 1] = u end
                end
            end
        end
    end
    State.enemies = out
    return out
end

local function lockTarget()
    if not C.Fire.PreferLockTarget then return nil end
    local p = player()
    if not valid(p) then return nil end
    local ok, t = pcall(function() return lib():GetUnitLockTargetActor(p) end)
    if ok and valid(t) then return t end
    return nil
end

-- 返回锥内目标列表 (按距离排序), 以及锁定目标(若在射程内)
local function acquireTargets()
    local p = player()
    if not valid(p) then return {} end
    local origin = actorLoc(p)
    if not origin then return {} end
    origin.Z = origin.Z + 90 -- 大概胸口高度
    local dir = aimDir()
    local cosLimit = math.cos(math.rad(C.Fire.ConeDegrees or 14))
    local range = C.Fire.Range or 3500

    local lt = lockTarget()
    if valid(lt) then
        local l = actorLoc(lt)
        if l and vlen(vsub(l, origin)) <= range then return { lt } end
    end

    local cands, nearest = {}, nil
    for _, e in ipairs(refreshEnemies(false)) do
        if valid(e) then
            local l = actorLoc(e)
            if l then
                l.Z = l.Z + 60
                local d = vsub(l, origin)
                local dist = vlen(d)
                if dist > 1 and dist <= range then
                    if dir then
                        local cosang = vdot(vnorm(d), dir)
                        if cosang >= cosLimit then cands[#cands + 1] = { u = e, dist = dist } end
                    end
                    if not nearest or dist < nearest.dist then nearest = { u = e, dist = dist } end
                end
            end
        end
    end
    -- 锥内没人 (或没有朝向) 时自动瞄准最近的敌人
    if #cands == 0 and nearest and (C.Fire.AutoAimFallback ~= false) then
        cands[1] = nearest
    end
    table.sort(cands, function(a, b) return a.dist < b.dist end)
    local out = {}
    for i, c in ipairs(cands) do
        out[#out + 1] = c.u
        if not C.Fire.Penetrate then break end
    end
    return out
end

-------------------------------------------------------------------------------
-- 伤害结算 (hitscan)
-------------------------------------------------------------------------------
local function playerDamage()
    local p = player()
    local atk = getAttr(p, ATTR.Attack) or 100
    return atk * (C.Damage.Multiplier or 0.6) + (C.Damage.Flat or 0)
end

local function hitscanRound()
    local targets = acquireTargets()
    -- 每 30 发汇报一次命中情况 (不吵但能看出有没有在打)
    local verbose = C.Debug.LogEveryShot or State.roundsFired < 5
    if verbose or State.roundsFired % 30 == 0 then
        local d, src = aimDir()
        log("第 %d 发: 目标 %d, 已知敌人 %d, 朝向来源=%s (%s)", State.roundsFired, #targets, #State.enemies, src,
            d and string.format("%.2f %.2f %.2f", d.X, d.Y, d.Z) or "nil")
    end
    if #targets == 0 then return false end
    local dmg = playerDamage()
    local hitAny = false
    for _, u in ipairs(targets) do
        local hp = getAttr(u, ATTR.HP)
        if hp then
            local nhp = hp - dmg
            if nhp <= 0 then
                setAttr(u, ATTR.HP, 0)
                if C.Damage.KillWhenZero then pcall(function() lib():BGUGMDead(u) end) end
                State.enemiesStamp = 0
            else
                local okS, errS = setAttr(u, ATTR.HP, nhp)
                if verbose and not okS then log("  BGUSetAttrValue 报错: %s", tostring(errS)) end
            end
            hitAny = true
            if verbose then
                local after = getAttr(u, ATTR.HP)
                log("  命中 %s  hp %.0f -> 期望 %.0f, 实际 %s", u:GetFullName(), hp, math.max(nhp, 0), tostring(after))
            end
        end
    end
    return hitAny
end

-------------------------------------------------------------------------------
-- 投射物弹药
-------------------------------------------------------------------------------
local AssetCache = {}
local function loadSpawnConfig(path)
    if AssetCache[path] ~= nil then return AssetCache[path] end
    local obj = StaticFindObject(path)
    if not valid(obj) then
        pcall(LoadAsset, path)
        obj = StaticFindObject(path)
    end
    if not valid(obj) then
        -- 试试带类型前缀的写法
        local alt = "BGWDataAsset_ProjectileSpawnConfig'" .. path .. "'"
        pcall(LoadAsset, alt)
        obj = StaticFindObject(path)
    end
    AssetCache[path] = valid(obj) and obj or false
    if not AssetCache[path] then log("投射物配置加载失败: %s", path) end
    return AssetCache[path]
end

local function projectileRound(ammo)
    local p = player()
    if not valid(p) then return false end
    local cfg = loadSpawnConfig(ammo.asset)
    if not cfg then return false end
    local n = nonrt()
    if not valid(n) then
        if not State.fallbackWarned.nonrt then
            State.fallbackWarned.nonrt = true
            log("找不到 BGUFuncLibNonRuntime, 投射物弹药不可用, 回退直射弹幕")
        end
        return false
    end
    local targets = acquireTargets()
    local target = targets[1]
    local ok, err = pcall(function() n:ProjectileSpawnTest(p, target, cfg) end)
    if not ok then
        if not State.fallbackWarned[ammo.asset] then
            State.fallbackWarned[ammo.asset] = true
            log("ProjectileSpawnTest 失败 (%s): %s —— 回退直射弹幕", ammo.name, tostring(err))
        end
        return false
    end
    return true
end

-------------------------------------------------------------------------------
-- 开火
-------------------------------------------------------------------------------
local function currentAmmo() return C.Ammo[State.ammoIndex] or C.Ammo[1] end

local function fireOneRound()
    if not State.enabled then return end
    local p = player()
    if not valid(p) then return end
    local ammo = currentAmmo()
    local done = false
    if ammo.mode == "projectile" then done = projectileRound(ammo) end
    if not done then hitscanRound() end

    State.roundsFired = State.roundsFired + 1
    local n = C.Feel.ShakeEveryNRounds or 0
    if n > 0 and State.roundsFired % n == 0 and C.Feel.ShakeBuffID then
        pcall(function() lib():BGUAddBuff(p, p, C.Feel.ShakeBuffID, 1, 100) end)
    end
end

local function burst(rounds)
    if not State.enabled then return end
    State.burstLeft = math.max(State.burstLeft, rounds or C.Fire.BurstRounds or 10)
end

-- 轮询玩家当前蒙太奇: 名字变化且含轻棍关键字 -> 点射 (代替找不到的事件钩子)
State.lastMontage = nil
local function pollMontage()
    if not (State.enabled and C.Fire.LightAttackBurst) then return end
    local p = player()
    if not valid(p) then return end
    local ok, name = pcall(function()
        local anim = p.Mesh:GetAnimInstance()
        local m = anim:GetCurrentActiveMontage()
        if valid(m) then return m:GetFullName() end
        return nil
    end)
    if not ok then return end
    if name ~= State.lastMontage then
        State.lastMontage = name
        if name then
            if C.Debug.LogMontageNames then log("蒙太奇: %s", name) end
            local lname = string.lower(name)
            for _, kw in ipairs(C.Fire.LightAttackMontageKeywords or {}) do
                if string.find(lname, kw, 1, true) then burst(C.Fire.BurstRounds); return end
            end
        end
    end
end

local function ensureFireLoop()
    if State.loopRunning then return end
    State.loopRunning = true
    local interval = math.max(20, math.floor(1000 / (C.Fire.RoundsPerSecond or 15)))
    LoopAsync(interval, function()
        if not State.enabled then State.loopRunning = false; return true end
        ExecuteInGameThread(function()
            pcall(pollMontage)
            if State.autoFire or State.burstLeft > 0 then
                if State.burstLeft > 0 then State.burstLeft = State.burstLeft - 1 end
                local ok, err = pcall(fireOneRound)
                if not ok then log("开火异常: %s", tostring(err)) end
            end
        end)
        return false
    end)
end

-------------------------------------------------------------------------------
-- 武器外观 (棍 -> 枪管)
-------------------------------------------------------------------------------
local function meshOf(w)
    local ok2, comp = pcall(function() return w.SkeletalMeshComp end)
    if ok2 and valid(comp) then return comp end
    return nil
end

local function weaponMeshComp()
    local p = player()
    if not valid(p) then return nil end
    -- 1) 官方接口
    local ok, w = pcall(function() return lib():BGUGetWeaponByIndex(p, C.WeaponVisual.WeaponIndex or 0) end)
    if ok and valid(w) then
        local m = meshOf(w)
        if m then return m end
    end
    -- 2) 遍历所有武器 actor, 找挂在玩家身上的 (Owner / AttachParentActor 是玩家)
    local okA, all = pcall(FindAllOf, "BGUWeaponBase")
    if okA and all then
        for _, cand in ipairs(all) do
            if valid(cand) then
                local okO, owner = pcall(function() return cand:GetOwner() end)
                local okP, parent = pcall(function() return cand:GetAttachParentActor() end)
                local mine = (okO and valid(owner) and owner:GetAddress() == p:GetAddress())
                          or (okP and valid(parent) and parent:GetAddress() == p:GetAddress())
                if mine then
                    local m = meshOf(cand)
                    if m then return m end
                end
            end
        end
    end
    -- 3) 玩家自身挂着的骨骼网格子组件里名字带 weapon/staff/gun/棍 的
    local okC, comps = pcall(function() return p:K2_GetComponentsByClass(StaticFindObject("/Script/Engine.SkeletalMeshComponent")) end)
    if okC and comps then
        local n = #comps
        for i = 1, n do
            local c = comps[i]
            if valid(c) then
                local nm = string.lower(c:GetFullName())
                if nm:find("weapon") or nm:find("staff") or nm:find("gun") or nm:find("bang") then return c end
            end
        end
    end
    return nil
end

local function applyWeaponVisual(on)
    if not C.WeaponVisual.Enabled then return end
    local comp = weaponMeshComp()
    if not valid(comp) then
        if on then log("未拿到武器组件, 跳过外观改造 (可能还没装备棍子)") end
        return
    end
    if on then
        local ok, s = pcall(function() return comp.RelativeScale3D end)
        if ok and s and not State.weaponOrigScale then State.weaponOrigScale = vec(s.X, s.Y, s.Z) end
        local base = State.weaponOrigScale or vec(1, 1, 1)
        local S = C.WeaponVisual.Scale
        safe("SetRelativeScale3D", function()
            comp:SetRelativeScale3D(vec(base.X * S.X, base.Y * S.Y, base.Z * S.Z))
        end)
        State.weaponComp = comp
    else
        local target = State.weaponComp
        if not valid(target) then target = comp end
        if State.weaponOrigScale and valid(target) then
            safe("SetRelativeScale3D(恢复)", function() target:SetRelativeScale3D(State.weaponOrigScale) end)
        end
        State.weaponComp = nil
    end
end

-------------------------------------------------------------------------------
-- 模式切换
-------------------------------------------------------------------------------
local function setEnabled(on)
    State.enabled = on
    if on then
        State.roundsFired = 0
        applyWeaponVisual(true)
        ensureFireLoop()
        if C.Fire.AutoFireOnEnable then State.autoFire = true end
        local p = player()
        local enemies = refreshEnemies(true)
        log("加特林模式 开启  弹药=%s  玩家=%s  攻击=%s  附近敌人=%d", currentAmmo().name,
            valid(p) and p:GetFullName() or "nil", tostring(getAttr(p, ATTR.Attack)), #enemies)
        showMsg("如意加特林 已就位! 弹药: " .. currentAmmo().name .. (State.autoFire and "  [扫射中]" or ""))
    else
        State.autoFire = false
        State.burstLeft = 0
        applyWeaponVisual(false)
        log("加特林模式 关闭")
        showMsg("如意加特林 收起")
    end
end

local function nextAmmo()
    State.ammoIndex = State.ammoIndex % #C.Ammo + 1
    log("弹药切换 -> %s", currentAmmo().name)
    showMsg("弹药: " .. currentAmmo().name)
end

-------------------------------------------------------------------------------
-- 诊断
-------------------------------------------------------------------------------
local function diagnose()
    log("================ 诊断 ================")
    local p = player()
    log("玩家: %s", valid(p) and p:GetFullName() or "nil")
    log("BGUFunctionLibraryCS: %s   BGUFuncLibNonRuntime: %s", tostring(valid(lib())), tostring(valid(nonrt())))
    -- 自检: UFunction 调用与返回值到底通不通 (逐个打印原始结果/错误)
    do
        local function probe(name, f)
            local ok, r1, r2, r3 = pcall(f)
            if ok then
                local function s(v)
                    if type(v) == "userdata" then
                        local ok2, str = pcall(function() return string.format("{%.2f, %.2f, %.2f}", v.X or v.Pitch or 0, v.Y or v.Yaw or 0, v.Z or v.Roll or 0) end)
                        return ok2 and str or tostring(v)
                    end
                    return tostring(v)
                end
                log("  自检 %s => %s %s %s", name, s(r1), r2 ~= nil and s(r2) or "", r3 ~= nil and s(r3) or "")
            else
                log("  自检 %s !! %s", name, tostring(r1))
            end
        end
        local kml = UEHelpers.GetKismetMathLibrary and UEHelpers.GetKismetMathLibrary()
        probe("KismetMath Add_IntInt(2,3)", function() return kml:Add_IntInt(2, 3) end)
        probe("KismetMath Multiply_FloatFloat", function() return kml:Multiply_FloatFloat(1.5, 2.0) end)
        local pc = controller()
        probe("PC:GetControlRotation()", function() return pc:GetControlRotation() end)
        probe("PC:K2_GetActorLocation()", function() return pc:K2_GetActorLocation() end)
        if valid(p) then
            probe("P:K2_GetActorLocation()", function() return p:K2_GetActorLocation() end)
            probe("P:GetActorForwardVector()", function() return p:GetActorForwardVector() end)
            probe("P:K2_GetActorRotation()", function() return p:K2_GetActorRotation() end)
            probe("Lib:GetAttrValue(p,151)", function() return lib():GetAttrValue(p, 151) end)
            probe("Lib:GetAttrValue(p,1)", function() return lib():GetAttrValue(p, 1) end)
            probe("Lib:BGUGetFloatAttr(p,151)", function() return lib():BGUGetFloatAttr(p, 151) end)
            probe("Lib:BGUGetWeaponNum(p)", function() return lib():BGUGetWeaponNum(p) end)
            probe("Lib:BGUIsUnitDead(p)", function() return lib():BGUIsUnitDead(p) end)
            probe("Lib:BGUGetResID(p)", function() return lib():BGUGetResID(p) end)
            probe("Lib:BGUShowDialogueUI", function() return lib():BGUShowDialogueUI(p, "如意加特林 自检", 3.0) end)
            probe("P.BGUDataComp", function() return p.BGUDataComp end)
            probe("P.EventCollection", function() return p.EventCollection end)
            probe("P:GetBUSEventCollection()", function() return p:GetBUSEventCollection() end)
            probe("P:GetTeamID()", function() return p:GetTeamID() end)
        end
    end
    if valid(p) then
        log("HP=%s / MaxHP=%s  攻击=%s  每发伤害=%.1f", tostring(getAttr(p, ATTR.HP)), tostring(getAttr(p, ATTR.MaxHP)),
            tostring(getAttr(p, ATTR.Attack)), playerDamage())
        local okW, wn = pcall(function() return lib():BGUGetWeaponNum(p) end)
        log("武器数: %s", okW and tostring(wn) or ("err " .. tostring(wn)))
        local comp = weaponMeshComp()
        log("武器组件: %s", valid(comp) and comp:GetFullName() or "nil")
        pcall(function()
            local all = FindAllOf("BGUWeaponBase") or {}
            log("场景中 BGUWeaponBase 共 %d 个:", #all)
            for i, w in ipairs(all) do
                if i > 12 then log("  ..."); break end
                local okO, owner = pcall(function() return w:GetOwner() end)
                local okP, parent = pcall(function() return w:GetAttachParentActor() end)
                log("  [%d] %s  owner=%s parent=%s", i, w:GetFullName(),
                    (okO and valid(owner)) and owner:GetFullName():match("[^%.]+$") or "nil",
                    (okP and valid(parent)) and parent:GetFullName():match("[^%.]+$") or "nil")
            end
            local okC, comps = pcall(function() return p:K2_GetComponentsByClass(StaticFindObject("/Script/Engine.SkeletalMeshComponent")) end)
            if okC and comps then
                log("玩家骨骼网格组件 %d 个:", #comps)
                for i = 1, math.min(#comps, 12) do log("  %s", comps[i]:GetFullName()) end
            end
            local okM, mname = pcall(function()
                local m = p.Mesh:GetAnimInstance():GetCurrentActiveMontage()
                return valid(m) and m:GetFullName() or "(当前没在播蒙太奇)"
            end)
            log("当前蒙太奇: %s", okM and tostring(mname) or ("读取失败: " .. tostring(mname)))
        end)
        local lt = lockTarget()
        log("锁定目标: %s", valid(lt) and lt:GetFullName() or "无")
        local d = aimDir()
        log("镜头朝向: %.2f %.2f %.2f", d.X, d.Y, d.Z)
    end
    local enemies = refreshEnemies(true)
    log("单位总数: %d (类 %s)  其中敌对且存活: %d", State.unitTotal or 0, tostring(State.unitClassUsed), #enemies)
    local origin = valid(p) and actorLoc(p) or nil
    for i, e in ipairs(enemies) do
        if i > 15 then log("  ... 更多省略"); break end
        local l = actorLoc(e)
        local dist = (l and origin) and vlen(vsub(l, origin)) or -1
        log("  [%d] %s  hp=%s dist=%.0f", i, e:GetFullName(), tostring(getAttr(e, ATTR.HP)), dist)
    end
    local ts = acquireTargets()
    log("当前准星锥内目标: %d", #ts)
    for _, a in ipairs(C.Ammo) do
        if a.mode == "projectile" then
            local cfg = loadSpawnConfig(a.asset)
            log("弹药 %s : 配置资源 %s", a.name, cfg and "已加载" or "加载失败")
        end
    end
    log("模式=%s 自动扫射=%s 弹药=%s 已射击=%d 蒙太奇钩子=%s", tostring(State.enabled), tostring(State.autoFire),
        currentAmmo().name, State.roundsFired, tostring(State.montageHookOk))
    -- 探测: 玩家身上的事件集合组件类里, 所有和技能/蒙太奇有关的函数名 (用于校准 RegisterHook 路径)
    if valid(p) then
        pcall(function()
            local ec = p.EventCollection
            if not valid(ec) then
                local okG, r = pcall(function() return p:GetBUSEventCollection() end)
                if okG then ec = r end
            end
            if valid(ec) then
                local cls = ec:GetClass()
                log("事件集合组件: %s  类: %s", ec:GetFullName(), cls:GetFullName())
                local n = 0
                cls:ForEachFunction(function(fn)
                    local fname = fn:GetFullName()
                    local l = fname:lower()
                    if l:find("montage") or l:find("castskill") or l:find("skill") then
                        n = n + 1
                        if n <= 40 then log("  fn: %s", fname) end
                    end
                end)
                log("  (共 %d 个相关函数)", n)
            else
                log("事件集合组件: 未找到 (p.EventCollection 为空)")
            end
        end)
        pcall(function()
            local cls = p:GetClass()
            local chain = {}
            while valid(cls) and #chain < 8 do
                chain[#chain + 1] = cls:GetFName():ToString()
                cls = cls:GetSuperStruct()
            end
            log("玩家类链: %s", table.concat(chain, " <- "))
        end)
    end
    log("======================================")
end

-------------------------------------------------------------------------------
-- 钩子: 轻棍触发点射 / 换图后重新抓玩家
-------------------------------------------------------------------------------
local tryRegisterMontageHook

local function registerHooks()
    if State.hooksRegistered then return end
    State.hooksRegistered = true

    safe("Hook ClientRestart", function()
        RegisterHook("/Script/Engine.PlayerController:ClientRestart", function(self, NewPawn)
            local ok, pawn = pcall(function() return NewPawn:get() end)
            if ok and valid(pawn) then
                Player = pawn
                State.weaponComp = nil
                State.weaponOrigScale = nil
                State.enemiesStamp = 0
                pcall(tryRegisterMontageHook)
                if State.enabled then
                    ExecuteWithDelay(1500, function()
                        ExecuteInGameThread(function() pcall(applyWeaponVisual, true) end)
                    end)
                end
            end
        end)
    end)

    tryRegisterMontageHook()
end

-- b1-Managed 的 UFunction 在主菜单阶段还不存在, 进关卡后才有, 所以要反复尝试直到成功
State.montageHookOk = false
State.montageHookTries = 0
function tryRegisterMontageHook()
    if State.montageHookOk then return end
    State.montageHookTries = State.montageHookTries + 1
    local ok, err = pcall(function()
        RegisterHook("/Script/b1-Managed.BUS_GSEventCollection:Evt_CastSkillWithAnimMontageMultiCast",
            function(Context, Montage, PlayTimeRate, MontagePosOffset, StartSectionName, Reason)
                local okM, name = pcall(function() return Montage:get():GetFullName() end)
                if not okM or not name then return end
                -- 只处理玩家自己的事件
                local okO, owner = pcall(function() return Context:get():GetOuter() end)
                local p = player()
                if okO and valid(owner) and valid(p) and owner:GetAddress() ~= p:GetAddress() then return end
                if C.Debug.LogMontageNames then log("蒙太奇: %s", name) end
                if not (State.enabled and C.Fire.LightAttackBurst) then return end
                local lname = string.lower(name)
                for _, kw in ipairs(C.Fire.LightAttackMontageKeywords or {}) do
                    if string.find(lname, kw, 1, true) then
                        burst(C.Fire.BurstRounds)
                        return
                    end
                end
            end)
    end)
    if ok then
        State.montageHookOk = true
        log("轻棍蒙太奇钩子已注册 (第 %s 次尝试)", tostring(State.montageHookTries))
    elseif State.montageHookTries == 1 or State.montageHookTries % 10 == 0 then
        log("蒙太奇钩子暂不可用 (第 %d 次): %s", State.montageHookTries, tostring(err):match("^[^\n]*") or "")
    end
end

-- 每 5 秒重试一次, 最多 10 分钟; 进关卡 (ClientRestart) 时也会立刻再试
LoopAsync(5000, function()
    if State.montageHookOk or State.montageHookTries > 120 then return true end
    ExecuteInGameThread(function() pcall(tryRegisterMontageHook) end)
    return false
end)

-------------------------------------------------------------------------------
-- 按键
-------------------------------------------------------------------------------
local function bind(key, name, fn)
    if key == nil then return end
    local ok, err = pcall(RegisterKeyBind, key, function()
        local ok2, e2 = pcall(fn)
        if not ok2 then log("%s 出错: %s", name, tostring(e2)) end
    end)
    if not ok then log("绑定按键 %s 失败: %s", name, tostring(err)) end
end

bind(C.Keys.Toggle,   "Toggle",   function() setEnabled(not State.enabled) end)
bind(C.Keys.AutoFire, "AutoFire", function()
    if not State.enabled then setEnabled(true) end
    State.autoFire = not State.autoFire
    ensureFireLoop()
    log("持续扫射 %s", State.autoFire and "开" or "关")
    showMsg(State.autoFire and "持续扫射: 开" or "持续扫射: 关")
end)
bind(C.Keys.NextAmmo, "NextAmmo", nextAmmo)
bind(C.Keys.Diagnose, "Diagnose", diagnose)
bind(C.Keys.Burst,    "Burst",    function() if State.enabled then burst(C.Fire.BurstRounds) end end)

ExecuteWithDelay(3000, function() ExecuteInGameThread(registerHooks) end)

-- 开机自检: UFunction 调用能不能拿到返回值 (黑悟空虚表被改过, 需要 VTableLayout.ini 修正; 见 docs)
ExecuteWithDelay(8000, function()
    ExecuteInGameThread(function()
        local ok, r = pcall(function()
            local kml = UEHelpers.GetKismetMathLibrary()
            return kml:Add_IntInt(2, 3)
        end)
        if ok and r == 5 then
            log("自检通过: KismetMath 2+3=5, UFunction 调用正常")
        else
            log("自检失败: KismetMath 2+3 => %s  (UFunction 返回值读不到; 检查 ue4ss/VTableLayout.ini 是否是黑悟空专用版)", tostring(r))
        end
    end)
end)

log("已加载。F7 开关加特林 | F8 持续扫射 | F6 换弹药 | 中键 点射 | F9 诊断")
