# NearLink LinkScope M6.0.1

补丁版，只修"命令行模式在特殊输出环境下的崩溃"，不改任何统计口径、不改固件、不改证据格式。

## 相对 M6.0.0 的改动

1. **stdout 强制 UTF-8**：Windows 上把输出重定向到文件/管道时默认编码是 `cp1252`，
   `--replay`、`--reproduce`、`--import-csv` 打印中文摘要会抛 `UnicodeEncodeError`，
   一次正常的导出会变成失败（CI 上先暴露：97 项里 2 项 error）。
2. **窗口程序里的空标准输出自动补位**：安装包是 `console=False`，PyInstaller 把
   `sys.stdout`/`sys.stderr` 置为 `None`，CLI 里一句 `print` 就是 `AttributeError`。
   现在换成空写入器，命令行模式仍能写文件并按退出码报告结果（只是没有屏幕输出）。
3. CI 与主机侧测试脚本修了两处"只在全新克隆/CI 上才出现"的问题
   （`.tools` 目录不存在时 `Resolve-Path` 直接抛错；单命令编译器被 PowerShell 展开成首字母）。

不影响：GUI、串口接收、统计、证据导出、固件、协议（仍 v0.3）。

## 实机验证

| 项 | 结果 |
|---|---|
| 单元测试 | 116 项（`tests/` 15 + `tools/test_nlscope.py` 101） |
| 固件主机侧测试 | `[RESULT] engine tests PASS`（zig cc） |
| CI | GitHub Actions `windows-latest` 通过 |
| M6.0.0 的实机证据 | 仍然有效：30 分钟 72,068 样本、104.7 分钟 251,343 样本、断链 `638 = 638` |

## 资产

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| `NearLink-LinkScope-M6.0.1-Setup.exe` | 11,465,688 | `287b3e45cab6071870fafd223eb3bf36297d21631fded9b412163435b230217e` |
| `NearLink-LinkScope-M6.0.1-Portable.zip` | 13,738,638 | `efb4855bb17613e8be0f32d95ccb84ab7fc29a6f8b5fcb1c8a5d78b1e978c38d` |

## 已知限制

- **未在干净机（无 Python 的 Windows）上安装验证**：按用户决定跳过该项验收。
- 打包成窗口程序后命令行模式没有屏幕输出（可写文件、看退出码）；这是 `console=False` 的代价。
- `qdrop`/`stale` 尚未实测到非零（缺"断链但保持供电"的 RF 屏蔽条件）。
- 应用层 CRC 不做；协议停在 v0.3。
- 超过 4 小时的连续采集未验证（内存斜率约 1.3 MB/min）。
- 40 Hz 下 `seq` 27.3 分钟回绕一次，计入 `pc_sequence.wraps`，不是丢包。
