/* 主机侧编译 shim：用可控的假时钟替代 TCXO，便于穷举时间边界 */
#ifndef TCXO_SHIM_H
#define TCXO_SHIM_H

#include <stdint.h>
#include "errcode.h"

extern uint64_t g_host_now_us;

/* 每次读时钟前进 1us：模拟真实时钟在两次打点之间也会走，避免 RTT 恒为 0 */
static inline uint64_t uapi_tcxo_get_us(void)
{
    g_host_now_us += 1u;
    return g_host_now_us;
}

static inline uint64_t uapi_tcxo_get_ms(void)
{
    return g_host_now_us / 1000u;
}

errcode_t uapi_tcxo_init(void);
uint64_t uapi_tcxo_get_count(void);
errcode_t uapi_tcxo_delay_us(uint32_t us);

#endif
