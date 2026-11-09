/*
 * LinkScope 发起端状态机实现
 *
 * 窗口语义：in_flight < window 就发，发出去的每一包都记 (seq, t1)；
 * 回包按 (seq, t1) 精确配对，配不上的是迟到/重复（dup），没有在途的是 unsolicited。
 * window==1 时行为与原来的停等完全一致。
 */
#include <stddef.h>
#include "engine.h"
#include "linkscope.h"

static void ring_push(ls_engine_t *e, const ls_sample_t *s)
{
    uint16_t next = (uint16_t)((e->head + 1u) % LS_RING_SIZE);
    if (next == e->tail) {
        e->ring_drop++;         /* 消费跟不上，丢最旧的语义 -> 这里直接丢弃新样本并计数 */
        return;
    }
    e->ring[e->head] = *s;
    e->head = next;
}

/* 把 pending/pending_seq/pending_t1_us 同步成"最老的在途请求"（打印/诊断用） */
static void inflight_sync(ls_engine_t *e)
{
    uint8_t i;
    uint8_t found = 0u;

    for (i = 0u; i < (uint8_t)LS_WINDOW_MAX; i++) {
        if (e->inflight[i].used == 0u) {
            continue;
        }
        if (found == 0u) {
            e->pending_seq = e->inflight[i].seq;
            e->pending_t1_us = e->inflight[i].t1_us;
            found = 1u;
        } else if ((int32_t)(e->inflight[i].t1_us - e->pending_t1_us) < 0) {
            e->pending_seq = e->inflight[i].seq;
            e->pending_t1_us = e->inflight[i].t1_us;
        }
    }
    e->pending = found;
    if (found == 0u) {
        e->pending_seq = 0u;
        e->pending_t1_us = 0u;
    }
}

void ls_engine_track(ls_engine_t *e, uint16_t seq, uint32_t t1_us)
{
    uint8_t i;

    if (e == NULL) {
        return;
    }
    for (i = 0u; i < (uint8_t)LS_WINDOW_MAX; i++) {
        if (e->inflight[i].used == 0u) {
            e->inflight[i].used = 1u;
            e->inflight[i].seq = seq;
            e->inflight[i].t1_us = t1_us;
            e->in_flight++;
            inflight_sync(e);
            return;
        }
    }
    /* 槽满：调用方应保证 in_flight < window <= LS_WINDOW_MAX */
}

static void inflight_drop(ls_engine_t *e, uint8_t idx)
{
    if (e->inflight[idx].used == 0u) {
        return;
    }
    e->inflight[idx].used = 0u;
    if (e->in_flight > 0u) {
        e->in_flight--;
    }
    inflight_sync(e);
}

/* 按 (seq, t1) 找在途请求，返回下标；找不到 LS_WINDOW_MAX */
static uint8_t inflight_find(const ls_engine_t *e, uint16_t seq, uint32_t t1_us)
{
    uint8_t i;

    for (i = 0u; i < (uint8_t)LS_WINDOW_MAX; i++) {
        if (e->inflight[i].used != 0u &&
            e->inflight[i].seq == seq &&
            e->inflight[i].t1_us == t1_us) {
            return i;
        }
    }
    return (uint8_t)LS_WINDOW_MAX;
}

static void on_rsp(ls_engine_t *e, const uint8_t *data, uint16_t len, uint32_t t4_us)
{
    probe_rsp_t rsp;
    ls_sample_t s;
    uint32_t rtt;
    uint32_t bproc;
    uint32_t dlt;
    uint8_t idx;

    if (probe_parse_rsp(data, len, &rsp) != PROBE_OK) {
        e->st.bad_frame++;
        return;
    }
    if (e->in_flight == 0u) {
        e->st.unsolicited++;
        e->rej_seq = rsp.seq;
        e->rej_pend = 0u;
        e->rej_age_us = t4_us - rsp.t1_us;
        e->rej_bproc_us = (rsp.t3_us >= rsp.t2_us) ? (rsp.t3_us - rsp.t2_us) : 0u;
        e->rej_count++;
        return;
    }

    idx = inflight_find(e, rsp.seq, rsp.t1_us);
    if (idx >= (uint8_t)LS_WINDOW_MAX) {
        e->st.dup++;             /* 迟到/重复的 RSP：对应请求已经超时回收或本来就不是它 */
        e->rej_seq = rsp.seq;
        e->rej_pend = e->pending_seq;
        e->rej_age_us = t4_us - rsp.t1_us;
        e->rej_bproc_us = (rsp.t3_us >= rsp.t2_us) ? (rsp.t3_us - rsp.t2_us) : 0u;
        e->rej_count++;
        return;
    }

    rtt = (uint32_t)(t4_us - rsp.t1_us);
    if (rtt > 10000000u) {                /* >10s：判为坏样本（回绕或时钟异常） */
        e->st.bad_frame++;
        inflight_drop(e, idx);
        return;
    }

    inflight_drop(e, idx);

    e->st.recv++;
    ls_stats_on_rtt(&e->st, rtt);

    /* B 端处理时延：同一时钟，精确 */
    bproc = (rsp.t3_us >= rsp.t2_us) ? (rsp.t3_us - rsp.t2_us) : 0u;
    /* t4-t3 跨了两个时钟，含未知偏差 δ；只作为"回程+本级开销"的相对指标。
     * B 的时钟通常比 A 快，δ 会把 t4-t3 顶成负数（u32 回绕成 4.2e9），
     * 这种样本对分位数是有毒的，按 0 计（原始方向在 CSV 里也看不出来，所以直接归零）。 */
    dlt = ((int32_t)(t4_us - rsp.t3_us) < 0) ? 0u : (uint32_t)(t4_us - rsp.t3_us);
    ls_stats_on_segments(&e->st, bproc, dlt);

    s.seq = rsp.seq;
    s.t1_us = rsp.t1_us;
    s.t4_us = t4_us;
    s.rtt_us = rtt;
    s.bproc_us = bproc;
    s.dlt_us = dlt;
    ring_push(e, &s);
}

