/*
 * SLE被动广播监测：持续seek，只记录公开报告，绝不连接、配对或发现服务。
 * seek回调运行在SLE service线程，不能阻塞。回调第一行取板端时间戳，随后只复制
 * 固定大小快照到消息队列；十六进制格式化和UART打印全部放在任务上下文。
 */
#include <stddef.h>
#include "common_def.h"
#include "soc_osal.h"
#include "securec.h"
#include "sle_device_discovery.h"
#include "sle_errcode.h"
#include "watchdog.h"
#include "linkscope.h"

#ifndef CONFIG_LINK_SCOPE_MONITOR_SEEK_INTERVAL
#define CONFIG_LINK_SCOPE_MONITOR_SEEK_INTERVAL 100
#endif
#ifndef CONFIG_LINK_SCOPE_MONITOR_SEEK_WINDOW
#define CONFIG_LINK_SCOPE_MONITOR_SEEK_WINDOW 100
#endif
#ifndef CONFIG_LINK_SCOPE_MONITOR_FILTER_DUP
#define CONFIG_LINK_SCOPE_MONITOR_FILTER_DUP 0
#endif

#define LS_MON_CORE_READY_MS       3000u
#define LS_MON_QUEUE_LEN           16u
#define LS_MON_DATA_MAX            255u
#define LS_MON_SUM_EVERY           200u
#define LS_MON_INVALID_RSSI        0x7Fu
#define LS_MON_QUEUE_WAIT_TICKS    100u

typedef struct {
    uint32_t seq;
    uint32_t t_us;
    uint32_t addr_id;
    uint8_t event_type;
    uint8_t rssi_raw;
    uint8_t data_status;
    uint8_t data_length;
    uint8_t data[LS_MON_DATA_MAX];
} ls_monitor_report_t;

static unsigned long g_mon_queue_id;
static volatile uint8_t g_mon_queue_ok;
static volatile uint32_t g_mon_seq;
static volatile uint32_t g_mon_drop;
static volatile uint32_t g_mon_invalid_rssi;

static uint32_t addr_id(const sle_addr_t *addr)
{
    uint32_t h = 2166136261u;
    uint32_t i;

    if (addr == NULL) {
        return 0u;
    }
    h = (h ^ (uint32_t)addr->type) * 16777619u;
    for (i = 0u; i < SLE_ADDR_LEN; i++) {
        h = (h ^ (uint32_t)addr->addr[i]) * 16777619u;
    }
    return h;
}

static void start_seek(void)
{
    sle_seek_param_t param = {0};
    errcode_t rc;

    param.own_addr_type = 0u;
    param.filter_duplicates = (CONFIG_LINK_SCOPE_MONITOR_FILTER_DUP != 0) ? 1u : 0u;
    param.seek_filter_policy = 0u;
    param.seek_phys = 1u;
    param.seek_type[0] = 1u;
    param.seek_interval[0] = (uint16_t)CONFIG_LINK_SCOPE_MONITOR_SEEK_INTERVAL;
    param.seek_window[0] = (uint16_t)CONFIG_LINK_SCOPE_MONITOR_SEEK_WINDOW;
    rc = sle_set_seek_param(&param);
    osal_printk("%s [MON] set seek rc=0x%x\r\n", LS_LOG, (uint32_t)rc);
    if (rc == ERRCODE_SLE_SUCCESS) {
        rc = sle_start_seek();
        osal_printk("%s [MON] start seek rc=0x%x\r\n", LS_LOG, (uint32_t)rc);
    }
}

static void on_sle_enabled(errcode_t status)
{
    osal_printk("%s [MON] sle enabled status=0x%x\r\n", LS_LOG, (uint32_t)status);
    if (status == ERRCODE_SLE_SUCCESS) {
        start_seek();
    }
}

static void on_seek_enabled(errcode_t status)
{
    osal_printk("%s [MON] seek enabled status=0x%x\r\n", LS_LOG, (uint32_t)status);
}

static void on_seek_disabled(errcode_t status)
{
    osal_printk("%s [MON] seek disabled status=0x%x\r\n", LS_LOG, (uint32_t)status);
}

