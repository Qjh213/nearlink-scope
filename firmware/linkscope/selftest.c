/*
 * 板端自检实现
 *
 * 同一份代码既能在 H3863 上跑（osal_printk），也能在 PC 上跑（printf），
 * 后者用 -DLS_HOST_TEST 编译，见 test/run_host_test.ps1。
 */
#ifdef LS_HOST_TEST
#include <stdio.h>
#define osal_printk(...) printf(__VA_ARGS__)
#define LS_LOG "[linkscope] "
#else
#include "common_def.h"
#include "soc_osal.h"
#include "linkscope.h"
#endif
#include "probe.h"
#include "stats.h"
#include "selftest.h"

static uint32_t g_pass;
static uint32_t g_fail;

static void check(int cond, const char *name)
{
    if (cond) {
        g_pass++;
    } else {
        g_fail++;
        osal_printk("%s [SELFTEST][FAIL] %s\r\n", LS_LOG, name);
    }
}

static void test_probe(void)
{
    uint8_t buf[PROBE_FRAME_MAX];
    probe_req_t req_in;
    probe_req_t req_out;
    probe_rsp_t rsp_in;
    probe_rsp_t rsp_out;
    int n;

    req_in.seq = 0x1234u;
    req_in.t1_us = 0xDEADBEEFu;
    req_in.cfg_id = 7u;
    req_in.frame_len = 40u;

    n = probe_build_req(buf, (uint16_t)sizeof(buf), &req_in);
    check(n == 40, "build_req length");
    check(buf[0] == PROBE_MAGIC && buf[1] == PROBE_TYPE_REQ, "build_req header");

    check(probe_parse_req(buf, (uint16_t)n, &req_out) == PROBE_OK, "parse_req ok");
    check(req_out.seq == req_in.seq && req_out.t1_us == req_in.t1_us &&
          req_out.cfg_id == req_in.cfg_id && req_out.frame_len == 40u, "parse_req fields");

    /* 长度夹取 */
    req_in.frame_len = 5u;
    n = probe_build_req(buf, (uint16_t)sizeof(buf), &req_in);
    check(n == (int)PROBE_FRAME_MIN, "build_req min clamp");
    req_in.frame_len = 1000u;
    n = probe_build_req(buf, (uint16_t)sizeof(buf), &req_in);
    check(n == (int)PROBE_FRAME_MAX, "build_req max clamp");

    /* 坏帧 */
    req_in.frame_len = 40u;
    n = probe_build_req(buf, (uint16_t)sizeof(buf), &req_in);
    buf[0] = 0x00u;
    check(probe_parse_req(buf, (uint16_t)n, &req_out) == PROBE_ERR_MAGIC, "reject bad magic");
    buf[0] = PROBE_MAGIC;
    buf[1] = PROBE_TYPE_RSP;
    check(probe_parse_req(buf, (uint16_t)n, &req_out) == PROBE_ERR_TYPE, "reject bad type");
    check(probe_parse_req(buf, 4u, &req_out) == PROBE_ERR_LEN, "reject short frame");

    /* RSP */
    rsp_in.seq = 0x00FFu;
    rsp_in.t1_us = 111u;
    rsp_in.t2_us = 222u;
    rsp_in.t3_us = 0u;
    rsp_in.cfg_id = 9u;
    n = probe_build_rsp(buf, (uint16_t)sizeof(buf), &rsp_in);
    check(n == (int)PROBE_RSP_LEN, "build_rsp length");
    probe_patch_u32(buf, PROBE_OFF_T3, 333u);
    check(probe_parse_rsp(buf, (uint16_t)n, &rsp_out) == PROBE_OK, "parse_rsp ok");
    check(rsp_out.seq == rsp_in.seq && rsp_out.t1_us == rsp_in.t1_us &&
          rsp_out.t2_us == rsp_in.t2_us && rsp_out.t3_us == 333u &&
          rsp_out.cfg_id == rsp_in.cfg_id, "parse_rsp fields");
    check(probe_parse_rsp(buf, 19u, &rsp_out) == PROBE_ERR_LEN, "reject rsp length");
}

static void test_stats(void)
{
    ls_stats_t st;
    ls_summary_t sum;
    uint32_t i;

    /* 桶索引单调 */
    check(ls_hist_index(1u) < ls_hist_index(2u), "hist index 1<2");
    check(ls_hist_index(1000u) < ls_hist_index(2000u), "hist index 1k<2k");
    check(ls_hist_index(1000u) < LS_HIST_BUCKETS, "hist index in range");
    check(ls_hist_index(100000000u) == LS_HIST_BUCKETS - 1u, "hist index clamp");

    /* 恒定 1000us：均值/中位数应落在一个桶内（误差 < 6%） */
    ls_stats_reset(&st);
    for (i = 0; i < 1000u; i++) {
        ls_stats_on_rtt(&st, 1000u);
    }
    sum = ls_stats_summary(&st);
    check(sum.n == 1000u, "stats n");
    check(sum.mean_us >= 995u && sum.mean_us <= 1005u, "stats mean");
    check(sum.p50_us >= 940u && sum.p50_us <= 1060u, "stats p50 within bucket");
    check(sum.p99_us >= 940u && sum.p99_us <= 1080u, "stats p99 within bucket");
    check(sum.jitter_us == 0u, "stats jitter constant");
    check(sum.p50_us <= sum.p99_us && sum.p99_us <= sum.max_us, "stats quantile order");

    /* 抖动：交替 1000/2000 -> 平均绝对差分 = 1000 */
    ls_stats_reset(&st);
    for (i = 0; i < 100u; i++) {
        ls_stats_on_rtt(&st, (i & 1u) ? 2000u : 1000u);
    }
    sum = ls_stats_summary(&st);
    check(sum.jitter_us >= 990u && sum.jitter_us <= 1010u, "stats jitter 1000");

    /* 加分位：990 个 1ms + 10 个 10ms -> p99.9 应跳到 10ms 档 */
    ls_stats_reset(&st);
    for (i = 0; i < 990u; i++) {
        ls_stats_on_rtt(&st, 1000u);
    }
    for (i = 0; i < 10u; i++) {
        ls_stats_on_rtt(&st, 10000u);
    }
    sum = ls_stats_summary(&st);
    check(sum.p999_us >= 9000u, "stats p99.9 tail");
    check(sum.max_us == 10000u, "stats max");

    /* 丢包 ppm */
    check(ls_loss_ppm(1000u, 990u) == 10000u, "loss ppm 1%");
    check(ls_loss_ppm(0u, 0u) == 0u, "loss ppm sent=0");

    /* 分段累计 */
    ls_stats_reset(&st);
    ls_stats_on_rtt(&st, 1000u);
    ls_stats_on_rtt(&st, 2000u);
    ls_stats_on_segments(&st, 100u, 300u);
    ls_stats_on_segments(&st, 200u, 500u);
    sum = ls_stats_summary(&st);
    check(sum.bproc_mean_us == 150u, "bproc mean");
    check(sum.bproc_max_us == 200u, "bproc max");
    check(sum.down_mean_us == 400u, "dlt mean");
    check(sum.down_max_us == 500u, "dlt max");
}

int ls_selftest_run(void)
{
    g_pass = 0;
    g_fail = 0;

    test_probe();
    test_stats();

    osal_printk("%s [SELFTEST] pass=%u fail=%u -> %s\r\n",
        LS_LOG, g_pass, g_fail, (g_fail == 0u) ? "PASS" : "FAIL");
    return (int)g_fail;
}
