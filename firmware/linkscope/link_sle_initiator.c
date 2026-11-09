/*
 * SLE 发起端（A）：seek -> connect -> pair -> MTU -> 发现属性 -> 写入 / 收 RSP。
 *
 * 与官方 sle_uart 客户端样例的差别：
 *   - 去掉每次写确认都打印（会污染被测对象的调度）；
 *   - 收包回调第一行取 t4，然后直接进 engine，不在回调里打印；
 *   - 主动读一次 RSSI（能力确认项 R3）。
 */
#include <stddef.h>
#include "common_def.h"
#include "soc_osal.h"
#include "securec.h"
#include "sle_device_discovery.h"
#include "sle_connection_manager.h"
#include "sle_ssap_client.h"
#include "sle_errcode.h"
#include "linkscope.h"
#include "link_ops.h"

#define SLE_TARGET_NAME         "linkscope"
#define SLE_MTU_SIZE            520u
#define SLE_CORE_READY_MS       3000u
#define SLE_SEEK_INTERVAL       100u
#define SLE_SEEK_WINDOW         100u
#define SLE_INVALID_RSSI        0x7Fu

/* ---- 诊断用（W1 定位 RTT 的来源） ---- */
#define LS_CFM_PRINT_N          3u          /* 前 3 个写确认单独打印，看单次数值 */
#define LS_INTV_SWEEP_US        20000000u   /* 每 20s 换一个连接间隔试，日志里能看到 rc 和实际给的值 */
#define LS_INTV_LATENCY         0u
#define LS_INTV_SUPERVISION     0x0C80u     /* 单位 10ms -> 32s */

/* 固定连接间隔（slot，1 slot = 125µs）。0 = 启用下方扫描；正式采数据时设成具体值，配置才可复现 */
#ifndef CONFIG_LINK_SCOPE_INTV_SLOT
#define CONFIG_LINK_SCOPE_INTV_SLOT 0
#endif

/* 依次尝试的连接间隔（单位 slot=125us）：0.75 / 3 / 6 / 8 / 12.5 ms */
static const uint16_t g_intv_sweep[] = {0x06u, 0x18u, 0x30u, 0x40u, 0x64u};
#define LS_INTV_SWEEP_N  ((uint8_t)(sizeof(g_intv_sweep) / sizeof(g_intv_sweep[0])))

static uint16_t g_conn_id;
static sle_addr_t g_remote_addr;
static ssapc_write_param_t g_write_param;
static volatile uint8_t g_ready;
static uint8_t g_peer_rssi = SLE_INVALID_RSSI;

static volatile uint32_t g_cfm_n;       /* 写确认次数 */
static volatile uint32_t g_cfm_sum_us;  /* 写确认时延累计（A 本地时钟） */
static volatile uint32_t g_cfm_max_us;
static volatile uint32_t g_send_us;     /* 最近一次 Write Request 的时刻 */
static uint32_t g_ready_us;             /* 链路就绪时刻 */
static uint32_t g_last_tune_us;         /* 上次发连接的参数更新请求的时刻 */
static uint8_t g_sweep_idx;
static volatile uint16_t g_granted_intv = 0xFFFFu;   /* 协议栈实际给的间隔 */

static int contains_name(const uint8_t *data, uint8_t len, const char *name)
{
    uint32_t nlen = 0;
    uint32_t i;

    if (data == NULL || name == NULL) {
        return 0;
    }
    while (name[nlen] != '\0') {
        nlen++;
    }
    if ((uint32_t)len < nlen) {
        return 0;
    }
    for (i = 0; i + nlen <= (uint32_t)len; i++) {
        uint32_t k;
        for (k = 0; k < nlen; k++) {
            if (data[i + k] != (uint8_t)name[k]) {
                break;
            }
        }
        if (k == nlen) {
            return 1;
        }
    }
    return 0;
}

static void start_seek(void)
{
    sle_seek_param_t param = {0};

    param.own_addr_type = 0;
    param.filter_duplicates = 0;
    param.seek_filter_policy = 0;
    param.seek_phys = 1;
    param.seek_type[0] = 1;
    param.seek_interval[0] = SLE_SEEK_INTERVAL;
    param.seek_window[0] = SLE_SEEK_WINDOW;
    (void)sle_set_seek_param(&param);
    (void)sle_start_seek();
}

static void on_sle_enabled(errcode_t status)
{
    osal_printk("%s [A] sle enabled status=0x%x, start seek\r\n", LS_LOG, (uint32_t)status);
    start_seek();
}

static void on_seek_enabled(errcode_t status)
{
    if (status != 0) {
        osal_printk("%s [A] seek enable failed 0x%x\r\n", LS_LOG, (uint32_t)status);
    }
}

