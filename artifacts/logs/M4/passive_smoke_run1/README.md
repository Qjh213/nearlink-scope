# M4 SLE被动监测冒烟第一轮

时间：2026-11-11。COM3运行 `linkscope-sle-monitor-m4.fwpkg`，COM4运行
`linkscope-sle-responder-write_dual-40b.fwpkg` 并持续announce。monitor只seek，不连接。

## 固件身份与配置

```text
COM3: role=monitor
link=sle-passive-seek
seek_intv=100
seek_win=100
filter_dup=0
selftest=PASS

COM4: role=responder
announce interval min=max=0xc8
announce payload: 010201020200
seek response: 0c020a0a0b6c696e6b73636f7065  # 含 linkscope
```

## 完整会话统计

QCOM打开时先收到旧会话seq 1549起的尾部；18:05:46复位COM3后形成可完整核验的新会话：

- seq 1→3,134，3,134条全部连续，无缺口；
- 板端时间跨度51.902643 s；
- 报告速率60.363 reports/s；
- 唯一 `addr_id=867ec3df`；
- event 3：1,570条，固定len=6、payload=`010201020200`；
- event 11：1,564条，固定len=14、payload=`0c020a0a0b6c696e6b73636f7065`；
- data_status全部为0；
- RSSI min/p10/p50/p90/max = -50/-48/-43/-41/-39 dBm；
- event 3报告间隔 p50/p90/p99/max = 26.241/50.690/86.264/150.169 ms；
- 所有可见 `# MON` 均为 `drop=0 invalid_rssi=0`；
- COM4全程无Connected，`req=0 bad=0 rsp=0`，证明monitor没有发起连接。

这里的“报告间隔”是monitor收到seek callback的到达间隔，不等同于advertiser真实空口广播间隔，也不能据此
计算丢包率。event 3和11是同一设备的两类上报，不能直接合并为一个“广播包”。

## 文件哈希

```text
edd72873b28bb4ce9a0752c3514ab39ef536d333e3fe7270f6cd0c9310e77050  monitor_COM3.txt
da1b3f0c2fa08cc930e1b43ad88cebb7a925bba654df5400d0293d9275f2410b  advertiser_COM4.txt
```

同目录 `monitor_COM3.log/.csv/.json/.md` 是M4 PC工具使用完整原始文件回放生成的四类验收导出。
它们包含文件中的旧会话尾部和复位后的完整会话，因此合计3,489条、reset=1；正式实验数字仍以上述
复位后seq 1→3,134的完整会话为准。
