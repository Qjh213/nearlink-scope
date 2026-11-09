/*
 * 主机侧 engine 集成测试：用假时钟跑 loopback 链路 + 超时链路。
 * 覆盖：发包节奏、停等、超时判丢、环形缓冲、包长夹取、RTT 统计。
 */
#include <stdio.h>
#include <stdint.h>
#include "engine.h"
#include "link_ops.h"
#include "linkscope.h"

uint64_t g_host_now_us;

static ls_engine_t g_engine;
static int g_fail;

const link_ops_t *ls_link_ops_get(void);   /* link_loopback.c */

void ls_on_frame(const uint8_t *data, uint16_t len, uint32_t t_recv_us)
{
    ls_engine_on_frame(&g_engine, data, len, t_recv_us);
}

static void expect(int cond, const char *name, uint32_t got_blob)
{
    if (!cond) {
        g_fail++;
        printf("[FAIL] %s (got %u)\n", name, got_blob);
    }
}

static int no_start(void) { return 0; }
static int no_send(const uint8_t *d, uint16_t l) { (void)d; (void)l; return 0; }
static uint8_t no_ready(void) { return 1u; }

static const link_ops_t g_dead_link = { "dead", no_start, no_send, no_ready, NULL, NULL };

static uint32_t run_loop(uint32_t steps, uint32_t step_us)
{
    uint32_t i;
    uint32_t got = 0;
    ls_sample_t s;

    for (i = 0; i < steps; i++) {
        g_host_now_us += step_us;
        ls_engine_tick(&g_engine, (uint32_t)g_host_now_us);
        while (ls_engine_pop(&g_engine, &s) != 0) {
            got++;
        }
    }
    return got;
}

