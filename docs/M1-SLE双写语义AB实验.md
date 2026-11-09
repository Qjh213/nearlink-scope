# M1：SLE Write Request / Write Command A/B 实验

## 1. 目的

只改变 initiator 的 SSAP 写入语义，比较：

- `ssapc_write_req()`：协议层有写确认；
- `ssapc_write_cmd()`：协议层无写确认。

两轮均使用同一 responder、相同 40 B 帧、40 Hz、窗口 1、超时 50 ms 和连接参数档位 24。
主指标仍是应用层 REQ/RSP 的 `t4-t1`，不能把 Write Request 独有的 CFM 时延当成 RTT。

## 2. 板卡与端口

| 板卡 | 角色 | Windows 串口 | 固件 |
|---|---|---:|---|
| 带 2.4 GHz 外置天线 | responder | COM4 | `linkscope-sle-responder-write_dual-40b.fwpkg` |
| 不带外置天线 | initiator | COM3 | 根据轮次选择 `write_cmd` 或 `write_req` 固件 |

串口参数统一为 `115200 / 8 data bits / 1 stop bit / no parity / no flow control`。

## 3. 固件身份校验

烧录前在 Windows PowerShell 执行：

```powershell
Get-FileHash E:\nearlink-scope\artifacts\firmware\*.fwpkg -Algorithm SHA256
```

结果必须与 `artifacts/firmware/SHA256SUMS.txt` 一致。三份包已额外用可见字符串验证：

- responder：包含 `role=responder`；
- Command initiator：包含 `role=initiator` 与 `sle-write_cmd`；
- Request initiator：包含 `role=initiator` 与 `sle-write_req`。

## 4. 固定实验条件

1. 两块板的位置、朝向、天线状态和供电方式保持不变；
2. responder 两轮不重新编译，始终使用同一固件；
3. 每轮先启动 COM4 日志，再启动 COM3 日志；先复位 responder，再复位 initiator；
4. 看到 `param updated ... intv=24` 后才进入有效采样期；此前预热数据不纳入统计；
5. 每轮稳定运行 5 分钟，不在期间操作板卡、串口窗口或改变周边距离；
6. 两端都保存完整原始日志，禁止只截取 `SUM` 行。

## 5. 轮次 A：Write Command

COM4 烧录：

```text
linkscope-sle-responder-write_dual-40b.fwpkg
```

COM3 烧录：

```text
linkscope-sle-write_cmd-initiator-40hz-40b-w1-intv24.fwpkg
```

启动后必须在 COM3 找到：

```text
link=sle-write_cmd
```

保存为：

```text
artifacts/logs/M1/write_cmd/client_COM3.txt
artifacts/logs/M1/write_cmd/server_COM4.txt
```

## 6. 轮次 B：Write Request

COM4 保持 responder 固件不变，只把 COM3 改烧：

```text
linkscope-sle-write_req-initiator-40hz-40b-w1-intv24.fwpkg
```

启动后必须在 COM3 找到：

```text
link=sle-write_req
```

Request 模式还应出现最初几条 `write cfm`，用于确认该路径确实生效。保存为：

```text
artifacts/logs/M1/write_req/client_COM3.txt
artifacts/logs/M1/write_req/server_COM4.txt
```

## 7. 验收口径

每轮至少核对：

- `param updated` 的 granted 原始档位；
- 稳态 `sent / recv / timeout / dup / loss_ppm`；
- RTT 的 `p50 / p90 / p99 / max / stdev`；
- responder 的 `bproc`；
- Request 独有的 `cfm avg/max`。

若两轮连接参数、运行时长或物理条件不同，不做协议语义结论，重新采集。