static void on_seek_result(sle_seek_result_info_t *result)
{
    uint32_t t_us = ls_now_us();
    ls_monitor_report_t report = {0};
    uint8_t len;

    if (result == NULL || g_mon_queue_ok == 0u) {
        g_mon_drop++;
        return;
    }
    len = result->data_length;
    report.seq = ++g_mon_seq;
    report.t_us = t_us;
    report.addr_id = addr_id(&result->addr);
    report.event_type = result->event_type;
    report.rssi_raw = result->rssi;
    report.data_status = result->data_status;
    report.data_length = len;
    if (len > 0u && result->data != NULL) {
        if (memcpy_s(report.data, sizeof(report.data), result->data, len) != EOK) {
            g_mon_drop++;
            return;
        }
    }
    if (report.rssi_raw == LS_MON_INVALID_RSSI) {
        g_mon_invalid_rssi++;
    }
    if (osal_msg_queue_write_copy(g_mon_queue_id, &report, (unsigned int)sizeof(report), 0) != 0) {
        g_mon_drop++;
    }
}

static void print_report(const ls_monitor_report_t *report)
{
    static const char hex[] = "0123456789abcdef";
    char payload_hex[(LS_MON_DATA_MAX * 2u) + 1u];
    uint32_t i;
    int rssi;

    for (i = 0u; i < (uint32_t)report->data_length; i++) {
        payload_hex[i * 2u] = hex[(report->data[i] >> 4) & 0x0Fu];
        payload_hex[(i * 2u) + 1u] = hex[report->data[i] & 0x0Fu];
    }
    payload_hex[(uint32_t)report->data_length * 2u] = '\0';
    rssi = (report->rssi_raw == LS_MON_INVALID_RSSI) ? 127 : (int)(int8_t)report->rssi_raw;
    osal_printk("ADV,%u,%u,%u,%08x,%d,%u,%u,%s\r\n", report->seq, report->t_us,
        (uint32_t)report->event_type, report->addr_id, rssi, (uint32_t)report->data_status,
        (uint32_t)report->data_length, payload_hex);
    if ((report->seq % LS_MON_SUM_EVERY) == 0u) {
        osal_printk("# MON n=%u drop=%u invalid_rssi=%u\r\n", report->seq,
            (uint32_t)g_mon_drop, (uint32_t)g_mon_invalid_rssi);
    }
}

void ls_monitor_run(void)
{
    static sle_announce_seek_callbacks_t callbacks;
    ls_monitor_report_t report;
    uint32_t report_len;
    errcode_t rc;

    if ((uint32_t)CONFIG_LINK_SCOPE_MONITOR_SEEK_WINDOW >
        (uint32_t)CONFIG_LINK_SCOPE_MONITOR_SEEK_INTERVAL) {
        osal_printk("%s [MON] invalid seek window=%u > interval=%u\r\n", LS_LOG,
            (uint32_t)CONFIG_LINK_SCOPE_MONITOR_SEEK_WINDOW,
            (uint32_t)CONFIG_LINK_SCOPE_MONITOR_SEEK_INTERVAL);
        return;
    }
    if (osal_msg_queue_create("ls_mon_q", (unsigned short)LS_MON_QUEUE_LEN, &g_mon_queue_id, 0,
                              (unsigned int)sizeof(ls_monitor_report_t)) != 0) {
        osal_printk("%s [MON] queue create failed\r\n", LS_LOG);
        return;
    }
    g_mon_queue_ok = 1u;
    osal_printk("%s link=sle-passive-seek seek_intv=%u seek_win=%u filter_dup=%u\r\n", LS_LOG,
        (uint32_t)CONFIG_LINK_SCOPE_MONITOR_SEEK_INTERVAL,
        (uint32_t)CONFIG_LINK_SCOPE_MONITOR_SEEK_WINDOW,
        (uint32_t)(CONFIG_LINK_SCOPE_MONITOR_FILTER_DUP != 0));
    osal_printk("# ADV,seq,t_us,event,addr_id,rssi_dbm,data_status,data_len,payload_hex\r\n");

    osal_msleep(LS_MON_CORE_READY_MS);
    callbacks.sle_enable_cb = on_sle_enabled;
    callbacks.seek_enable_cb = on_seek_enabled;
    callbacks.seek_result_cb = on_seek_result;
    callbacks.seek_disable_cb = on_seek_disabled;
    (void)sle_announce_seek_register_callbacks(&callbacks);
    rc = enable_sle();
    if (rc != ERRCODE_SUCC) {
        osal_printk("%s [MON] enable_sle failed rc=0x%x\r\n", LS_LOG, (uint32_t)rc);
    }

    while (1) {
        report_len = (uint32_t)sizeof(report);
        if (osal_msg_queue_read_copy(g_mon_queue_id, &report, &report_len, LS_MON_QUEUE_WAIT_TICKS) == 0) {
            print_report(&report);
        }
        (void)uapi_watchdog_kick();
    }
}
