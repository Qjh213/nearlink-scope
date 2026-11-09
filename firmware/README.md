# firmware

H3863 固件工程。W1 的实现放在 `linkscope/`（应用名 `linkscope`），
源码以本仓库为准，构建前用 `firmware/scripts/sync-to-sdk.ps1` 同步进 SDK。

- 设计依据：`docs/项目方案.md` §4（分层、link_ops 三实现）
- 协议：`docs/protocol.md`
- W1 结论与待实测项：`docs/W1-平台能力确认.md`
- 构建 / 输出格式 / 取舍：`firmware/linkscope/README.md`

底座来源：响应端的 SSAP server 与广播代码来自官方 `application/samples/products/sle_uart/sle_uart_server/`
（Apache-2.0，保留原版权头），已随仓库拷贝到 `linkscope/sle/`；发起端按同样的调用顺序重写，
去掉了逐包打印，并把 5s 递归初始化改成一次。

## 待确认（W1）

源码能答的已经答完，详见 `docs/W1-平台能力确认.md`；剩下两项必须实测：

- [x] 板载是否自带 USB 转串口 —— 是，Type-C 直连烧录/调试（需 CH34x 驱动）
- [x] 微秒时基接口（`uapi_tcxo_get_us()`）是否存在、是否单调 —— 存在、u64、24 ticks/µs、需 `uapi_tcxo_init()`
- [x] `ssapc_write_req` 的流控行为与在途包上限 —— 头文件未定义上限；W1 用停等规避，上限待 W2 实测
- [x] 连接间隔可否通过 API 配置、最小值 —— `sle_update_connect_param()`，单位 slot=125µs；最小档待实测
- [x] RSSI 是否可读 —— 可读（`sle_read_remote_device_rssi()` + 回调）
- [ ] 任务优先级 28 下的调度抖动实测 —— `-Role timing` 产出
- [ ] 250B 单包是否真能跑通 —— W2 用 `LINK_SCOPE_PAYLOAD_BYTES=250` 验证
