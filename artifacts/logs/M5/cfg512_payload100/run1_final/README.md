# M5 cfg512 Payload 100B 最终正式 run1

**结论：正式通过。**

- 配置：SLE Write Command、cfg512、Payload 100B、40Hz、window=1、timeout=50ms、interval=24；
- 采集473.335秒，有效样本18,934条，seq=1至18,934严格连续；
- PC精确统计：p50=23.951ms、p90=25.014ms、p99=27.855ms、p99.9=34.023ms、max=43.033ms；
- timeout/duplicate/out-of-order/bad/drop全部为0；
- 94条板端周期SUM的`loss_ppm`全部为0，包括打印时`sent=recv+1`且有1包在途的时刻；
- 两端各一次boot、一次连接、无断连；
- COM4共953条心跳，全部req=rsp、bad=0、lasterr=0，末条为18,926/18,926。

该结果同时验证了两个修复：目标连接参数生效前不启动正式测量；统计快照中的在途请求不再被误报为损失。
仍需独立复位执行run2，验证分布和长尾的可重复性。
