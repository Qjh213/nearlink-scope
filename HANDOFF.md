# HANDOFF · 会话交接说明

> 这份文件给"在新工作区 E:\nearlink-scope 里新建的那个会话"看。
> 上一个会话（default-workspace）已经完成了选题、方案和仓库骨架，这里是接续点。

## 一句话现状

项目已立项：**星闪链路体检仪 · NearLink Scope**。方案、协议、仓库骨架均已落盘，硬件 2× BearPi-Pico H3863 在手，**还没写任何固件代码**——卡在开发环境信息确认。

## 已完成

| 事项 | 产物 |
|---|---|
| 选题与差异化设计 | `docs/项目方案.md` |
| 探针帧格式 v0.1 | `docs/protocol.md` |
| 仓库骨架 + 命名约定 | `README.md` |
| 固件改写要点 + W1 待确认清单 | `firmware/README.md` |
| SDK 能力实测（时基/调度/SLE 参数） | `docs/W1-平台能力确认.md` |
| 立项至今全部踩坑与解法（P01–P24） | `docs/项目日志.md` |

## 三条设计主线（不要推翻，除非有实测证据）

1. **测 RTT，不测单向时延** —— 两板晶振不同步，单向误差被时钟偏差淹没。RTT 拆成「上行 + 对端处理 + 回程」三段定位瓶颈。
2. **同板 BLE 对照** —— H3863 一颗芯片一根天线，同探针跑 BLE 即变量唯一的对照组。固件必须有 `link_ops` 抽象层（sle / ble / loopback 三实现）。
3. **先测噪声底** —— 本地回环量出工具自身抖动，报告注明"仅对超过噪声底的差异下结论"。

## 下一步（W1）

1. 确认开发环境（用户将提供）：系统、HiSpark Studio / VS Code、工具链能否编译烧录、Python + pyserial、串口驱动、git
2. 确认板载是否自带 USB 转串口
3. 烧官方 `sle_uart` 例程，双板跑通透传
4. 写 `hello_timing`：取时间戳 + 翻 GPIO + 打印差值 → 测时基分辨率与调度抖动
5. 做本地回环噪声底测量
6. 填完 `firmware/README.md` 里的 W1 待确认清单

## W1 进展（2026-11-05 更新）

**环境已确认**：编译在 Ubuntu 虚拟机（`192.168.22.129`，`bearpi`），SDK 在 VM 的
`/home/bearpi/project/bearpi-pico_h3863-master`，Windows 经 RaiDrive 映射成 `Z:`；
构建命令 `python3 build.py -c ws63-liteos-app`，产物 `output/ws63/fwpkg/.../*_all.fwpkg`。
离线副本 `E:\Starklink\...` 只作参照（少了 output，且 adv.c 还是固定 MAC 的旧版）。

**已完成（不进硬件就能做的部分）**

- `firmware/linkscope/`：`timing` / `loopback` / `initiator` / `responder` 四种角色，
  `link_ops` 抽象 + 停等 engine + 对数直方图统计 + 上电自检。
- 主机侧单测：`firmware/linkscope/test/`（probe/stats 21 项、engine/loopback 集成 11 项，全过）；
  全部源文件已用 SDK 真头文件编译通过（`-Wall -Wextra`）。
- `docs/W1-平台能力确认.md`：8 项能力逐条给结论 + 证据 + 待实测项；
  修正了方案里 4 处错误（`t4-t3` 跨时钟、`set_mcs` 函数名、性能模式宏、`osal_msleep(1)`≠1ms）。
- `firmware/scripts/sync-to-sdk.ps1`：把源码同步进 Z: 的 SDK，并挂钩 Kconfig/CMake/`.config`
  （已执行一次，SDK 已切到 `role=initiator`，原文件备份为 `*.bak.linkscope`）。

**剩下的是实机三件事**（命令见 `firmware/linkscope/README.md`）

1. `-Role timing` → 时基分辨率/开销、`osal_msleep(1)` 实际粒度
2. `-Role loopback` → 噪声底 P50/P99
3. A=`initiator` + B=`responder` → 双板 `S,seq,t1,t4,rtt` 与 `# SUM`

## 环境确认后立刻要写的三块代码（纯逻辑，不依赖 SDK）

- `firmware/linkscope/probe.c/.h` —— REQ/RSP 帧编解码
- `firmware/linkscope/stats.c/.h` —— 对数分桶直方图 + 分位数 + 丢包/乱序
- `tools/nlscope.py` —— pyserial 采集 + 实时曲线 + CSV

## 外部参考

- 官方例程骨架：`Hermes`/SDK 中 `application/samples/products/sle_uart/sle_uart.c`
- 低时延：`sle_low_latency.h` → `sle_low_latency_tx_register_callbacks()`
- MCS 档位：`sle_uart_server_sample_set_mcs(conn_id)`
- 单包上限：`CONFIG_SAMPLE_SUPPORT_PERFORMANCE_TYPE` 下 40B → 250B
- 社区项目：[NLChat](https://github.com/Hny0305Lin/NLChat)、[NearLink-Mesh-ePaper](https://github.com/NearLink-ePaper/NearLink-Mesh-ePaper)、[nearlink-firmwares](https://github.com/MiraHikari/nearlink-firmwares)
- 协议栈开源仓：[communication_nearlink_service](https://github.com/openharmony/communication_nearlink_service)

## 给新会话的第一句话建议

"项目在 E:\nearlink-scope，先读 README.md 和 docs/项目方案.md，然后按 HANDOFF.md 的 W1 清单继续。"
