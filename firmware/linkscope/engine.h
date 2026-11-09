/*
 * 发起端状态机：发 REQ、收 RSP、算 RTT。
 *
 * 在途窗口 window：1 = 停等（W1 的默认口径，丢包解释最干净）；
 * >1 = 流水线（P26：停止等往返底线 24ms、上限 41 包/秒，要更高吞吐只能放开在途）。
 * 不论窗口多少，每个 RSP 都按 (seq, t1) 精确配对回收，迟到/重复仍计 dup。
 *
 * 热路径约定：
 *   ls_engine_on_frame() 可能在协议栈回调上下文里执行，只做解析 + 计数 + 入环形缓冲，
 *   绝不打印；打印由任务上下文 ls_engine_pop() 取走后完成。
 */
#ifndef LINK_SCOPE_ENGINE_H
#define LINK_SCOPE_ENGINE_H

#include <stdint.h>
#include "link_ops.h"
#include "probe.h"
#include "stats.h"

#define LS_RING_SIZE        128u
#define LS_WINDOW_MAX       8u

/* 一个在途请求。t1 是发出时刻，用于回包配对（seq 会回绕，seq+t1 一起判才不会认错） */
typedef struct {
    uint8_t  used;
    uint16_t seq;
    uint32_t t1_us;
} ls_inflight_t;

typedef struct {
    uint16_t seq;
    uint32_t t1_us;
    uint32_t t4_us;
    uint32_t rtt_us;
    uint32_t bproc_us;      /* t3-t2，B 端时钟内有效 */
    uint32_t dlt_us;        /* t4-t3，含未知时钟偏差 δ，只看趋势 */
} ls_sample_t;

typedef struct {
    const link_ops_t *link;
    ls_stats_t st;

    uint16_t seq;
    uint16_t payload_bytes;
    uint32_t cfg_id;
    uint32_t period_us;
    uint32_t timeout_us;
    uint32_t next_send_us;

    uint8_t window;                    /* 在途上限：1 = 停等 */
    uint8_t in_flight;
    ls_inflight_t inflight[LS_WINDOW_MAX];

    /* 最老在途请求的镜像（打印/诊断用；window==1 时就是那一包） */
    uint8_t pending;
    uint16_t pending_seq;
    uint32_t pending_t1_us;

    uint32_t send_err;

    /*
     * 被拒 RSP 的现场快照（只写不打印，打印交给任务上下文）。
     * 用来区分"帧没到"和"帧到了但迟到/对不上"：
     *   rej_seq/rej_pend  seq 与在途 seq 是否相同
     *   rej_age_us       t4 - rsp.t1，跨时钟但同机，精确 = 该帧真实 RTT
     *   rej_bproc_us     rsp.t3 - rsp.t2，B 端处理耗时（同一时钟，精确）
     *   rej_count        被拒次数，用于"有新样本才打印"
     */
    volatile uint32_t rej_count;
    volatile uint16_t rej_seq;
    volatile uint16_t rej_pend;
    volatile uint32_t rej_age_us;
    volatile uint32_t rej_bproc_us;

    ls_sample_t ring[LS_RING_SIZE];
    volatile uint16_t head;
    volatile uint16_t tail;
    uint32_t ring_drop;
} ls_engine_t;

void ls_engine_init(ls_engine_t *e, const link_ops_t *link,
                    uint32_t period_us, uint32_t timeout_us,
                    uint16_t payload_bytes, uint32_t cfg_id);

/* 设置在途窗口（1..LS_WINDOW_MAX），默认 1（停等）。可在运行期改。 */
void ls_engine_set_window(ls_engine_t *e, uint8_t window);

/* 登记一个已发出的在途请求（引擎内部用；主机单测构造场景也用它） */
void ls_engine_track(ls_engine_t *e, uint16_t seq, uint32_t t1_us);

/* 任务上下文按毫秒级节奏调用 */
void ls_engine_tick(ls_engine_t *e, uint32_t now_us);

/* 协议栈回调上下文调用：只解析、计数、入队 */
void ls_engine_on_frame(ls_engine_t *e, const uint8_t *data, uint16_t len, uint32_t t4_us);

/* 任务上下文取一条已完成的样本；返回 1 表示取到 */
int ls_engine_pop(ls_engine_t *e, ls_sample_t *out);

#endif /* LINK_SCOPE_ENGINE_H */
