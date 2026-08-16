# 黑神话: 悟空 Mod 工程 —— 如意加特林 + wkmod 加载器

> **v1.0 状态 (2026-08-16)** — 暂停开发, 留档以便日后继续。
>
> ✅ 已打通 (实机验证):
> - `wkmod` 加载器: 定位游戏 / 安装 UE4SS / 部署 / 清理 / 启动, 端到端测试通过
> - UE4SS 在当前版本黑悟空上稳定运行的三个必要修正 (否则闪退或函数调用无效), 已固化到加载器:
>   `HookInitGameState=0` + `HookLoadMap=0`; 黑悟空专用 `VTableLayout.ini` (ProcessEvent 在虚表第 74 号); `bUseUObjectArrayCache=true`
> - 模组能拿到玩家/攻击力/敌人列表, 屏幕提示、震屏 buff 生效, `KismetMath 2+3=5` 自检通过
>
> ⏳ 未完成:
> - 直射弹幕扣血是否生效未确认 (最后一版加了逐发日志与自动瞄准, 还没跑过)
> - 棍子外观缩放: `BGUGetWeaponByIndex` 拿不到武器, 备用查找路径未验证
> - 投射物弹药 (`ProjectileSpawnTest`): 三个资源里只有“夜叉王地刺”能加载, 未实际发射过
> - 轻棍触发: 事件钩子 `Evt_CastSkillWithAnimMontageMultiCast` 在当前版本不存在, 已改为轮询蒙太奇, 未验证
> - 最后一次实机测试出现闪退, 原因未查 (`%LOCALAPPDATA%\b1\Saved\Crashes` 有报告)
>
> 续做时先看 [docs/黑悟空_UE4SS_API笔记.md](docs/黑悟空_UE4SS_API笔记.md)。

```
289_黑悟空Mod/
├── wkmod.py / wkmod.bat        加载器 CLI (Python 3, 无第三方依赖)
├── loader.config.json          加载器配置 (自动生成: 游戏路径)
├── mods/
│   ├── RuyiGatling/            ★ 如意加特林 (UE4SS Lua 模组)
│   │   ├── mod.json
│   │   ├── Scripts/main.lua    逻辑
│   │   ├── Scripts/config.lua  按键 / 射速 / 伤害 / 弹药 / 外观
│   │   └── README.md
│   └── _template/              新模组模板 (wkmod new <名字>)
├── docs/
│   ├── 黑悟空_UE4SS_API笔记.md  以后加功能查这个: 函数库、属性ID、资源路径、钩子
│   └── types/                  游戏 b1-Managed / b1 模块的类型导出 (查函数签名)
└── tests/                      离线测试 (模拟 UE4SS 跑 Lua; 加载器端到端)
```

## 为什么做成加载器

模组源码留在这个工程里, 加载器把它们“投影”到游戏目录:

- lua 模组 → `b1/Binaries/Win64/ue4ss/Mods/<名字>` (默认用**目录联接**, 你在工程里改一行代码, 游戏里 Ctrl+R 热重载立刻生效; `--copy` 可改为复制)
- pak 模组 → `b1/Content/Paks/~mods/`
- 自动维护 `mods.txt`, `wkmod clean` 一键全部撤走, 游戏目录随时能恢复干净。
- 以后加功能: `wkmod new 名字` 建一个新模组目录, 写 lua, `wkmod deploy`; 或者直接往 `RuyiGatling/Scripts` 里加文件。

## 上手 (3 步)

```bash
wkmod detect                 # 1. 找到游戏 (已检测到 E:\SteamLibrary\...\BlackMythWukong)
wkmod ue4ss install          # 2. 装 UE4SS (会提示从 GitHub 下载 experimental 版, 也可 wkmod ue4ss install 本地zip)
wkmod launch                 # 3. 部署模组 + 通过 Steam 启动游戏
```
进游戏后: **F7** 开加特林, **F8** 持续扫射, **F6** 换弹药, **鼠标中键** 点射, **F9** 诊断到日志 (`wkmod log`)。

其它命令: `wkmod list` / `enable` / `disable` / `deploy` / `clean` / `log -f` / `ue4ss console on|off` / `ue4ss uninstall` / `new <名字>`。
`wkmod -h` 看全部。

## 如意加特林做了什么

见 [mods/RuyiGatling/README.md](mods/RuyiGatling/README.md)。一句话: 全走游戏自带的 `BGUFunctionLibraryCS` C# 桥接函数
(读攻击力、对锥内敌人扣血、真子弹生成、武器网格缩放、震屏 buff), 不改资源包, 不碰存档。

## 首次实机要确认的三件事 (都在 F9 诊断 + 日志里能看到)

1. `ProjectileSpawnTest` 在 shipping 版是否可调 —— 不行也没关系, 会自动回退到直射弹幕 (hitscan), 一定能打。
2. 轻棍蒙太奇名字是否含 `comboa` —— 不含就把 `Debug.LogMontageNames = true` 看一眼名字, 填进 `LightAttackMontageKeywords`。
3. `BGUGetWeaponByIndex(玩家, 0)` 是否拿到棍子 —— 拿不到只是没有“枪管”外观, 其它照常。

## 测试

```bash
pip install lupa                       # 只为离线跑 Lua
python tests/run_mod_offline.py        # 模拟 UE4SS 环境跑加特林逻辑 (18 项)
python tests/test_loader.py            # 加载器 install/deploy/clean/new 端到端 (临时目录, 不动真游戏)
```

## 注意

- 单机自用。UE4SS 是注入式工具, 装了之后如果游戏更新出问题, `wkmod ue4ss uninstall` 即可还原。
- 修改数值属性 (扣血) 不经过游戏正规伤害结算, 没有伤害飘字/受击硬直; 想要“真子弹”体验用 F6 切到投射物弹药。
