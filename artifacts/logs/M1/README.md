# M1 SLE 写语义 A/B 结论

## 可比性

两轮使用同一板卡、同一 responder 固件、40 Hz、40 B、window=1、timeout=50 ms、连接参数档位24。
两端都无断链、坏帧或稳态新增超时。RSSI 分别为 -39 dBm（Command）与 -36 dBm（Request）。

为避免样本数不同影响累计分布，主表统一取两轮 `n=28800` 的 SUM：

| 指标 | Write Command | Write Request | Request - Command | 相对变化 |
|---|---:|---:|---:|---:|
| RTT p50 | 14.873 ms | 23.362 ms | +8.489 ms | +57.08% |
| RTT p90 | 16.128 ms | 24.536 ms | +8.408 ms | +52.13% |
| RTT p99 | 18.403 ms | 26.478 ms | +8.075 ms | +43.88% |
| RTT mean | 14.830 ms | 23.640 ms | +8.810 ms | +59.41% |
| jitter | 1.471 ms | 1.263 ms | -0.208 ms | -14.14% |
| max | 36.160 ms | 38.471 ms | +2.311 ms | +6.39% |
| B端处理均值 | 10 µs | 11 µs | +1 µs | — |

Write Request 的稳态写确认时延加权平均为15.873 ms；Command 没有写确认。确认时延与应用层 RTT
是不同观测量，不能把二者直接相加或把8.489 ms差值称为“确认包的空口时延”。现阶段能严谨得出的结论是：

> 在本项目这套 H3863 SDK、当前板卡和配置下，把 SLE 上行从无响应 Write Command 改为有确认
> Write Request，会改变端到端调度路径，使应用层 RTT 中位数增加约8.49 ms。

该结论是版本与配置相关的实测结果，不外推为 SLE 标准的固定性能数字。

## 证据入口

- `write_cmd/`：最终 Command 原始日志和摘要；
- `write_req/`：P38 修复后的最终 Request 原始日志和摘要；
- `write_req_run1/`：受重复 CFM 日志扰动的预实验，仅供问题复现。
