# M5 cfg512 Payload 100B readyfix 冒烟结论

**结论：通过，可作为修复后的正式测试基线。**

- 配置：SLE Write Command、cfg512、Payload 100B、40Hz、window=1、timeout=50ms、interval=24；
- 两端日志均从本次独立`boot.`开始，各连接1次且无断连；
- 顺序为“service ready → 请求interval=24 → 参数更新成功 → measurement ready → 首次发包”；
- 首个正式样本为seq=1，2705条样本至seq=2705严格连续，无缺口；
- PC精确统计：p50=23.456ms、p90=24.545ms、p99=27.475ms、p99.9=33.006ms、max=34.962ms；
- timeout/duplicate/out-of-order/bad/drop全部为0；
- COM4共142条心跳，全部满足req=rsp、bad=0、lasterr=0，末条为2703/2703。

这证明原先15～16次timeout/duplicate来自目标连接参数生效前就启动测量，并非稳定阶段链路故障。
本轮周期SUM偶尔出现非零`loss_ppm`，原因是旧公式`(sent-recv)/sent`把打印瞬间的1个在途请求当成损失；
该统计口径已在后续源码中修正为`timeout/(recv+timeout)`，需要重新构建后才会体现在固件日志中。
