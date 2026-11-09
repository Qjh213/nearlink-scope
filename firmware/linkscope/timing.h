/*
 * hello_timing：时基与调度抖动实测（W1 交付物之二）
 *
 * 只回答四个问题：
 *   1) uapi_tcxo_get_us() 的分辨率、单调性、单次调用开销
 *   2) uapi_tcxo_get_count()（24 ticks/us）能到什么分辨率
 *   3) uapi_tcxo_delay_us() / osal_udelay() / osal_msleep(1) 的实际误差
 *   4) GPIO 翻转 + 打点的自身开销（有逻辑分析仪时可对照波形）
 */
#ifndef LINK_SCOPE_TIMING_H
#define LINK_SCOPE_TIMING_H

void ls_timing_run(void);

#endif /* LINK_SCOPE_TIMING_H */