static void on_seek_result(sle_seek_result_info_t *result)
{
    if (result == NULL) {
        return;
    }
    if (contains_name(result->data, result->data_length, SLE_TARGET_NAME) == 0) {
        return;
    }
    g_peer_rssi = result->rssi;
    (void)memcpy_s(&g_remote_addr, sizeof(g_remote_addr), &result->addr, sizeof(sle_addr_t));
    osal_printk("%s [A] found %s rssi=%d dBm, stop seek\r\n", LS_LOG, SLE_TARGET_NAME,
        (g_peer_rssi == SLE_INVALID_RSSI) ? 127 : (int)(int8_t)g_peer_rssi);
    (void)sle_stop_seek();
}

static void on_seek_disabled(errcode_t status)
{
    if (status != 0) {
        osal_printk("%s [A] seek disable failed 0x%x\r\n", LS_LOG, (uint32_t)status);
        return;
    }
    (void)sle_remove_paired_remote_device(&g_remote_addr);
    (void)sle_connect_remote_device(&g_remote_addr);
}

static void on_connect_state(uint16_t conn_id, const sle_addr_t *addr,
                             sle_acb_state_t conn_state, sle_pair_state_t pair_state,
                             sle_disc_reason_t disc_reason)
{
    unused(addr);
    if (conn_state == SLE_ACB_STATE_CONNECTED) {
        g_conn_id = conn_id;
        osal_printk("%s [A] connected conn_id=0x%x pair_state=%u\r\n", LS_LOG, conn_id, (uint32_t)pair_state);
        if (pair_state == SLE_PAIR_NONE) {
            (void)sle_pair_remote_device(&g_remote_addr);
        }
    } else if (conn_state == SLE_ACB_STATE_DISCONNECTED) {
        g_ready = 0;
        osal_printk("%s [A] disconnected reason=0x%x, re-seek\r\n", LS_LOG, (uint32_t)disc_reason);
        (void)sle_remove_paired_remote_device(&g_remote_addr);
        start_seek();
    }
}

static void on_pair_complete(uint16_t conn_id, const sle_addr_t *addr, errcode_t status)
{
    unused(addr);
    if (status == 0) {
        ssap_exchange_info_t info = {0};
        info.mtu_size = (uint16_t)SLE_MTU_SIZE;
        info.version = 1;
        (void)ssapc_exchange_info_req(0, conn_id, &info);
    } else {
        osal_printk("%s [A] pair failed 0x%x\r\n", LS_LOG, (uint32_t)status);
    }
}

static void on_rssi(uint16_t conn_id, int8_t rssi, errcode_t status)
{
    unused(conn_id);
    if (status == 0) {
        osal_printk("%s [A] rssi=%d dBm\r\n", LS_LOG, (int)rssi);
    }
}

static void on_exchange_info(uint8_t client_id, uint16_t conn_id, ssap_exchange_info_t *param, errcode_t status)
{
    ssapc_find_structure_param_t find_param = {0};

    unused(client_id);
    if (status != 0 || param == NULL) {
        osal_printk("%s [A] mtu exchange failed 0x%x\r\n", LS_LOG, (uint32_t)status);
        return;
    }
    osal_printk("%s [A] mtu=%u\r\n", LS_LOG, param->mtu_size);
    find_param.type = SSAP_FIND_TYPE_PROPERTY;
    find_param.start_hdl = 1;
    find_param.end_hdl = 0xFFFF;
    (void)ssapc_find_structure(0, conn_id, &find_param);
}

static void on_find_service(uint8_t client_id, uint16_t conn_id, ssapc_find_service_result_t *service,
                            errcode_t status)
{
    unused(client_id);
    unused(conn_id);
    unused(service);
    unused(status);
}

static void on_find_property(uint8_t client_id, uint16_t conn_id, ssapc_find_property_result_t *property,
                             errcode_t status)
{
    unused(client_id);
    unused(conn_id);
    if (status != 0 || property == NULL) {
        return;
    }
    g_write_param.handle = property->handle;
    g_write_param.type = SSAP_PROPERTY_TYPE_VALUE;
    g_ready_us = ls_now_us();
    g_last_tune_us = g_ready_us;
    if ((uint32_t)CONFIG_LINK_SCOPE_INTV_SLOT != 0u) {
        /* 固定档位：链路一就绪就去谈参数，不等 20s。
         * 实测代价：默认 12.5ms 间隔下停止等 40Hz 探针会积压 600+ 包，
         * 前 25s 全部超时，白扔一段数据。 */
        g_last_tune_us = (uint32_t)(g_ready_us - LS_INTV_SWEEP_US);
    }
    g_ready = 1u;
    osal_printk("%s [A] property handle=0x%x, link ready\r\n", LS_LOG, property->handle);
}

