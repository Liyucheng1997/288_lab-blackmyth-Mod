-- 极简 UE4SS 运行时模拟, 用于离线跑 main.lua 的逻辑 (不是真实引擎!)
-- 用法: python tests/run_mod_offline.py

Key = setmetatable({}, { __index = function(_, k) return k end })
ModifierKey = setmetatable({}, { __index = function(_, k) return k end })

local Mock = { keybinds = {}, loops = {}, delayed = {}, hooks = {}, log = {} }
_G.MockRT = Mock

local function newVec(x, y, z) return { X = x, Y = y, Z = z } end

local Obj = {}
Obj.__index = Obj
function Obj:IsValid() return not self.__dead end
function Obj:GetAddress() return self.__addr end
function Obj:GetFullName() return self.__name end
function Obj:GetOuter() return self.__outer end
function Obj:get() return self end
function Obj:K2_GetActorLocation() return newVec(self.pos.X, self.pos.Y, self.pos.Z) end
function Obj:GetActorForwardVector() return newVec(1, 0, 0) end
function Obj:GetControlRotation() return { Pitch = 0, Yaw = 0, Roll = 0 } end
function Obj:SetRelativeScale3D(v) self.RelativeScale3D = newVec(v.X, v.Y, v.Z); Mock.log[#Mock.log + 1] = "scale " .. v.X end
function Obj:GetOwner() return self.owner end
function Obj:GetAttachParentActor() return self.owner end
function Obj:GetAnimInstance() return self end
function Obj:GetCurrentActiveMontage() return Mock.currentMontage end
function Obj:K2_GetComponentsByClass() return {} end

local nextAddr = 1
local function newObj(name, fields)
    local o = setmetatable(fields or {}, Obj)
    o.__name = name; o.__addr = nextAddr; nextAddr = nextAddr + 1
    return o
end

-- 世界
Mock.player = newObj("Unit_Player_Wukong_C /Game/Map.Wukong", { pos = newVec(0, 0, 0), attrs = { [1] = 1000, [151] = 1000, [153] = 200 } })
Mock.player.Mesh = Mock.player
Mock.currentMontage = nil
Mock.enemies = {
    newObj("BGU_CharacterAI /Game/Map.Wolf_1", { pos = newVec(800, 50, 0), attrs = { [151] = 5000 }, enemy = true }),
    newObj("BGU_CharacterAI /Game/Map.Wolf_2", { pos = newVec(1500, -100, 0), attrs = { [151] = 90 }, enemy = true }),
    newObj("BGU_CharacterAI /Game/Map.Behind", { pos = newVec(-600, 0, 0), attrs = { [151] = 50000 }, enemy = true }),
    newObj("BGU_CharacterAI /Game/Map.Friend", { pos = newVec(700, 0, 0), attrs = { [151] = 500 }, enemy = false }),
}
Mock.weaponComp = newObj("SkeletalMeshComponent /Game/Map.Staff.Mesh", { RelativeScale3D = newVec(1, 1, 1) })
Mock.weapon = newObj("BGUWeaponBase /Game/Map.Staff", { SkeletalMeshComp = Mock.weaponComp })
Mock.pc = newObj("PlayerController", { Pawn = Mock.player })
Mock.deadCalls = {}

local Lib = newObj("/Script/b1-Managed.Default__BGUFunctionLibraryCS", {})
function Lib:GetAttrValue(u, id) return u.attrs[id] end
function Lib:BGUSetAttrValue(u, id, v) u.attrs[id] = v end
function Lib:BGUIsEnemyTeam(a, b) return b.enemy == true end
function Lib:BGUIsUnitDead(u) return (u.attrs[151] or 1) <= 0 end
function Lib:BGUGMDead(u) Mock.deadCalls[#Mock.deadCalls + 1] = u.__name; u.__dead = true end
function Lib:GetUnitLockTargetActor(u) return Mock.lockTarget end
function Lib:BGUShowDialogueUI(u, text, dur) Mock.log[#Mock.log + 1] = "msg " .. text end
function Lib:BGUAddBuff(a, b, id, st, dur) Mock.log[#Mock.log + 1] = "buff " .. id end
function Lib:BGUGetWeaponByIndex(u, i) return Mock.weapon end
function Lib:BGUGetWeaponNum(u) return 1 end

local NonRT = newObj("/Script/b1-Managed.Default__BGUFuncLibNonRuntime", {})
Mock.projectileCalls = 0
Mock.projectileFail = false
function NonRT:ProjectileSpawnTest(spawner, target, cfg)
    if Mock.projectileFail then error("mock: spawn failed") end
    Mock.projectileCalls = Mock.projectileCalls + 1
end

local assets = {}
function LoadAsset(path)
    local p = path:gsub("^[%w_]+'(.*)'$", "%1")
    assets[p] = newObj("BGWDataAsset_ProjectileSpawnConfig " .. p, {})
end
function StaticFindObject(path)
    if path == Lib.__name then return Lib end
    if path == NonRT.__name then return NonRT end
    return assets[path]
end
function FindFirstOf(cls) if cls == "Unit_Player_Wukong_C" then return Mock.player end end
function FindAllOf(cls)
    if cls == "BGU_CharacterAI" then
        local t = { Mock.player }
        for _, e in ipairs(Mock.enemies) do t[#t + 1] = e end
        return t
    end
end
function RegisterKeyBind(key, a, b)
    local fn = b or a
    Mock.keybinds[key] = fn
end
function RegisterHook(name, fn) Mock.hooks[name] = fn end
function LoopAsync(ms, fn) Mock.loops[#Mock.loops + 1] = { ms = ms, fn = fn } end
function ExecuteWithDelay(ms, fn) Mock.delayed[#Mock.delayed + 1] = fn end
function ExecuteInGameThread(fn) fn() end
function IsKeyBindRegistered() return false end

local KML = newObj("/Script/Engine.Default__KismetMathLibrary", {})
function KML:Add_IntInt(a, b) return a + b end
package.loaded["UEHelpers"] = {
    GetPlayerController = function() return Mock.pc end,
    GetKismetMathLibrary = function() return KML end,
}

-- 驱动辅助
function Mock.press(key) assert(Mock.keybinds[key], "no keybind " .. tostring(key))(); end
function Mock.tick(n)
    for _ = 1, (n or 1) do
        for i = #Mock.loops, 1, -1 do
            local stop = Mock.loops[i].fn()
            if stop then table.remove(Mock.loops, i) end
        end
    end
end
function Mock.flushDelayed()
    local d = Mock.delayed; Mock.delayed = {}
    for _, fn in ipairs(d) do fn() end
end
