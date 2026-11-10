# M2 BLE Write Request 正式轮

时间：2026-11-10 19:55–20:10。配置：40 Hz、40 B、window=1、timeout=50 ms、BLE interval unit=6。

## 准入证据

- 两端 selftest：`pass=32 fail=0`；
- client/server 首次注册返回 `0x80006001`，500 ms 后第2次成功；
- 扫描、连接、MTU 23→247、特征及 CCCD 发现和 CCCD 写确认均完成；
- 发现的句柄为 write=0x13、notify=0x10、CCCD=0x11；
- 参数更新回调给出的实际 interval=6、latency=0；
- 只有上述步骤完成后才进入 measurement-ready；
- server 全程 `req=rsp`、`bad=0`，无断链。

## 结果

最终完整汇总使用 `n=34,400`：

```text
p50=14.118 ms  p90=18.362 ms  p99=20.453 ms  p999=25.790 ms
max=40.324 ms  mean=14.569 ms  jitter=3.429 ms
sent=34401 recv=34400 timeout=1 duplicate=1 bad=0 drop=0 loss_ppm=29
```

与其余三组统一使用28,800个有效样本时：

```text
p50=14.117 ms  p90=18.361 ms  p99=20.466 ms  p999=25.987 ms
max=40.324 ms  mean=14.573 ms  jitter=3.435 ms
sent=28801 recv=28800 timeout=1 duplicate=1 bad=0 drop=0 loss_ppm=34
```

唯一一次 timeout 及其迟到 duplicate 在约 `n=2,200` 时出现，之后直到 `n=34,400` 两个计数均未继续增长，
因此记录为单次孤立事件，不描述为持续丢包。服务端 RX handle 过滤已生效，CCCD 控制写没有再污染 `bad` 计数。

## Write Request 确认辅助统计

日志中可解析173个确认统计窗口，合计34,460次确认；窗口加权平均约14,476.3 us，最大38,824 us。
启动阶段前三次确认延迟分别为16,381、13,756、11,382 us。该确认属于辅助观测，不能再与应用层 REQ/RSP RTT
相加，否则会重复计算时间；两份日志停止时刻略有差异，因此确认总数不要求与最后一个完整 SUM 的 n 完全相等。

## 结果边界

本轮 BLE Request 的应用 RTT 没有表现出比 BLE Command 更慢的中位数。它说明当前 SDK 实现中，写确认并未
在应用层通知回包之前形成一个可直接相加的串行 RTT；不能据此声称 BLE Request 在协议层天然快于 Command。

## 文件哈希

```text
8783543ad96cc5148edf1632730add334cebcbbe2a065a644e40c48495b527a  client_COM3.txt
103e33f53d0909d9363056f5d49c516ad34b4ab4b4249adf3040b14009ad6222  server_COM4.txt
```