static void on_find_complete(uint8_t client_id, uint16_t conn_id, ssapc_find_structure_result_t *result,
                             errcode_t status)
{
    unused(client_id);
    unused(conn_id);
    unused(result);
    unused(status);
}

static void on_write_cfm(uint8_t client_id, uint16_t conn_id, ssapc_write_result_t *result, errcode_t status)
{
#if defined(CONFIG_LINK_SCOPE_SLE_WRITE_REQUEST)
    uint32_t now = ls_now_us();
    uint32_t lat = (uint32_t)(now - g_send_us);   /* 写请求 -> 写确认，A 本地时钟，精确 */

    unused(client_id);
    unused(conn_id);
    unused(result);
    if (status != 0) {
        osal_printk("%s [A] write cfm error 0x%x\r\n", LS_LOG, (uint32_t)status);
        return;
    }
    g_cfm_n++;
    g_cfm_sum_us += lat;
    if (lat > g_cfm_max_us) {
        g_cfm_max_us = lat;
    }
    if (g_cfm_n <= LS_CFM_PRINT_N) {
        osal_printk("%s [A] write cfm #%u lat=%u us\r\n", LS_LOG, (uint32_t)g_cfm_n, lat);
    }
#else
    unused(client_id);
    unused(conn_id);
    unused(result);
    unused(status);
#endif
}

/* 连接参数更新结果：granted interval 的单位是 slot（125us） */
static void on_param_update(uint16_t conn_id, errcode_t status, const sle_connection_param_update_evt_t *param)
{
    if (status != 0 || param == NULL) {
        osal_printk("%s [A] param update failed 0x%x\r\n", LS_LOG, (uint32_t)status);
        return;
    }
    g_granted_intv = param->interval;
    osal_printk("%s [A] param updated conn=0x%x intv=%u slot (%u us) latency=%u sup=%u\r\n", LS_LOG,
        (uint32_t)conn_id, (uint32_t)param->interval, (uint32_t)param->interval * 125u,
        (uint32_t)param->latency, (uint32_t)param->supervision);
}

static void on_notification(uint8_t client_id, uint16_t conn_id, ssapc_handle_value_t *data, errcode_t status)
{
    static volatile uint8_t seen;
    uint32_t t4 = ls_now_us();      /* 铁律：回调第一行取时间戳 */

    unused(client_id);
    unused(conn_id);
    unused(status);
    if (data == NULL || data->data == NULL || data->data_len == 0u) {
        return;
    }
    if (seen == 0u) {               /* 只打一次，确认链路真的回包了，并留下前 4 字节供比对 */
        seen = 1u;
        osal_printk("%s [A] first rsp: handle=0x%x len=%u status=0x%x b=%02x %02x %02x %02x\r\n", LS_LOG,
            (uint32_t)data->handle, (uint32_t)data->data_len, (uint32_t)status,
            (uint32_t)data->data[0], (uint32_t)data->data[1],
            (uint32_t)data->data[2], (uint32_t)data->data[3]);
    }
    ls_on_frame(data->data, data->data_len, t4);
}

static void on_indication(uint8_t client_id, uint16_t conn_id, ssapc_handle_value_t *data, errcode_t status)
{
    on_notification(client_id, conn_id, data, status);
}

static int sle_start(void)
{
    static sle_announce_seek_callbacks_t seek_cbk;
    static sle_connection_callbacks_t conn_cbk;
    static ssapc_callbacks_t ssapc_cbk;

    /* 等 SLE 协议栈初始化完成；官方样例这里也要等 5s */
    osal_msleep(SLE_CORE_READY_MS);

    seek_cbk.sle_enable_cb = on_sle_enabled;
    seek_cbk.seek_enable_cb = on_seek_enabled;
    seek_cbk.seek_result_cb = on_seek_result;
    seek_cbk.seek_disable_cb = on_seek_disabled;
    (void)sle_announce_seek_register_callbacks(&seek_cbk);

    conn_cbk.connect_state_changed_cb = on_connect_state;
    conn_cbk.pair_complete_cb = on_pair_complete;
    conn_cbk.read_rssi_cb = on_rssi;
    conn_cbk.connect_param_update_cb = on_param_update;
    (void)sle_connection_register_callbacks(&conn_cbk);

    ssapc_cbk.exchange_info_cb = on_exchange_info;
    ssapc_cbk.find_structure_cb = on_find_service;
    ssapc_cbk.ssapc_find_property_cbk = on_find_property;
    ssapc_cbk.find_structure_cmp_cb = on_find_complete;
    ssapc_cbk.write_cfm_cb = on_write_cfm;
    ssapc_cbk.notification_cb = on_notification;
    ssapc_cbk.indication_cb = on_indication;
    (void)ssapc_register_callbacks(&ssapc_cbk);

    if (enable_sle() != ERRCODE_SUCC) {
        osal_printk("%s [A] enable_sle failed\r\n", LS_LOG);
        return -1;
    }
    return 0;
}

