# M2 BLE Write Command 正式轮

时间：2026-11-10 14:23–14:41。配置：40 Hz、40 B、window=1、timeout=50 ms、BLE interval unit=6。

## 准入证据

- 两端 selftest：`pass=32 fail=0`；
- client 注册第1次返回 `0x80006001`，500 ms后成功，ID=1；
- server 同样在第2次注册成功，ID=1；
- 扫描 RSSI -58 dBm，连接后 RSSI -47 dBm；
- MTU 从23更新到247；
- 发现 write=0x13、notify=0x10、CCCD=0x11；
- CCCD 写确认完成后才进入 transport-ready；
- 参数更新成功，实际 interval=6、latency=0；
- 之后才出现 measurement-ready 和正式探针；
- 全程无断链。

## 结果

最终完整汇总使用 `n=43,600`：

```text
p50=14.906 ms  p90=18.012 ms  p99=23.175 ms  p999=30.116 ms
max=45.623 ms  mean=14.883 ms  jitter=3.517 ms
sent=43600 recv=43600 timeout=0 duplicate=0 bad=0 drop=0 loss_ppm=0
```

与 M1 相同的28,800有效样本口径：

```text
p50=14.913 ms  p90=18.041 ms  p99=23.444 ms  mean=14.919 ms  jitter=3.560 ms
sent=28800 recv=28800 timeout=0 duplicate=0 bad=0 drop=0
```

## 与 SLE Write Command 的等样本比较

| 指标 | SLE Command | BLE Command | BLE-SLE |
|---|---:|---:|---:|
| p50 | 14.873 ms | 14.913 ms | +0.040 ms |
| p90 | 16.128 ms | 18.041 ms | +1.913 ms |
| p99 | 18.403 ms | 23.444 ms | +5.041 ms |
| mean | 14.830 ms | 14.919 ms | +0.089 ms |
| jitter | 1.471 ms | 3.560 ms | +2.089 ms |

该表只代表当前板卡、SDK、环境与各自已验收连接配置。SLE `intv=24` 与 BLE `intv=6` 的原始单位
不同，当前公开头文件不足以证明二者物理连接间隔严格相等，因此不能把差值外推成协议通用性能。

## server `bad=1` 说明

`bad=1` 在首个探针前由 CCCD 控制写入触发，此后43,000余个探针期间始终不增长。client 的探针统计
始终 `bad=0`。已在后续源码中增加 `req->handle == g_rx_handle` 过滤，使控制面写入不再进入探针计数。

## 文件哈希

```text
c82768076d459a8101d22f885e63d62de66788773f8b161492f116d972e226e6  client_COM3.txt
0b49938c0403b32f81001143423822b52ad3cf8b92e7defb0eb8f1d1145d385d  server_COM4.txt
```
