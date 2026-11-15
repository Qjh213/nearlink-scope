# Windows 打包与安装器

## 构建环境

- Windows x64；
- 项目 `.venv`；
- `tools/requirements.txt` 中的运行依赖；
- `tools/requirements-build.txt` 中的 PyInstaller；
- Inno Setup 6。

安装构建依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\tools\requirements.txt -r .\tools\requirements-build.txt
winget install --id JRSoftware.InnoSetup --exact
```

构建：

```powershell
.\tools\packaging\build-installer.ps1
```

输出：

```text
dist\release\NearLink-LinkScope-<APP_VERSION>-Setup.exe
dist\release\NearLink-LinkScope-<APP_VERSION>-Portable.zip
dist\release\SHA256SUMS.txt
```

`<APP_VERSION>` 直接取自 `tools/nlscope.py` 的 `APP_VERSION`（当前为 `M5.0.9-rc5`）。
Windows 文件资源里的四段版本号由脚本自动换算：`M5.0.9-rc5` → `5.0.9.5`。
构建时会在 `build\pyinstaller\version_info.txt` 生成资源版本文件，不写回仓库；
`tools\packaging\version_info.txt` 只是绕过脚本直接跑 PyInstaller 时的兜底。

改版本号只需要改 `tools/nlscope.py` 一处，不要再去改 `.iss` 或 `.spec`。

安装包为当前用户安装，不要求管理员权限。目标电脑不需要 Python，但仍需正确安装开发板 CH340 驱动。
程序未做商业代码签名，Windows SmartScreen 可能显示“未知发布者”；发布时必须同时提供 SHA-256。

## 发布前验收

1. 对照 `SHA256SUMS.txt` 检查安装包和免安装包；
2. 在一台未安装 Python 的 Windows 10/11 x64 电脑上安装并启动；
3. 确认串口下拉列表能识别 CH340 设备，并完成一次开始、停止和导出；
4. 从“应用和功能”卸载，确认程序目录和快捷方式被清理。
