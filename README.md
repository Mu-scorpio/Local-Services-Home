# 本地服务管理

Windows 本机 WebUI 服务控制台：统一登记端口、目录与启动脚本，一键启停，系统托盘常驻。

## 使用（单文件）

1. 运行 `build.bat` 打包  
2. 得到 **`dist\本地服务管理.exe`**（仅此一个文件）  
3. 复制到任意位置双击运行  

- 托盘图标 → 快捷面板 / 打开主窗口 / 退出  
- 用户数据：`%LOCALAPPDATA%\Local Services Home\`  
- 管理器监听：`http://127.0.0.1:18888`

## 开发

```bat
pip install -r requirements.txt
python -m backend.desktop
```

## 目录

```
backend/     # Python 后端 + 桌面入口
frontend/    # 主窗口 / 快捷面板 UI
build.spec   # PyInstaller 单文件配置
build.bat    # 一键打包
```

## 功能

- 端口探测：从监听端口推断目录与启动脚本  
- 启动 / 无窗口启动 / 按端口停止  
- 状态轮询（窗口隐藏时自动降频）  
- 白天 / 夜间主题  
- Favicon 图标缓存  

## 依赖

| 包 | 用途 |
|----|------|
| FastAPI / Uvicorn | 本机 HTTP API |
| psutil | 端口与进程 |
| httpx | 拉取图标 |
| pywebview / pystray / Pillow | 桌面窗口与托盘 |
