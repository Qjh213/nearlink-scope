# M2 BLE Write Command run2（烧录校验失败 + 服务回调暴露）

时间：2026-11-10 13:37–13:39。

## COM3

应用镜像未通过启动校验并持续复位：

```text
verify_image_code_area hash memcmp fail!
verify_image_app_body failed!! ret = 0x800015C8
```

因此 BurnTool 停在99%且显示 `Execution Successful` 不能作为本轮烧录成功证据；COM3 必须重新完整烧录。

## COM4

注册重试修复生效：第一次返回 `0x80006001`，约500 ms后成功取得 `server id=1`。随后协议栈内置
GAP/GATT 服务的 start 回调也进入了 LinkScope 的全局回调，造成三次启动广播；`status=0x1` 的含义是
`ADV_STATUS_ADVERTISING`，不是错误。

修复方式：只处理 UUID 为 `0xFE30` 的自定义服务 add 回调，并保存其 handle；start 回调只接受该 handle。

## 哈希

```text
6436632449f8e40bc6c54fef0802db56e17c7593d78476f5ad361fc19d22a2b2  client_COM3.txt
cb8394171be4490ff27b716114701741f3378690a040656013759f7ae0b54f2e  server_COM4.txt
```
