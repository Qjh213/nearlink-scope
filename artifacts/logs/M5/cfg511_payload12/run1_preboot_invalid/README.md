# M5 cfg511 Payload 12B · run1（证据边界无效）

## 判定

**本轮性能数据可参考，但不作为正式run1，修复后重做。**

平台签名、375.310秒时长、15001条成功样本和双端业务均正常；然而COM3原始Session在板卡本次复位前已经
保存了20行旧运行日志，`boot.`位于`initiator_COM3.log`第21行。这说明当时版本只限制了分析窗口显示，
没有把同一复位门应用到Parser和原始导出，不满足正式证据必须从本次boot开始的规则。

## 可参考结果

- cfg511、Payload 12 B、40 Hz、MTU=520、interval=24、ready=yes；
- 采集375.310 s，样本跨度375.262 s；
- 15001条成功样本，seq=1…15001，无序号缺口；
- p50 11.816 ms、p90 12.944 ms、p99 13.769 ms、max 50.309 ms；
- sent=recv=15000，timeout/duplicate/ooo/bad/drop均为0；
- COM4从本次boot开始记录，757条心跳均bad=0、req=rsp、lasterr=0。

修复版本从COM3串口bytes入口即丢弃boot前数据，并且每次开始都创建全新Session。本目录原始证据保留，
不删除、不伪装为通过轮次；下一轮仍保存到run1，需先将本目录改名为`run1_preboot_invalid`或使用新目录。
