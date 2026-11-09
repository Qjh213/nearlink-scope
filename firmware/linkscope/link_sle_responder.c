/*
 * SLE 响应端（B）：收 REQ -> 打 t2 -> 组 RSP -> 打 t3 -> 立即回发。
 *
 * 铁律：这个回调里不打印、不转发、不 malloc。
 * 底座用的是官方 sle_uart 的 server/adv（已随仓库拷贝到 ./sle/），
 * 但写回调换成了这里的最小实现。
 */
#include <stddef.h>
#include <string.h>
#include "common_def.h"
#include "soc_osal.h"
#include "securec.h"
#include "sle_ssap_server.h"
#include "sle_device_discovery.h"
#include "sle_errcode.h"
#include "linkscope.h"
#include "link_ops.h"
#include "sle/sle_uart_server.h"

#define LS_ADV_HANDLE           1u
#define LS_START_DELAY_MS       2000u
#define LS_QUEUE_LEN            5u
#define LS_QUEUE_ITEM_SIZE      32u
#define LS_HB_TICKS             50u    /* 心跳间隔：50 tick × 20ms = 1s */
#define LS_HB_FIRST_PRINTS      6u     /* 前 6 次无条件打印，便于确认"一个 REQ 都没收到" */
#define LS_DISCONNECT_MSG       "sle_dis_connect"

static unsigned long g_queue_id;
static uint8_t g_queue_ok;
static uint8_t g_rsp_buf[PROBE_RSP_LEN];

/* 诊断计数：回调里只自增，打印一律放到任务上下文（见 resp_hb） */
static volatile uint32_t g_dbg_req;
static volatile uint32_t g_dbg_bad;
static volatile uint32_t g_dbg_rsp;
static volatile int32_t  g_dbg_err;

static void resp_read_cb(uint8_t server_id, uint16_t conn_id, ssaps_req_read_cb_t *req, errcode_t status)
{
    unused(server_id);
    unused(conn_id);
    unused(req);
    unused(status);
}

static void resp_write_cb(uint8_t server_id, uint16_t conn_id, ssaps_req_write_cb_t *req, errcode_t status)
{
    probe_req_t in;
    probe_rsp_t out;
    errcode_t rc;
    uint32_t t2;
    uint32_t t3;

    unused(server_id);
    unused(conn_id);
    if (req == NULL || req->value == NULL || status != 0) {
        g_dbg_bad++;
        return;
    }

    t2 = ls_now_us();                    /* 铁律：第一行取时间戳，计数放在它后面 */
    g_dbg_req++;
    if (probe_parse_req(req->value, req->length, &in) != PROBE_OK) {
        g_dbg_bad++;
        return;
    }
    out.seq = in.seq;
    out.t1_us = in.t1_us;
    out.t2_us = t2;
    out.t3_us = 0u;
    out.cfg_id = in.cfg_id;
    if (probe_build_rsp(g_rsp_buf, (uint16_t)sizeof(g_rsp_buf), &out) != (int)PROBE_RSP_LEN) {
        g_dbg_bad++;
        return;
    }
    t3 = ls_now_us();
    probe_patch_u32(g_rsp_buf, PROBE_OFF_T3, t3);

    rc = sle_uart_server_send_report_by_handle(g_rsp_buf, PROBE_RSP_LEN);
    if (rc != ERRCODE_SLE_SUCCESS) {
        g_dbg_err = (int32_t)rc;
    } else {
        g_dbg_rsp++;
    }
}

static void resp_hb(uint32_t *last_req, uint32_t *prints)
{
    if (g_dbg_req == *last_req && *prints >= LS_HB_FIRST_PRINTS) {
        return;                          /* 没动静就不再刷屏 */
    }
    *last_req = g_dbg_req;
    if (*prints < LS_HB_FIRST_PRINTS) {
        (*prints)++;
    }
    osal_printk("%s [B] hb req=%u bad=%u rsp=%u lasterr=0x%x\r\n", LS_LOG,
        (uint32_t)g_dbg_req, (uint32_t)g_dbg_bad, (uint32_t)g_dbg_rsp, (uint32_t)g_dbg_err);
}

static void resp_msg_queue(uint8_t *buffer_addr, uint16_t buffer_size)
{
    if (g_queue_ok != 0u) {
        (void)osal_msg_queue_write_copy(g_queue_id, (void *)buffer_addr, (unsigned int)buffer_size, 0);
    }
}

void ls_responder_run(void)
{
    uint8_t event[LS_QUEUE_ITEM_SIZE];
    uint32_t event_len;
    uint32_t hb_last_req = 0xFFFFFFFFu;
    uint32_t hb_prints = 0u;

    osal_printk("%s [B] responder booting, wait SLE stack %ums\r\n", LS_LOG, LS_START_DELAY_MS);
    osal_msleep(LS_START_DELAY_MS);

    if (osal_msg_queue_create("ls_resp_q", (unsigned short)LS_QUEUE_LEN, &g_queue_id, 0,
                              LS_QUEUE_ITEM_SIZE) == 0) {
        g_queue_ok = 1u;
    } else {
        osal_printk("%s [B] msg queue create failed, no auto re-announce\r\n", LS_LOG);
    }
    sle_uart_server_register_msg(resp_msg_queue);
    if (sle_uart_server_init(resp_read_cb, resp_write_cb) != ERRCODE_SLE_SUCCESS) {
        osal_printk("%s [B] server init failed\r\n", LS_LOG);
    } else {
        osal_printk("%s [B] ready, waiting for REQ (name=%s)\r\n", LS_LOG, "linkscope");
    }

    while (1) {
        if (g_queue_ok == 0u) {
            osal_msleep(1000);
        } else {
            event_len = (uint32_t)sizeof(event);
            (void)memset(event, 0, sizeof(event));
            if (osal_msg_queue_read_copy(g_queue_id, (void *)event, &event_len, LS_HB_TICKS) == 0) {
                if (strncmp((const char *)event, LS_DISCONNECT_MSG, sizeof(LS_DISCONNECT_MSG)) == 0) {
                    osal_printk("%s [B] peer gone, re-announce\r\n", LS_LOG);
                    (void)sle_start_announce(LS_ADV_HANDLE);
                }
            }
        }
        resp_hb(&hb_last_req, &hb_prints);   /* 一个心跳周期（1s）一次，静默后自动闭嘴 */
    }
}
