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
dist\release\NearLink-LinkScope-0.3.6-Setup.exe
dist\release\NearLink-LinkScope-0.3.6-Portable.zip
dist\release\SHA256SUMS.txt
```

安装包为当前用户安装，不要求管理员权限。目标电脑不需要 Python，但仍需正确安装开发板 CH340 驱动。
程序未做商业代码签名，Windows SmartScreen 可能显示“未知发布者”；发布时必须同时提供 SHA-256。

## 发布前验收

1. 对照 `SHA256SUMS.txt` 检查安装包和免安装包；
2. 在一台未安装 Python 的 Windows 10/11 x64 电脑上安装并启动；
3. 确认串口下拉列表能识别 CH340 设备，并完成一次开始、停止和导出；
4. 从“应用和功能”卸载，确认程序目录和快捷方式被清理。
