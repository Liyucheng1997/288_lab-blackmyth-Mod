--[[
    __MODNAME__ —— 黑神话: 悟空 UE4SS Lua 模组模板
    常用接口速查见 docs/黑悟空_UE4SS_API笔记.md
]]
local UEHelpers = require("UEHelpers")

local MOD = "[__MODNAME__]"
local function log(fmt, ...) print(MOD .. " " .. string.format(fmt, ...) .. "\n") end

local function valid(o) return o ~= nil and o.IsValid ~= nil and o:IsValid() end

local Lib
local function lib()
    if not valid(Lib) then Lib = StaticFindObject("/Script/b1-Managed.Default__BGUFunctionLibraryCS") end
    return Lib
end

local function player()
    local ok, pc = pcall(UEHelpers.GetPlayerController)
    if ok and valid(pc) and valid(pc.Pawn) then return pc.Pawn end
    return FindFirstOf("Unit_Player_Wukong_C")
end

-- 示例: F10 打印玩家血量/攻击
RegisterKeyBind(Key.F10, function()
    local p = player()
    if not valid(p) then log("没找到玩家"); return end
    local ok, hp = pcall(function() return lib():GetAttrValue(p, 151) end)
    local ok2, atk = pcall(function() return lib():GetAttrValue(p, 153) end)
    log("玩家 %s  HP=%s 攻击=%s", p:GetFullName(), ok and tostring(hp) or "?", ok2 and tostring(atk) or "?")
end)

log("已加载")