int main(void)
{
    uint32_t got;
    ls_summary_t sum;

    /* --- 1. loopback：应该几乎不丢包，RTT 很小 --- */
    g_host_now_us = 1000u;
    ls_engine_init(&g_engine, ls_link_ops_get(), 1000u, 5000u, 40u, 1u);
    expect(g_engine.payload_bytes == 40u, "payload 40", g_engine.payload_bytes);
    got = run_loop(20000u, 100u);          /* 2 s，每 100us 走一步 */
    sum = ls_stats_summary(&g_engine.st);
    expect(sum.sent >= 1500u, "loopback sent", sum.sent);
    expect(sum.recv == sum.sent, "loopback recv==sent", sum.recv);
    expect(sum.timeout == 0u, "loopback no timeout", sum.timeout);
    expect(sum.dup == 0u && g_engine.st.bad_frame == 0u, "loopback no dup/bad", sum.dup);
    expect(got == sum.recv, "loopback popped==recv", got);
    expect(sum.p50_us > 0u && sum.p50_us < 50u, "loopback p50 tiny", sum.p50_us);
    printf("[ok] loopback sent=%u recv=%u p50=%uus p99=%uus\n", sum.sent, sum.recv, sum.p50_us, sum.p99_us);

    /* --- 2. 包长夹取 --- */
    ls_engine_init(&g_engine, ls_link_ops_get(), 1000u, 5000u, 5u, 1u);
    expect(g_engine.payload_bytes == 12u, "payload min clamp", g_engine.payload_bytes);
    ls_engine_init(&g_engine, ls_link_ops_get(), 1000u, 5000u, 999u, 1u);
    expect(g_engine.payload_bytes == 250u, "payload max clamp", g_engine.payload_bytes);

    /* --- 3. 对端不回：超时判丢，且停等不阻碍后续发包 --- */
    g_host_now_us = 1000u;
    ls_engine_init(&g_engine, &g_dead_link, 1000u, 5000u, 40u, 1u);
    got = run_loop(30000u, 100u);          /* 3 s */
    sum = ls_stats_summary(&g_engine.st);
    expect(sum.recv == 0u && got == 0u, "dead no recv", sum.recv);
    expect(sum.sent >= 3u, "dead sent>0", sum.sent);
    expect(sum.timeout >= 2u, "dead timeouts", sum.timeout);
    expect(sum.loss_ppm == 1000000u, "dead loss 100%", sum.loss_ppm);
    printf("[ok] dead link sent=%u timeout=%u loss_ppm=%u\n", sum.sent, sum.timeout, sum.loss_ppm);

    /* --- 4. 乱序/重复：把 RSP 的 seq 改掉应被记为 dup --- */
    {
        uint8_t req[PROBE_FRAME_MAX];
        probe_req_t r;
        probe_rsp_t rsp;
        uint32_t t1;

        g_host_now_us = 1000u;
        ls_engine_init(&g_engine, ls_link_ops_get(), 1000u, 5000u, 40u, 1u);
        r.seq = 0u;
        r.t1_us = 0u;
        r.cfg_id = 1u;
        r.frame_len = 40u;
        /* 手动发一包，构造一个在途请求 */
        r.seq = (uint16_t)(g_engine.seq + 1u);
        r.t1_us = (uint32_t)g_host_now_us;
        (void)probe_build_req(req, sizeof(req), &r);
        g_engine.seq = r.seq;
        g_engine.st.sent++;
        ls_engine_track(&g_engine, r.seq, r.t1_us);
        t1 = r.t1_us;

        /* 发一个 seq 不对的 RSP */
        rsp.seq = (uint16_t)(r.seq + 5u);
        rsp.t1_us = t1;
        rsp.t2_us = t1 + 1u;
        rsp.t3_us = t1 + 2u;
        rsp.cfg_id = 1u;
        {
            uint8_t frame[PROBE_RSP_LEN];
            (void)probe_build_rsp(frame, sizeof(frame), &rsp);
            ls_on_frame(frame, PROBE_RSP_LEN, t1 + 10u);
        }
        expect(g_engine.st.dup == 1u, "stale rsp counted as dup", g_engine.st.dup);
        expect(g_engine.pending == 1u, "pending kept after dup", g_engine.pending);
        /* 被拒帧的现场快照必须被填上：seq 是帧里的、pend 是在途的、age 是这一帧的真实 RTT */
        expect(g_engine.rej_count == 1u, "rej snapshot taken", g_engine.rej_count);
        expect(g_engine.rej_seq == (uint16_t)(r.seq + 5u), "rej seq from frame", g_engine.rej_seq);
        expect(g_engine.rej_pend == r.seq, "rej pend from engine", g_engine.rej_pend);
        expect(g_engine.rej_age_us == 10u, "rej age = t4-t1", g_engine.rej_age_us);
        expect(g_engine.rej_bproc_us == 1u, "rej bproc = t3-t2", g_engine.rej_bproc_us);
    }

    /* --- 5. 流水线：窗口放开后，对端不回时的发包能力应成倍上升 --- */
    {
        uint32_t sent_stop;
        uint32_t sent_win;
        uint32_t to_stop;

        g_host_now_us = 1000u;
        ls_engine_init(&g_engine, &g_dead_link, 1000u, 5000u, 40u, 1u);
        (void)run_loop(30000u, 100u);          /* 3 s：停等 */
        sent_stop = g_engine.st.sent;
        to_stop = g_engine.st.timeout;

        g_host_now_us = 1000u;
        ls_engine_init(&g_engine, &g_dead_link, 1000u, 5000u, 40u, 1u);
        ls_engine_set_window(&g_engine, 4u);
        expect(g_engine.window == 4u, "window set 4", g_engine.window);
        (void)run_loop(30000u, 100u);
        sent_win = g_engine.st.sent;

        /* 同样的超时下，4 个在途 → 每 5ms 最多 4 包，至少是停等的 2 倍 */
        expect(sent_win >= sent_stop * 2u, "window 4 lifts send rate", sent_win);
        expect(g_engine.st.timeout >= to_stop, "window timeouts counted", g_engine.st.timeout);
        expect(g_engine.in_flight <= 4u, "in_flight <= window", g_engine.in_flight);
        printf("[ok] window sent=%u vs stop=%u\n", sent_win, sent_stop);

        /* 窗口上限夹取 */
        ls_engine_init(&g_engine, &g_dead_link, 1000u, 5000u, 40u, 1u);
        ls_engine_set_window(&g_engine, 0u);
        expect(g_engine.window == 1u, "window 0 clamps to 1", g_engine.window);
        ls_engine_set_window(&g_engine, 200u);
        expect(g_engine.window == LS_WINDOW_MAX, "window clamps to max", g_engine.window);
    }

    printf(g_fail == 0 ? "[RESULT] engine tests PASS\n" : "[RESULT] engine tests FAIL\n");
    return g_fail;
}
