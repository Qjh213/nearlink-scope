# NearLink LinkScope M5.0.9-rc5（M5）

这是 M5 阶段的桌面分发版本，用 `M5.0.9-rc5` 源码重新打包，替代 M3 时代的 `v0.3.6`。

## 这一版和 v0.3.6 的差别

- **接收实现重写**：按 QCOM 行为规格复刻——读线程只搬字节、按 CR 切行、空闲 300 ms 冲刷半行、
  `read(256)` 不再回 `readline()`、丢弃必须可计数（`forced_flushes` / `dropped_bytes`）。
  v0.3.6 的 exe 是 2026-11-13 构建，早于这次改造，行为与源码不一致；本版消除该差异。
- **版本号单一来源**：`tools/nlscope.py` 的 `APP_VERSION` 现在同时决定窗口标题、`run.json`
  的 `app_version`、安装包版本号和产物文件名，不再出现"程序里 M5.0.9-rc5、安装包叫 0.3.6"。
- 其余界面与指标口径不变，与 M5 各轮证据所用版本一致。

## 下载选择

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `NearLink-LinkScope-M5.0.9-rc5-Setup.exe` | 11,415,769 B | `97091fac9f4f45c3fae3ffb587f9affc88b37343541dc423e7f9bb3bba288f67` |
| `NearLink-LinkScope-M5.0.9-rc5-Portable.zip` | 13,685,336 B | `6789266d572010b4a627d04eb64040f6cac2c0274e49d68320a609aba6100906` |

免安装版解压后运行 `LinkScope\LinkScope.exe`（内部 `LinkScope.exe` 的 SHA-256 为
`df260267d9ae1fbf93b7a9b3eac4cdb33675d7bd823e4f745bad454dcd969608`）。

安装包为当前用户安装，不要求管理员权限；目标电脑不需要 Python，但仍需安装开发板 CH340 驱动。

## 验收记录

- `dist\LinkScope\LinkScope.exe` 的文件资源版本为 `M5.0.9-rc5`（四段数字 `5.0.9.5`）；
- 用打包后的 exe 回放 `artifacts/logs/M5/cfg522_rate25/run1/initiator_COM3.log`：
  `n=4508 p50=14778us p99=17868us max=21819us`，与当时 `run.json` 逐项一致；
- GUI 启动正常，窗口标题为 `NearLink LinkScope · M5.0.9-rc5`。

## 已知限制

- 未做商业代码签名，Windows SmartScreen 可能提示"未知发布者"，请用上表 SHA-256 校验；
- M5 的距离组（cfg531/532）本轮未做，本版不改变这一点。

## 对应提交

源码基线 `M5.0.9-rc5`，重打包脚本与版本单一来源改造见 `tools/packaging/`（P160）。
