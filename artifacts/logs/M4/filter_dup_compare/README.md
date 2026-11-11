# M4 SLE重复过滤对照

时间：2026-11-11。COM4保持同一SLE advertiser；COM3更换为
`linkscope-sle-monitor-filter-dup1-m4.fwpkg`，启动签名确认 `filter_dup=1`。

## 同口径结果

两轮均截取最后一次 `seq=1` 起始的完整会话，报告间隔按同地址ID、同event分别统计。

| 指标 | filter_dup=0基线 | filter_dup=1 |
|---|---:|---:|
| 会话时长（板端） | 51.902643 s | 69.631392 s |
| ADV报告数 | 3,134 | 4,260 |
| 报告速率 | 60.382/s | 61.179/s |
| event 3 | 1,570 | 2,135 |
| event 11 | 1,564 | 2,125 |
| RSSI min/p10/p50/p90/max | -50/-48/-43/-41/-39 | -65/-56/-46/-41/-39 |
| gap/dup/ooo/reset | 0/0/0/0 | 0/0/0/0 |
| queue drop / invalid RSSI | 0/0 | 0/0 |

开启过滤后的报告速率没有下降，event 3与event 11仍近似1:1持续上报。两轮并非同时采集且RSSI环境不同，
1.3%的速率差异不能解释为过滤收益或性能退化。当前仅能确认：在本板、本SDK与这组announce/seek response
数据下，`filter_duplicates=1`没有产生可观察的重复抑制。SDK如何定义重复、是否将RSSI或event纳入判定、
以及该版本是否完整实现过滤仍未验证。

## 文件

- `monitor_filter_dup1_COM3.txt`、`advertiser_filter_dup1_COM4.txt`：QCOM原始日志；
- `monitor_filter_dup1_COM3.log/.csv/.json/.md`：LinkScope四类回放导出；
- filter_dup=0基线：`../passive_smoke_run1/monitor_COM3.txt`。

```text
002233037efbef7b23d434a55c89a3dd8002022ecb6cfbc8a63506b4a86b1105  monitor_filter_dup1_COM3.txt
3e913783cd7607c4cf3e9221a6ffade2f27392dc2c4f208187e507157b739feb  advertiser_filter_dup1_COM4.txt
```
