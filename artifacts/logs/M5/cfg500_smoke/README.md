# M5 cfg500首次双串口冒烟（无效配置，保留作工具证据）

时间：2026-11-11。双串口打开、COM3四类分析导出、COM4原始日志和run.json均成功生成，证明第一版联合
工作台链路成立。

但本轮不能作为cfg500基线：COM3实际上仍运行M4 monitor固件，完整签名为：

```text
role=monitor
link=sle-passive-seek
seek=100/100
filter_dup=1
selftest=PASS
ready=yes
```

因此COM4没有连接，心跳req/bad/rsp始终为0。run.json记录1,436条monitor报告、23.596秒，不是预期的
SLE Write Command RTT样本。本轮失败证据不删除，用于验证双端联合导出，并推动PC工具增加“预期实验配置
与实测平台签名匹配”准入门。

修复后选择 `M5 cfg500 基线 40B/40Hz` 时，role/link/cfg/payload/rate/window/timeout任一不匹配都不会
开始采样或计时。

## SHA-256

```text
fe858d69f2ec2743b770830b7bac4787162bffb5a04307bc3d77e74d70533c57  initiator_COM3.csv
bc15d37d0e55417fe3ee75e2c1fde23582a47d2d06d1b8a6f7c63fe8543d2142  initiator_COM3.json
635dc73ad972599f2b6e8c27f7510eac156485d31da675f9c3248dcaa3f5ea81  initiator_COM3.log
54b95ae12b43ea4ecdc971fa91e4e9d1ec771d901c52bdcf9b63075220a25f97  initiator_COM3.md
ad20102df35175b456ea71d85a0ff6854f7e5ba6b4e6f599df9a8ad31d3d797d  responder_COM4.log
cf558c0ea410802f89c0ffc1dea326acfa8bb32a3b25a4bd32f9edacd38bbfbc  run.json
```
