/*
 * LinkScope 固件公共定义：时钟、日志前缀、上行入口。
 */
#ifndef LINK_SCOPE_H
#define LINK_SCOPE_H

#include <stdint.h>
#include "tcxo.h"
#include "probe.h"
#include "stats.h"
#include "link_ops.h"

#define LS_LOG "[linkscope] "

/**
 * 单调微秒时钟。底层是 uapi_tcxo_get_us()（u64），这里截成 u32：
 * 与协议里 4 字节 t1/t2/t3/t4 对齐，回绕靠无符号减法天然处理（71.6 分钟）。
 */
static inline uint32_t ls_now_us(void)
{
    return (uint32_t)uapi_tcxo_get_us();
}

/* 传输层收到一帧后调用（t_recv_us 必须在回调第一行取） */
void ls_on_frame(const uint8_t *data, uint16_t len, uint32_t t_recv_us);

/* 响应端主循环（仅在 role=responder 时编译） */
void ls_responder_run(void);

#endif /* LINK_SCOPE_H */
