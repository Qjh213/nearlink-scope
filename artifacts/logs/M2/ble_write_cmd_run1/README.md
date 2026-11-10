# M2 BLE Write Command run1（注册失败，诊断轮）

时间：2026-11-10 00:03–01:42。

## 结果

该轮不进入性能统计。两端固件身份和 selftest 正确，但均在 GATT 应用注册阶段停止：

```text
COM3: [A] register client failed
COM4: [B] register server failed
```

没有广播、扫描、连接、MTU、CCCD、参数更新、探针或 `# SUM` 数据。

## 文件哈希

```text
0be3e49495c788d7c79788157f1dc35427dde7b3786e118a2abac1ccafa55e93  client_COM3.txt
924f1d6ead3e81cd4aa4ff1aa33116591fc31139edfa2f5214eb081d375a6b7a  server_COM4.txt
```

## 修复

对齐 SDK 官方 `ble_uart` 样例，将 BLE 启动等待从 2 秒改为 5 秒；注册增加 500 ms 间隔的有限重试，
并打印每次注册的 `rc` 和 ID。修复后的日志将用于判断是初始化竞态还是其他 API 错误。