void ls_engine_set_window(ls_engine_t *e, uint8_t window)
{
    if (e == NULL) {
        return;
    }
    if (window == 0u) {
        window = 1u;
    }
    if (window > (uint8_t)LS_WINDOW_MAX) {
        window = (uint8_t)LS_WINDOW_MAX;
    }
    e->window = window;
}

void ls_engine_init(ls_engine_t *e, const link_ops_t *link,
                    uint32_t period_us, uint32_t timeout_us,
                    uint16_t payload_bytes, uint32_t cfg_id)
{
    uint8_t i;

    if (e == NULL) {
        return;
    }
    e->link = link;
    ls_stats_reset(&e->st);
    e->seq = 0u;
    e->payload_bytes = probe_req_len(payload_bytes);
    e->cfg_id = cfg_id;
    e->period_us = (period_us == 0u) ? 10000u : period_us;
    e->timeout_us = (timeout_us == 0u) ? 50000u : timeout_us;
    e->next_send_us = ls_now_us();
    e->window = 1u;
    e->in_flight = 0u;
    for (i = 0u; i < (uint8_t)LS_WINDOW_MAX; i++) {
        e->inflight[i].used = 0u;
        e->inflight[i].seq = 0u;
        e->inflight[i].t1_us = 0u;
    }
    e->pending = 0u;
    e->pending_seq = 0u;
    e->pending_t1_us = 0u;
    e->send_err = 0u;
    e->rej_count = 0u;
    e->rej_seq = 0u;
    e->rej_pend = 0u;
    e->rej_age_us = 0u;
    e->rej_bproc_us = 0u;
    e->head = 0u;
    e->tail = 0u;
    e->ring_drop = 0u;
}

void ls_engine_tick(ls_engine_t *e, uint32_t now_us)
{
    /*
     * 静态发送缓冲：协议栈可能在 send() 返回后还持有指针，所以每个在途槽一份，
     * 不能像停等那样复用同一块（那会让后发的包把前一个在途包的载荷改掉）。
     */
    static uint8_t tx_buf[LS_WINDOW_MAX][PROBE_FRAME_MAX];
    probe_req_t req;
    uint32_t t1;
    int n;
    uint8_t i;
    uint8_t slot;
    int sent_now;

    if (e == NULL || e->link == NULL) {
        return;
    }

    /* 超时判定：逐包看龄期，过期的回收成丢包 */
    for (i = 0u; i < (uint8_t)LS_WINDOW_MAX; i++) {
        if (e->inflight[i].used != 0u &&
            (uint32_t)(now_us - e->inflight[i].t1_us) >= e->timeout_us) {
            e->st.timeout++;
            inflight_drop(e, i);
        }
    }

    sent_now = 0;
    while (e->in_flight < e->window) {
        if ((int32_t)(now_us - e->next_send_us) < 0) {
            break;
        }
        if (e->link->ready != NULL && e->link->ready() == 0u) {
            e->next_send_us = now_us + e->period_us;   /* 没连上就先不攒包 */
            break;
        }

        slot = (uint8_t)LS_WINDOW_MAX;
        for (i = 0u; i < (uint8_t)LS_WINDOW_MAX; i++) {
            if (e->inflight[i].used == 0u) {
                slot = i;
                break;
            }
        }
        if (slot >= (uint8_t)LS_WINDOW_MAX) {
            break;
        }

        e->seq++;
        t1 = ls_now_us();                     /* 时间戳尽量贴近发送动作 */
        req.seq = e->seq;
        req.t1_us = t1;
        req.cfg_id = e->cfg_id;
        req.frame_len = e->payload_bytes;

        n = probe_build_req(tx_buf[slot], (uint16_t)sizeof(tx_buf[slot]), &req);
        if (n <= 0) {
            e->send_err++;
            break;
        }

        /*
         * 先置"在途"再发：loopback 会在 send() 的调用栈里同步把 RSP 送回来，
         * 若先发后置，RSP 到达时会被误判成 unsolicited。真实空口下这个顺序也无副作用。
         */
        ls_engine_track(e, e->seq, t1);

        if (e->link->send(tx_buf[slot], (uint16_t)n) != 0) {
            inflight_drop(e, slot);
            e->send_err++;
            e->next_send_us = now_us + e->period_us;
            break;
        }
        e->st.sent++;
        sent_now++;

        e->next_send_us += e->period_us;
        if (e->window <= 1u && (int32_t)(now_us - e->next_send_us) > 0) {
            /* 停等：落后太多就重新对齐，不补发 */
            e->next_send_us = now_us + e->period_us;
            break;
        }
        if (sent_now >= (int)e->window) {
            /* 本 tick 最多补满一个窗口，避免把积压一次性全喷出去 */
            break;
        }
    }
}

void ls_engine_on_frame(ls_engine_t *e, const uint8_t *data, uint16_t len, uint32_t t4_us)
{
    if (e == NULL || data == NULL) {
        return;
    }
    if (len == PROBE_RSP_LEN) {
        on_rsp(e, data, len, t4_us);
    } else {
        e->st.bad_frame++;
    }
}

int ls_engine_pop(ls_engine_t *e, ls_sample_t *out)
{
    if (e == NULL || out == NULL || e->tail == e->head) {
        return 0;
    }
    *out = e->ring[e->tail];
    e->tail = (uint16_t)((e->tail + 1u) % LS_RING_SIZE);
    return 1;
}
