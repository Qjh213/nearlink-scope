# LinkScope 固件

星闪链路体检仪的板端程序。M1 已完成测量内核与 SLE 写语义 A/B；M2 在同一套
探针、统计口径和硬件上加入 BLE 对照。

## 角色（编译期选择，一次构建一种）

| role | 板子 | 干什么 | 产出 |
|---|---|---|---|
| `initiator` | A | 按设定速率发 REQ，收 RSP，算 RTT | `S,seq,t1,t4,rtt,...` 与 `# SUM` 行 |
| `responder` | B | 收 REQ，打 t2/t3，立即回 RSP | 只打印连接状态，不打印数据 |
| `loopback` | 任意一块 | 不走空口，本机自环 | 噪声底（工具自身抖动） |
| `timing` | 任意一块 | `hello_timing`：时基分辨率/开销、延时与调度粒度 | 一组带单位的时间数字 |

## 目录

```
linkscope/
├── linkscope_main.c        角色分派、上报节奏（CSV / SUM）
├── probe.c/.h              REQ/RSP 帧编解码（纯逻辑，主机也能编）
├── stats.c/.h              对数分桶直方图、分位数、抖动、丢包（纯逻辑）
├── engine.c/.h             发起端停等状态机 + 无锁环形缓冲
├── selftest.c/.h           上电自检（probe/stats，同一份代码也能在 PC 上跑）
├── timing.c/.h             hello_timing
├── link_ops.h              传输层抽象（send / start / ready）
├── link_sle_initiator.c    SLE 发起端（seek→connect→pair→MTU→发现→写/收）
├── link_sle_responder.c    SLE 响应端（写回调里打点并回发）
├── link_loopback.c         本机自环
├── sle/                    从官方 sle_uart 样例拷来的 server/adv 底座（带©头）
└── test/                   主机侧单元测试（gcc 或 zig）
```

## 串口输出

上电先跑自检：

```
[linkscope]  [SELFTEST] pass=32 fail=0 -> PASS
[linkscope] ===== LinkScope W1 =====
[linkscope] role=initiator (A)
[linkscope] link=sle cfg=0 payload=40B rate=100Hz timeout=50ms
# seq,t1_us,t4_us,rtt_us,bproc_us,dlt_us
S,1,1048576,1048599,23,4,12
...
# SUM n=200 p50=... p99=... jit=... sent=... recv=... to=... loss_ppm=...
```

| 列 | 含义 | 时钟 |
|---|---|---|
| `t1_us` | A 发出时刻 | A |
| `t4_us` | A 收到 RSP 时刻 | A |
| `rtt_us` | `t4 - t1`，**主指标** | A（单一，无同步问题） |
| `bproc_us` | `t3 - t2`，B 端处理 | B（同一时钟，精确） |
| `dlt_us` | `t4 - t3` | 跨时钟，含未知偏差 δ，**只看趋势** |

`# SUM` 行的 `bproc=` / `dlt=` 是均值/最大值；`loss_ppm` 用 `(sent-recv)/sent` 算。

## 构建

源码在仓库里，构建在 Ubuntu 虚拟机里（SDK 在 Z: 盘）。先同步再编译：

```powershell
# Windows：同步到 SDK，并把 .config 切到目标角色
.\firmware\scripts\sync-to-sdk.ps1 -Role initiator
```

SLE 发起端默认使用有确认的 Write Request。若要与 BLE 当前的无响应写语义做更公平的对照：

```powershell
.\firmware\scripts\sync-to-sdk.ps1 -Role initiator -Link sle -SleWriteMode command
```

服务端属性同时声明 `WRITE` 与 `WRITE_NO_RSP`，因此同一份 responder 固件可接收两种写入。
Write Request 模式会额外输出协议层写确认时延；Write Command 没有该确认，RTT 仍以应用层
REQ/RSP 帧的 `t4-t1` 为准。日志中的链路名分别为 `sle-write_req` 和 `sle-write_cmd`，避免混淆数据。

BLE 同样支持两种写语义，默认是 Write Command。响应端 RX 特征同时声明 `WRITE` 与
`WRITE_NO_RSP`，所以两种模式共用一份 responder 固件：

```powershell
# BLE Write Command，连接间隔请求值 6 × 1.25 ms = 7.5 ms
.\firmware\scripts\sync-to-sdk.ps1 -Role initiator -Link ble -BleWriteMode command -BleIntvUnit 6

# BLE Write Request，同样请求 7.5 ms
.\firmware\scripts\sync-to-sdk.ps1 -Role initiator -Link ble -BleWriteMode request -BleIntvUnit 6
```

BLE 端只有在 CCCD 写确认完成、连接参数更新成功且实际授予间隔等于配置值后才进入
测量态。日志链路名为 `ble-write_cmd` 或 `ble-write_req`；Request 模式额外记录协议层
写确认，Command 模式不伪造这项指标。

```bash
# Ubuntu 虚拟机
cd /home/bearpi/project/bearpi-pico_h3863-master
python3 build.py -c ws63-liteos-app 2>&1 | tee linkscope_initiator.log
# 产物：output/ws63/fwpkg/ws63-liteos-app/ws63-liteos-app_all.fwpkg
```

UART 控制台为 115200。连接参数以配置值和运行日志中的实际授予值为准，不能只看源码默认值。

## 主机侧单元测试（不接板子）

```bash
cd firmware/linkscope/test && ./run_host_test.sh     # Ubuntu，用 gcc
```

```powershell
.\firmware\linkscope\test\run_host_test.ps1           # Windows，用 zig/gcc/clang
```

同一份 `selftest.c` 既能在板端跑，也能在 PC 上跑（`-DLS_HOST_TEST`），所以"自检通过"不是只靠板子说话。

## 几个刻意的取舍

- **停等发包**：一次只有一包在途，丢包口径干净；代价是超时期间发不出包（对端掉线时速率降到 1/timeout）。吞吐实验到 W3 再放开。
- **忙等主循环**（`LINK_SCOPE_LOOP_US=500`）：`osal_msleep(1)` 在 100Hz tick 下实际是 10ms，做不到 100Hz 的检查频率，所以主循环用 `osal_udelay` 忙等。
- **收包回调不打印**：t4 取完就进环形缓冲，打印在任务上下文做，避免 UART 反压污染被测链路。
- **上行发送缓冲是静态的**：`ssapc_write_req` / `ssapc_write_cmd` 是异步接口，不能把栈地址交给它。
