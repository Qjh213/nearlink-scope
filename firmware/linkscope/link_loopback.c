/*
 * Loopback 传输层：不占空口，把 REQ 直接喂给同一套"响应端"代码再送回来。
 * 用途 = 噪声底：量出"本工具自身"的路径抖动（组帧、打点、解析、统计、上下文切换）。
 */
#include "common_def.h"
#include "linkscope.h"
#include "probe.h"
#include "link_ops.h"

static uint8_t g_rsp[PROBE_RSP_LEN];

static int loopback_start(void)
{
    return 0;
}

static uint8_t loopback_ready(void)
{
    return 1u;
}

static int loopback_send(const uint8_t *data, uint16_t len)
{
    probe_req_t req;
    probe_rsp_t rsp;
    uint32_t t2;
    uint32_t t3;

    if (probe_parse_req(data, len, &req) != PROBE_OK) {
        return -1;
    }

    /* 模拟 B 端：收到 -> 组帧 -> 发出，全在同一个调用栈里完成 */
    t2 = ls_now_us();
    rsp.seq = req.seq;
    rsp.t1_us = req.t1_us;
    rsp.t2_us = t2;
    rsp.t3_us = 0u;
    rsp.cfg_id = req.cfg_id;
    if (probe_build_rsp(g_rsp, (uint16_t)sizeof(g_rsp), &rsp) != (int)PROBE_RSP_LEN) {
        return -1;
    }
    t3 = ls_now_us();
    probe_patch_u32(g_rsp, PROBE_OFF_T3, t3);

    ls_on_frame(g_rsp, PROBE_RSP_LEN, ls_now_us());
    return 0;
}

static const link_ops_t g_loopback_ops = {
    .name = "loopback",
    .start = loopback_start,
    .send = loopback_send,
    .ready = loopback_ready,
};

const link_ops_t *ls_link_ops_get(void)
{
    return &g_loopback_ops;
}