static int sle_send(const uint8_t *data, uint16_t len)
{
    if (g_ready == 0u) {
        return -1;
    }
    g_write_param.data = (uint8_t *)data;
    g_write_param.data_len = len;
#if defined(CONFIG_LINK_SCOPE_SLE_WRITE_COMMAND)
    return (ssapc_write_cmd(0, g_conn_id, &g_write_param) == ERRCODE_SLE_SUCCESS) ? 0 : -1;
#else
    g_send_us = ls_now_us();        /* 与写确认配对，量"发出 -> 确认"这一段 */
    return (ssapc_write_req(0, g_conn_id, &g_write_param) == ERRCODE_SLE_SUCCESS) ? 0 : -1;
#endif
}

static uint8_t sle_ready(void)
{
    return g_ready;
}

/*
 * 连上以后每 20s 换一个连接间隔试一次，把 rc 和协议栈实际给的间隔都打出来。
 * 目的：确认 ~24ms 的 RTT 是不是"2 个连接事件"卡出来的——如果是，间隔越小 RTT 越小，
 * 100Hz 就只差把间隔压下去（停止等模式的最小 RTT 天然是 2 个事件）。
 * 放在任务上下文（poll 每 5s 一次），不影响热路径。
 */
static void sle_poll(uint32_t now_ms)
{
    sle_connection_param_update_t req;
    errcode_t rc;
    uint16_t slot;
    uint32_t now = ls_now_us();

    unused(now_ms);
    if (g_ready == 0u) {
        return;
    }
    if (((uint32_t)CONFIG_LINK_SCOPE_INTV_SLOT != 0u) &&
        (g_granted_intv == (uint16_t)CONFIG_LINK_SCOPE_INTV_SLOT)) {
        return;   /* 已经谈到目标间隔，别再周期性打扰链路 */
    }
    if ((uint32_t)(now - g_last_tune_us) < LS_INTV_SWEEP_US) {
        return;
    }
    g_last_tune_us = now;
    if ((uint32_t)CONFIG_LINK_SCOPE_INTV_SLOT != 0u) {
        /* 固定档位：正式采数据时用这个，不带扫描，配置才是可复现的 */
        slot = (uint16_t)CONFIG_LINK_SCOPE_INTV_SLOT;
    } else {
        slot = g_intv_sweep[g_sweep_idx];
        g_sweep_idx = (uint8_t)((g_sweep_idx + 1u) % LS_INTV_SWEEP_N);
    }
    req.conn_id = g_conn_id;
    req.interval_min = slot;
    req.interval_max = slot;
    req.max_latency = LS_INTV_LATENCY;
    req.supervision_timeout = LS_INTV_SUPERVISION;
    rc = sle_update_connect_param(&req);
    /* 注意：真正的 granted 由 on_param_update 回调里那行打印（这里读到的是上一次的值） */
    osal_printk("%s [A] tune slot=0x%x (%u us) rc=0x%x (prev_granted=0x%x)\r\n", LS_LOG,
        (uint32_t)slot, (uint32_t)slot * 125u, (uint32_t)rc, (uint32_t)g_granted_intv);
}

/*
 * 写确认时延统计——**按窗口清零**。
 * 之前是启动以来的累计均值，换了间隔以后几十秒都还原不过来（0x64 那段的历史会把 750us 那段拉平），
 * 根本看不出"这个间隔下 cfm 是多少"。现在每 5s 报一次、报完清零，4 个点正好覆盖一个 20s 的间隔档。
 */
static void sle_dbg(uint32_t *count, uint32_t *avg_us, uint32_t *max_us)
{
    uint32_t n = g_cfm_n;
    uint32_t sum = g_cfm_sum_us;
    uint32_t max = g_cfm_max_us;

    g_cfm_n = 0u;
    g_cfm_sum_us = 0u;
    g_cfm_max_us = 0u;

    if (count != NULL) {
        *count = n;
    }
    if (avg_us != NULL) {
        *avg_us = (n == 0u) ? 0u : (sum / n);
    }
    if (max_us != NULL) {
        *max_us = max;
    }
}

static const link_ops_t g_sle_ops = {
#if defined(CONFIG_LINK_SCOPE_SLE_WRITE_COMMAND)
    .name = "sle-write_cmd",
#else
    .name = "sle-write_req",
#endif
    .start = sle_start,
    .send = sle_send,
    .ready = sle_ready,
    .poll = sle_poll,
    .dbg = sle_dbg,
};

const link_ops_t *ls_link_ops_get(void)
{
    return &g_sle_ops;
}
