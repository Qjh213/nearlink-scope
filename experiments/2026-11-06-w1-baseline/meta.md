# W1 正式基线 · 2026-11-06 19:52

**这是 W1 的结项数据**，也是 W2 上位机（`tools/nlscope.py`）的 golden 数据集：
波形工具画出来的图，必须和这里的数字对得上。

## 配置（可复现）

| 项 | 值 |
|---|---|
| 固件 | `linkscope` @ P27 修复后（`sle_poll` 就绪即谈参数 + 1 s 节拍） |
| 角色 | A = `initiator`，B = `responder` |
| `cfg_id` | 0 |
| 包长 | 40 B |
| 探测速率 | 40 Hz（停等，单包在途） |
| 连接间隔 | 固定 3 ms（`INTV_SLOT=24`） |
| 超时 | 50 ms |
| 距离/环境 | 两板相距 ~10 cm，无遮挡，桌面 |

复现命令：

```powershell
cd E:\nearlink-scope\firmware\scripts
.\sync-to-sdk.ps1 -Role initiator -Hz 40 -TimeoutMs 50 -IntvSlot 24
# VM: python3 build.py -c ws63-liteos-app → BurnTool 烧 A
```

## 平台签名

```ini
chip        = ws63 / hi3863
sdk         = bearpi-pico_h3863-master
tick_us     = 40
min_interval= 3ms
events_rtt  = 8
max_pps     = 41.7
noise_floor = 6us (P50) / 8us (P99)   # loopback
sched_tick  = 20ms
```

## 结果

| 项 | 值 |
|---|---|
| 时长 / 样本 | 309.2 s / 12 369（稳态 12 170） |
| 采样率 | 40.0 次/秒 |
| 预热 | 0.7 s / 17 包 |
| p50 / p90 / p99 / p999 | 23.91 / 24.66 / 27.05 / 33.61 ms |
| max | 41.64 ms |
| stdev | 1.02 ms |
| 稳态丢包 | 0 |
| `bproc` | 11 µs 平均 / 206 µs 最大 |

三段（各 ~103 s）p50：23.907 / 23.909 / 23.907 ms —— 相差 2 µs。

## 判读

```
python tools\analyze_log.py experiments\2026-11-06-w1-baseline\raw\A-initiator-40hz-3ms.txt
```

预期输出：`验收: PASS`，`稳态新丢包: 0`，p50 = 23908 µs。

## 备注

- `raw/` 里是 QCOM 原样落盘的日志（约 2.4 MB，174k 行），未做任何裁剪；
- 前 0.7 s 的 17 个超时是"参数生效前的空窗"，不是链路问题（见 `docs/项目日志.md` P27）；
- 更早那轮 700 s 的数据被 QCOM 覆盖了，其结论保留在 `docs/W1-平台能力确认.md` §5.4 的对比表里。
