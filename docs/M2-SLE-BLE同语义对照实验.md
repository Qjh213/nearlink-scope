# M2 · SLE/BLE 同写语义公平对照实验

## 1. 目标

在同一对 BearPi-Pico H3863、同一探针协议、同一统计引擎和同一负载下，分别测量：

1. SLE Write Command vs BLE Write Command（主对照）；
2. SLE Write Request vs BLE Write Request（补充对照）；
3. BLE Request vs Command（解释 BLE 内部写语义代价）。

M2 不把实测差异外推成协议标准或芯片的通用性能，只报告当前 SDK、板卡、射频环境和配置下的结果。

## 2. 固定变量

| 项目 | 固定值/口径 |
|---|---|
| 板卡 | 同两块 BearPi-Pico H3863；COM3 initiator，COM4 responder |
| 负载 | 40 B |
| 发包策略 | 停等，window=1 |
| 目标速率 | 40 Hz |
| 超时 | 50 ms |
| 主指标 | 应用层 REQ/RSP RTT，即 A 端 `t4-t1` |
| 统计 | p50/p90/p99/p999、mean、jitter、timeout、duplicate、bad、drop |
| 预热 | 连接、CCCD、连接参数均完成后才开始采样；正式分析剔除预热异常 |
| 样本数 | 每轮至少 28,800 个有效响应；不足时延长运行 |

连接参数必须同时记录“请求原始值”和“运行回调实际授予值”。SLE 与 BLE 的原始单位不同，未经官方材料或
运行证据确认时，不把数值直接换算为相同物理间隔。

## 3. BLE 运行准入

initiator 日志必须依次出现：

1. 扫描命中 `linkscope`；
2. connected；
3. MTU 交换；
4. RX/TX 特征与 CCCD 发现；
5. `transport ready`（CCCD 写确认成功）；
6. `param updated` 且授予间隔等于配置；
7. `measurement ready after param update`；
8. 与固件一致的链路名：`ble-write_cmd` 或 `ble-write_req`。

缺少任一项时，该轮日志不能进入正式统计。

## 4. 实验顺序

### 4.1 BLE 双模式构建与上板

先构建并烧录 BLE responder。其 RX 特征同时支持 Request 与 Command，两轮无需更换服务端固件。

```powershell
.\firmware\scripts\sync-to-sdk.ps1 -Role responder -Link ble -BleWriteMode command `
  -BleIntvUnit 6 -Hz 40 -Payload 40 -Window 1 -TimeoutMs 50
```

随后分别构建 initiator：

```powershell
# 主对照：Command
.\firmware\scripts\sync-to-sdk.ps1 -Role initiator -Link ble -BleWriteMode command `
  -BleIntvUnit 6 -Hz 40 -Payload 40 -Window 1 -TimeoutMs 50

# 补充对照：Request
.\firmware\scripts\sync-to-sdk.ps1 -Role initiator -Link ble -BleWriteMode request `
  -BleIntvUnit 6 -Hz 40 -Payload 40 -Window 1 -TimeoutMs 50
```

Ubuntu 中每次同步后执行：

```bash
cd /home/bearpi/project/bearpi-pico_h3863-master
python3 build.py -c ws63-liteos-app 2>&1 | tee linkscope_m2_build.log
```

### 4.2 正式采集

每轮同时保存 COM3、COM4 完整原始日志，不截取、不覆盖：

```text
artifacts/logs/M2/ble_write_cmd/client_COM3.txt
artifacts/logs/M2/ble_write_cmd/server_COM4.txt
artifacts/logs/M2/ble_write_req/client_COM3.txt
artifacts/logs/M2/ble_write_req/server_COM4.txt
```

SLE 对照优先复用 M1 已验收的同写语义数据；如果 M2 为连接参数对齐修改了任何配置，就必须重新采集，
不能把不同条件的数据拼在同一张主对照表中。

## 5. 验收标准

- 两端 selftest 通过；
- 固件身份、写语义与日志链路名一致；
- BLE CCCD 和连接参数准入链完整；
- 正式采样期间无断链；
- `bad=0`、本地 `drop=0`；
- timeout/duplicate 若存在，能区分预热累计值与稳态新增值；
- Request 的写确认只作辅助指标，不与应用 RTT 相加；
- 四组比较均明确写语义，不出现笼统的“SLE vs BLE”结论。

## 6. M2 文章交付

阶段验收后生成一篇个人学习笔记风格文章，正文和图片保存到：

```text
E:\Starklink\文章\link\M2
```

文章至少包含：公平对照为什么难、三个审计问题及修复、连接建立证据链、同语义数据表、结论边界、
可复现实验命令和 GitHub 仓库链接。

## 7. 正式结果（统一 n=28,800）

| 链路/写语义 | p50 | p90 | p99 | mean | jitter | timeout/duplicate |
|---|---:|---:|---:|---:|---:|---:|
| SLE Command | 14.873 ms | 16.128 ms | 18.403 ms | 14.830 ms | 1.471 ms | 0/0 |
| BLE Command | 14.913 ms | 18.041 ms | 23.444 ms | 14.919 ms | 3.560 ms | 0/0 |
| SLE Request | 23.362 ms | 24.536 ms | 26.478 ms | 23.640 ms | 1.263 ms | 0/0 |
| BLE Request | 14.117 ms | 18.361 ms | 20.466 ms | 14.573 ms | 3.435 ms | 1/1 |

BLE Request 的一次 timeout 和对应迟到 duplicate 约发生在 n=2,200，之后直到34,400有效样本均未继续增长。
四组结果只用于描述当前实现；连接参数原始单位、SDK调度路径及 API 行为尚不能从协议因素中完全剥离。
