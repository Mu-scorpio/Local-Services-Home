# 本地服务管理首页 / Local Services Home

中文 | [English](#english)

---

## 中文

### 简介

**本地服务管理首页** 是一个面向 Windows 的本机 WebUI 服务控制台。  
开发与运维中，本机常会运行多个映射到 `127.0.0.1` 的工具（数据库面板、AI WebUI、监控页、自建小服务等），端口分散、启停脚本各异，管理成本高。

本项目提供统一页面，用于：

- 登记本地服务（端口、目录、启动脚本、备注）
- 一键 **启动 / 无窗口启动 / 停止**
- 实时查看 **运行状态、进程名、PID**
- 通过 **端口探测** 自动识别服务目录与启动脚本
- 使用各服务 WebUI 的 **favicon** 作为卡片图标
- **白天 / 夜间** 两套界面主题

管理器本身仅监听本机：`http://127.0.0.1:18888`。

### 环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows（推荐 Windows 10/11） |
| 运行时 | Python 3.10+ |
| 权限 | 普通用户即可；个别受保护进程可能需要管理员权限才能读取/结束 |

### 使用方法

#### 1. 启动 / 停止管理器

| 操作 | 方式 |
|------|------|
| 启动 | 双击 `start.bat`（首次会安装依赖，并打开浏览器） |
| 停止 | 双击 `stop.bat` |

也可手动：

```bat
python -m pip install -r requirements.txt
python -m backend.launcher start
python -m backend.launcher stop
```

打开地址：**http://127.0.0.1:18888**

#### 2. 添加服务（推荐：端口探测）

1. 先**手动启动**一次目标服务，确保端口已在监听  
2. 页面点击 **添加服务**  
3. 填写 **端口**，点击 **从端口探测**（或在端口框按 Enter）  
4. 程序自动识别：监听进程、工作目录、启动脚本、建议名称  
5. 确认后保存  

若服务尚未运行，也可 **选择文件夹**，再 **扫描脚本** 手动配置。

#### 3. 日常管理

在服务卡片上可：

| 操作 | 说明 |
|------|------|
| 打开 | 在新标签打开 WebUI |
| 启动 | 执行启动脚本（显示控制台窗口） |
| 无窗口 | 后台执行启动脚本，不弹黑窗 |
| 停止 | **结束占用该端口的进程**（默认无需 stop 脚本） |
| 编辑 | 右上角铅笔图标，修改名称/端口/目录等 |
| 删除 | 仅删除本工具中的配置，不删业务目录 |

顶栏可在 **白天 / 夜间** 主题间切换，选择会保存在浏览器本地。

#### 4. 示例服务（可选）

仓库内 `test_service/` 提供简易演示（`python -m http.server 18765`）。可用于验证端口探测与启停流程。

### 功能一览

| 功能 | 说明 |
|------|------|
| 端口探测 | 由监听端口定位 PID、CWD、命令行，并推断服务根目录 |
| 脚本扫描 | 识别 `start` / `run` / `启动` 等 `.bat` `.cmd` `.ps1` |
| 状态轮询 | 约每 5 秒刷新端口与进程信息 |
| 进程停止 | 基于端口结束监听进程及其子进程 |
| 图标缓存 | 在线时抓取 WebUI favicon，缓存到 `data/icons/` |
| 主题 | 白天模式 / 夜间模式，localStorage 记忆 |

### 技术原理

#### 整体架构

```
浏览器 (frontend)  ──HTTP JSON──►  FastAPI (backend)
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
              services.json      脚本/进程执行      端口与进程发现
              图标缓存           (subprocess)       (psutil / socket)
```

- **前端**：原生 HTML / CSS / JS 单页，无构建步骤  
- **后端**：FastAPI + Uvicorn，托管 API 与静态页面  
- **配置**：`data/services.json` 持久化服务列表  
- **启动器**：`start.bat` / `stop.bat` 调用 `python -m backend.launcher`，避免 bat 中复杂引号/括号解析问题  

#### 运行状态如何判定

不以「是否存在 bat 进程」为准（bat 往往启动后立刻退出），而以：

1. **TCP 端口是否可连接**（`127.0.0.1:port`）  
2. 使用 **psutil** 查找该端口的 `LISTEN` 连接，读取 **PID / 进程名**

因此状态与「WebUI 是否真正可访问」更一致。

#### 端口探测如何工作

当用户提供端口时：

1. `psutil.net_connections` 找到监听该端口的 PID  
2. 读取进程的 `cwd`、`exe`、`cmdline`  
3. 从路径与命令行中提取候选目录  
4. 在候选目录中扫描启停脚本，按名称启发式打分（优先 `start.bat` 等）  
5. 返回建议目录、启动脚本、服务名称  

#### 启动与停止

| 动作 | 实现要点 |
|------|----------|
| 启动 | 在服务目录下执行登记的 `.bat` / `.cmd` / `.ps1`；可选 `CREATE_NEW_CONSOLE` 或 `CREATE_NO_WINDOW` |
| 停止 | 查找端口监听 PID，终止该进程树（`terminate` → 必要时 `kill`），无需依赖 stop 脚本 |
| 停止脚本 | 仍可作为可选兜底，默认不使用 |

#### 安全边界

- 默认仅绑定 **127.0.0.1**，不对外网开放  
- 脚本路径必须落在已登记的服务目录内，禁止路径穿越  
- 不执行任意 shell 字符串，只执行已配置的脚本文件  
- 删除服务不会删除用户业务目录  

#### 主要依赖

| 包 | 用途 |
|----|------|
| FastAPI / Uvicorn | HTTP API 与静态资源 |
| psutil | 端口 → 进程、进程树结束 |
| httpx | 拉取 favicon |
| pydantic | 请求体校验 |

#### 目录结构

```
Home Page/
├── start.bat / stop.bat     # 一键启停管理器
├── requirements.txt
├── README.md
├── backend/
│   ├── main.py              # 路由与静态托管
│   ├── services.py          # 业务：CRUD / 启停
│   ├── process_info.py      # 端口探测与杀进程
│   ├── process_runner.py    # 执行启动脚本
│   ├── scanner.py           # 目录脚本扫描
│   ├── folder_picker.py     # 系统文件夹选择
│   ├── icon_fetcher.py      # 图标抓取
│   ├── launcher.py          # bat 调用的启动/停止逻辑
│   └── ...
├── frontend/
│   ├── index.html
│   ├── css/style.css        # 白天 / 夜间主题
│   └── js/app.js
├── data/                    # 运行时数据（默认不入库）
│   ├── services.json
│   └── icons/
└── test_service/            # 可选演示服务
```

### 许可与说明

个人本地工具。上传为私有仓库时，请注意不要把含敏感路径的 `data/services.json` 提交到公开环境。

---

<a id="english"></a>

## English

### Introduction

**Local Services Home** is a Windows-oriented dashboard for managing local WebUI services bound to `127.0.0.1`.

During development and operations you often run many local tools (DB consoles, AI WebUIs, monitors, small apps). Ports and start scripts differ; juggling them is painful.

This project gives you one page to:

- Register services (port, directory, start script, notes)
- **Start / start hidden / stop** from the browser
- See **status, process name, and PID**
- **Discover** directory and start script from a listening port
- Use each service’s **favicon** as its card icon
- Switch between **light and dark** themes

The manager listens only on localhost: **http://127.0.0.1:18888**.

### Requirements

| Item | Requirement |
|------|-------------|
| OS | Windows (10/11 recommended) |
| Runtime | Python 3.10+ |
| Privileges | Normal user; protected processes may need elevation |

### Usage

#### 1. Start / stop the manager

| Action | How |
|--------|-----|
| Start | Double-click `start.bat` (installs deps on first run, opens browser) |
| Stop | Double-click `stop.bat` |

Or manually:

```bat
python -m pip install -r requirements.txt
python -m backend.launcher start
python -m backend.launcher stop
```

URL: **http://127.0.0.1:18888**

#### 2. Add a service (recommended: port discovery)

1. **Start the target service once** so its port is listening  
2. Click **Add service**  
3. Enter the **port**, click **Discover from port** (or press Enter in the port field)  
4. The app infers process, working directory, start script, and a suggested name  
5. Save  

If the service is not running, use **Choose folder** and **Scan scripts** instead.

#### 3. Day-to-day controls

| Action | Behavior |
|--------|----------|
| Open | Open the WebUI in a new tab |
| Start | Run the start script (console visible) |
| Hidden | Run the start script without a console window |
| Stop | **Kill the process listening on the port** (no stop script required) |
| Edit | Pencil icon on the card’s top-right |
| Delete | Removes config only; never deletes the service folder |

Theme (**Day / Night**) is remembered in `localStorage`.

#### 4. Demo service (optional)

`test_service/` runs a simple `python -m http.server 18765` for trying discovery and start/stop.

### Features

| Feature | Description |
|---------|-------------|
| Port discovery | Map port → PID / CWD / cmdline → service root + scripts |
| Script scan | Heuristics for `start` / `run` / Chinese names, `.bat` `.cmd` `.ps1` |
| Status polling | Refresh port & process info about every 5s |
| Process stop | Terminate the listener process tree by port |
| Icon cache | Fetch WebUI favicon into `data/icons/` |
| Themes | Light and dark CSS schemes |

### How it works (technical)

#### Architecture

```
Browser (frontend)  ──HTTP JSON──►  FastAPI (backend)
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
              services.json      run scripts         port/process
              icon cache         (subprocess)        (psutil / socket)
```

- **Frontend**: plain HTML/CSS/JS SPA (no bundler)  
- **Backend**: FastAPI + Uvicorn for API + static files  
- **Config**: `data/services.json`  
- **Launcher**: `start.bat` / `stop.bat` call `python -m backend.launcher` to avoid fragile cmd parsing  

#### Status model

Bat processes often exit immediately after spawning the real server. Status is therefore based on:

1. TCP reachability of `127.0.0.1:port`  
2. **psutil** lookup of `LISTEN` sockets for PID / process name  

#### Port discovery pipeline

1. Find PIDs listening on the port  
2. Read `cwd`, `exe`, `cmdline`  
3. Collect candidate directories from paths in those fields  
4. Scan for start/stop scripts and score matches  
5. Return suggested directory, start script, and name  

#### Start / stop

| Action | Mechanism |
|--------|-----------|
| Start | Execute registered script under the service directory; visible or hidden window flags on Windows |
| Stop | Find listener PID(s), terminate process tree (`terminate` then `kill` if needed) |
| Stop script | Optional fallback; not required by default |

#### Security notes

- Bound to **127.0.0.1** only by default  
- Scripts must resolve inside the registered service directory  
- No free-form shell execution  
- Deleting a service never deletes the user’s project folder  

#### Dependencies

| Package | Role |
|---------|------|
| FastAPI / Uvicorn | HTTP API & static hosting |
| psutil | Port → process; kill process tree |
| httpx | Favicon download |
| pydantic | Request validation |

### License / notes

Intended as a personal local tool. Keep `data/services.json` out of public remotes if it contains sensitive paths.
