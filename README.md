# 魔物娘TD 私服

《モンスター娘TD》（DMM 版 / R18）的本地服务器、PC 客户端接入插件和简体中文翻译。装好后游戏连你自己电脑上的服务器，存档在本地，控制台里能开活动、改存档、一键汉化。

## 仓库里有什么，你要自备什么

仓库提供：私服本体 `server/`、控制台、客户端接入插件 `client-plugin/`（`dist/` 里有编译好的）、全部界面与剧情的简体中文译文 `localization/`。

你要自备：游戏本体（目录里有 `monster_musume_tdx.exe`）和游戏资源（趁官服还在，进游戏把「一括ダウンロード」做完）。主数据用下面的脚本从官方资源服务器拉，也需要它还在线。适配的游戏、BepInEx 版本见 [CHANGELOG.md](CHANGELOG.md)。

## 第一次接私服

照着做一遍，大概十五分钟，之后每次玩只需要打开控制台点两个按钮。

### 0. 准备环境

| 需要 | 哪里来 | 怎么确认 |
|---|---|---|
| Windows 10 / 11 64 位 | — | — |
| Python 3.12 或更新 | [python.org](https://www.python.org/downloads/windows/)，安装时勾上 **Add python.exe to PATH** | 终端里 `python --version` |
| WebView2 运行时 | Win11 自带；Win10 没有的话从[微软下载](https://developer.microsoft.com/microsoft-edge/webview2/)「Evergreen Bootstrapper」 | 控制台窗口能打开就行 |
| BepInEx 6（IL2CPP、x64） | [builds.bepinex.dev](https://builds.bepinex.dev/projects/bepinex_be)，下 `BepInEx-Unity.IL2CPP-win-x64-6.0.0-be.7xx.zip`，版本号见 CHANGELOG | 见第 2 步 |
| Git（可选） | 用来下载和更新本仓库；不装就点 GitHub 的 Download ZIP | — |

不需要 .NET SDK（除非你要自己改插件），不需要 DMM 账号登录——私服自带本地登录。

### 1. 下载本仓库

建议放在游戏目录旁边，例如：

```
D:\Games\
├─ mnms_td_r18\          ← 游戏目录（里面有 monster_musume_tdx.exe）
└─ monster-musume-ps\    ← 本仓库
```

```powershell
git clone --depth 1 https://github.com/alksdesu/monster-musume-ps.git
```

译文库有几十 MB，`--depth 1` 只拉最新一份，快很多。

游戏目录不在旁边也没关系：打开 `server\data\settings.json`，把 `client.dir` 改成你的游戏目录（可以写绝对路径，例如 `"D:/DMMGames/mnms_td_r18"`）。

### 2. 给游戏装 BepInEx

1. 把 BepInEx 的 zip 解压到游戏目录，解压完游戏目录里应该多出 `BepInEx\`、`dotnet\`、`winhttp.dll`、`doorstop_config.ini`。
2. 像平常一样启动一次游戏（这次还是连官服，连不上也没事），等它自己退出或者到标题画面后关掉。第一次启动 BepInEx 要生成 `BepInEx\interop\` 下的程序集，会多等一两分钟，黑窗口里有进度。
3. 看到 `BepInEx\interop\` 里有一堆 dll 就成了。

### 3. 安装私服的依赖

在仓库目录打开 PowerShell（资源管理器地址栏输入 `powershell` 回车），执行：

```powershell
python -m pip install -e ".[console,tools]"
```

如果提示「禁止运行脚本」，先执行一次 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`，以后就不用管了。仓库是用 Download ZIP 下的话，解压前先右键 zip → 属性 → 勾「解除锁定」，否则 Windows 会把里面的脚本当成网上来的拦住。

### 4. 拉取主数据

```powershell
python tools\fetch_master.py
```

它会问官方接口当前版本，下载五个数据表文件、解密、导出到 `masterdata\`（约 150 MB，4000 多个 json）。打印出 `Data: ... tables=174` 这样的行就是成功。需要能连上官方的版本接口（`gapi.game-monmusu-td.net`）和资源服务器（`assets.game-monmusu-td.net`）。

### 5. 打开控制台，开服，开游戏

```powershell
.\start-console.ps1
```

会弹出一个窗口。右上角点 **启动服务器**，几秒后状态变成「运行中」；再点 **开始游戏**。第一次开游戏会把接入插件复制进游戏目录、写好配置，然后游戏自己启动，直接进到本地存档的新手教程。

关掉控制台窗口不会关服务器和游戏；下次打开控制台会接上它们。

### 6. 切成中文

控制台左边点 **翻译**，按 **全部翻译成简体中文**，然后点 **重启游戏，让改动生效**（或者自己重开游戏）。界面、说明、角色名、剧情对话就都是中文了。按钮和标题画面上那些画进图片里的字还是日文，这个改不了。

想换回日文，同一页按 **切回日文原文**。

到这里就接好了。以后每次玩：打开控制台 → 启动服务器 → 开始游戏。

## 常见问题

- **控制台窗口打不开**：在仓库目录跑 `.\start-console.ps1 -Foreground`，报错留在终端里；多半是 `pywebview` 没装好或缺 WebView2。
- **「开始游戏」失败，概览页有红字**：红字下面是原因。常见的是 `client.dir` 指错、游戏目录里没有 `BepInEx\core`、游戏已经开着。
- **游戏还在连官服 / 卡标题**：看游戏目录 `BepInEx\LogOutput.log` 有没有 `Local game server: http://127.0.0.1:18621` 这一行；没有就是插件没加载，确认 BepInEx 是 IL2CPP 版且 `BepInEx\plugins\MonmusuTd.Local\` 里有 dll。
- **中文显示方块**：系统没有微软雅黑。在 `server\data\settings.json` 的 `client.font` 填一个中文字体文件路径（如 `C:/Windows/Fonts/simhei.ttf`），重开游戏。
- **切了翻译没变化**：翻译是游戏启动时拉的，重开游戏。
- **端口 18621 被占**：改 `server\data\settings.json` 的 `port` 和 `public_url`。
- **想从头再来**：关掉游戏和服务器，删 `saves\`（备份在 `backups\`）。

## 目录

`server/` 私服与控制台，`client-plugin/` 接入插件，`localization/` 译文，`protocol/` 接口定义，`tools/` 主数据与剧情脚本下载，`tests/` 测试；`masterdata/`、`saves/`、`backups/`、`logs/` 是本机生成的，不在仓库里。

## 改代码

测试：`python -m pip install -e ".[test]"` 后 `python -m pytest -q`（要先有主数据）。插件：装 .NET 6 SDK 后开游戏时自动重编译并刷新 `client-plugin\dist`。翻译工具在 `server/monmusu_l10n`，`extract` / `translate` / `status` / `rename` 四个子命令，密钥只从环境变量 `DEEPSEEK_API_KEY` 读。

## 声明

本项目只供学习使用。
