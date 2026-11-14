# 固件归档说明

本目录只存 `.fwpkg`（烧录包）和 `SHA256SUMS.txt`（权威清单）。`.gitignore` 忽略了 `*.fwpkg`，
所以包本身只在本地磁盘；**清单在仓库里**，用来证明“哪个实验配的是哪一版固件”。

归档命名：`linkscope-<系列>-<配置或角色>-<关键参数>.fwpkg`。
`_all.fwpkg` 才是可烧录的完整包；`_load_only.fwpkg` 是分区包，不归档。

## M5 各档对应的 initiator 固件

| 档位 | 固件 | SHA-256（前 16 位） |
|---|---|---|
| cfg500 开头基线 | `linkscope-m5-cfg500-sle-cmd-40hz-40b.fwpkg` | `815a01d0a03b44af` |
| cfg511 12 B | `linkscope-m5-cfg511-payload12.fwpkg` | `89f2259e140b398c` |
| cfg512 100 B | `linkscope-m5-cfg512-payload100.fwpkg` / `-final` / `-readyfix` | `195983f03942e8aa` / `b1bae1fdcdb83f6e` / `a5ba4199ba45eff4` |
| cfg513 250 B | `linkscope-m5-cfg513-payload250.fwpkg` | `15a49574165f8a42` |
| cfg521 10 Hz | `linkscope-m5-cfg521-rate10hz.fwpkg` | `bd4acce99418bd0d` |
| cfg522 25 Hz | `linkscope-m5-cfg522-rate25hz.fwpkg` | `aa9b186aa093be7e` |
| cfg523 60 Hz | `linkscope-m5-cfg523-rate60hz.fwpkg` | `2b95218f80cec204` |
| cfg541 受扰 | `linkscope-m5-cfg541-interference.fwpkg` | `5265408c37a2f102` |
| cfg500 结尾基线 | `linkscope-m5-cfg500-end-baseline.fwpkg` | `9cd78eb2e4b65b6c` |

responder 全部 M5 正式轮使用 P139：`linkscope-m5-responder-p139-queuefix.fwpkg`，
SHA-256 `072ba2ba26462abd1497017abdedea609867a13ac44268fd3df084a38bd68a14`。

## 两个必须知道的坑

**1. `linkscope-m5-responder-notifyfix-stale-pre-p139.fwpkg` 的名字是错的。**

它与 `linkscope-m5-responder-p139-queuefix.fwpkg` **字节完全相同**（都是 1,433,704 B、
SHA-256 `072ba2ba…`），只是拷贝时间早（06:00:51 vs 09:09:41）。也就是说它是 P139 修复包的重复副本，
不是“修复前的旧包”。要复现崩溃不能用它，它就是要验证的那个修复版本。

真正的 P139 之前 responder 是 2026-11-09 的
`linkscope-sle-responder-write_dual-40b.fwpkg`（1,433,576 B，`333d6f55364d2f6b…`），
但它没有和 M5 的任何一轮绑定，不能当作 cfg500–513 的固件身份。

**2. P135 的失败包 `9e256b7b7c9833d52dcd713650dd8cd1a25af8b2c888752a617dea4302476827`
不在本机归档里。**

全盘扫描（仓库 + Z: 盘 SDK 输出）都没有这个哈希；目前只剩交接文档里的哈希记录。所以
“失败包 vs 修复包”的字节级对比在这台机器上做不了，只能靠源码 diff（`link_sle_responder.c` 的
队列读失败路径 + `osal_msleep(1)`）说明差异。任何声称是 `9e256…` 的文件都要重新算哈希核对。

## 校验方式

```powershell
Get-FileHash .\linkscope-m5-responder-p139-queuefix.fwpkg -Algorithm SHA256
```

清单里每条都是 `哈希  文件名`，可直接比对。
