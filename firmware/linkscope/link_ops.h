/*
 * 传输层抽象：上层（engine / app）只认这三个动作。
 * SLE / BLE / loopback 各实现一份，统计与协议代码零改动。
 */
#ifndef LINK_SCOPE_LINK_OPS_H
#define LINK_SCOPE_LINK_OPS_H

#include <stdint.h>

typedef struct {
    const char *name;                       /* "sle" / "loopback" */
    int  (*start)(void);                    /* 建链/开局；返回 0 成功 */
    int  (*send)(const uint8_t *data, uint16_t len);  /* 发一帧；返回 0 成功 */
    uint8_t (*ready)(void);                 /* 连接是否已就绪（可发） */

    /* 下面两个都是可选的诊断口，都在**任务上下文**里调用，不在回调里打印 */
    void (*poll)(uint32_t now_ms);          /* 每 5s 一次：链路层自己的周期性动作 */
    void (*dbg)(uint32_t *count, uint32_t *avg_us, uint32_t *max_us);  /* 写确认时延 */
} link_ops_t;

#endif /* LINK_SCOPE_LINK_OPS_H */
