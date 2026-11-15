# NearLink LinkScope M6.0.0-rc1（M6 第一批，预发行）

这是 M6 阶段的第一批改进，**预发行版**：功能与证据链已通过实机验证，但还没有走完干净机
（无 Python 的 Windows）验收，所以先标 `Pre-release`，不替换 `vM5.0.9-rc5` 的 Latest 位置。

## 这一版和 M5.0.9-rc5 的差别

### 1. 统计不再"假零"

- 板端 `SUM` 新增 `ooo` / `rx_drop` / `send_err` / `cfm_err` / `dlt_neg` 五个计数；
  M5 里 `ooo` 永远是 0，因为它根本没被上报过。
- `SUM` 缺失时，PC 侧对应指标显示 `—`（未知），不再补 0。
- `run.json` 记录 `sum_frames_seen`、`parse_failures`、`csv_rows_total/skipped`、
  `pc_sequence{gaps,duplicates,out_of_order,wraps}`、`board_restarts`、`board_sample_span_s`。
- 分位数改为线性插值，板端直方图与 PC 精确值的差异写进协议文档（p99 可差 1–3%）。

### 2. 证据不再会悄悄被覆盖或丢掉

- 导出拒绝覆盖：同名文件已存在就报错，不再静默替换；
- 除渲染后的 `.log` 外，同时保存原始字节 `.raw`（接收侧边采边写，进程崩溃也能留证）；
- 导出后自动自检九项（行数、序号、解析失败、签名、SUM、双端固件、代码身份、原始字节），
  结果写进 `run.json.evidence_checks` 并在弹窗/CLI 打印；`fail` 只用于证据自相矛盾。
- 导出的 `.log` 修复了"一半行只有时间戳没有正文"的问题（CRLF 残留 LF 导致）。
- 采样中板卡重启会被识别（样本序号回退），自检提示"样本来自 N+1 次上电，不可当作单轮"。

### 3. 通用实验规格（供第三方接入）

- `tools/experiment_specs.json`：规格名、模式（`raw` / `auto` / `measure`）、期望平台签名；
- GUI 可"导入规格"，也可勾掉"等待 boot. 边界"；CLI 支持 `--no-boot-gate`；
- `run.json` 写入实际使用的串口名、实验规格、双端固件 SHA-256、git HEAD 与 dirty 标记。

### 4. 固件修正

- responder 队列 5→16，队满计 `qdrop`、断链后旧响应计 `stale`（不再把"来不及"记成 `bad`）；
- initiator 逐字节校验 REQ 填充模式（`PROBE_ERR_PAYLOAD = -5`），校验 RSP 回显的 `cfg_id`；
- Write Command 模式下不再错误打印 `cfm_n=0`（该计数器只在 Write Request 下有意义）。

## 兼容性

- **CSV 删除了 `dlt_us` 列**（表头变为 `seq,t1_us,t4_us,rtt_us,bproc_us,wall_time`）。
  `dlt` 是两个设备时钟的差，不是时延，M5 的全部历史数据里它恒为 0，留着只会被误读。
  旧 CSV 仍可回放，缺失列读作未知。
- 协议升到 v0.3（见 `docs/protocol.md`）；旧固件与新 PC 混用会缺字段，建议两端一起升级。

## 下载选择

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `NearLink-LinkScope-M6.0.0-rc1-Setup.exe` | 11,437,461 B | `ee82e4b92f99ab90ff899288439ed4e2608af3765defd05a625365cb79689ae2` |
| `NearLink-LinkScope-M6.0.0-rc1-Portable.zip` | 13,708,342 B | `97174f23904ab4d15e9d84855993dc188dddfcc75c9958246a7de0be95aa2db9` |

免安装版解压后运行 `LinkScope\LinkScope.exe`。安装包为当前用户安装，不要求管理员权限；
目标电脑不需要 Python，但仍需安装开发板 CH340 驱动。

## 已完成的实机验证

| 项目 | 结果 |
|---|---|
| 双板冒烟（cfg500 / 40 B / 40 Hz） | 3,803 样本，p99 17.112 ms，`to/dup/ooo/bad/drop` 全 0，自检 PASS |
| 真实断链（拔 responder 电源） | 板端 `to=638` 与 PC `gaps=638` 互证；重连后无样本丢失 |
| 30 分钟长跑 | 72,068 样本，40 Hz 稳定，`wraps=1`（uint16 回绕被正确吸收，不产生 gap），`loss_ppm=0` |
| 双端固件归档 | initiator `79e0c884…9955d`、responder `f6c1a49b…c96ba7` |
| 自动化测试 | 15 + 66 = 81 项，含固件 host test 与 SDK 语法检查 |

## 已知限制

- **未做干净机验收**（清单在 `docs/M6-干净机验收清单.md`），所以是预发行；
- 未做商业代码签名，SmartScreen 可能提示"未知发布者"，请用上表 SHA-256 校验；
- `qdrop` / `stale` 两条路径只做了代码审查与单元测试，还没有"保持双板供电的 RF 阻断"实测；
- 30 分钟长跑内存约 +1.1 MB/min（三份常驻：样本、`raw_lines`、UI 行），
  按此推算 8 小时约 500–600 MB，超长采集需要后续改为溢写或环形缓冲；
- 自动重连不做：串口掉线（CH340 重枚举）后需重新"开始串口"，这是有意的（避免把两次
  物理会话混成一份证据）。

## 对应提交

源码基线 `83a1f48`（三批：`478abca` 固件与协议、`4af331f` PC 核心、`83a1f48` 文档），
标签 `vM6.0.0-rc1`。
