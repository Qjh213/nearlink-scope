/*
 * BLE 发起端（A）：扫名字 -> 连 -> 换 MTU -> 发现特征/CCCD -> 调参 -> 发 REQ -> 等 notify。
 *
 * 与 SLE 版的对照点：
 * M2 通过 Kconfig 分别选择 Write Request / Write Command，只与同语义 SLE 结果比较。
 */
#include <stddef.h>
#include <string.h>
#include "common_def.h"
#include "soc_osal.h"
#include "securec.h"
#include "errcode.h"
#include "bts_def.h"
#include "bts_gatt_stru.h"
#include "bts_gatt_client.h"
#include "bts_le_gap.h"
#include "linkscope.h"
#include "link_ops.h"

#define LS_BLE_CLIENT_UUID      0xFE30u
#define LS_BLE_TX_UUID          0xFE31u   /* 对端 notify 的特征（RSP） */
#define LS_BLE_RX_UUID          0xFE32u   /* 对端 write 的特征（REQ） */
#define LS_BLE_CCCD_UUID        0x2902u
#define LS_BLE_MTU              247u
#define LS_BLE_TUNE_MS          5000u
#define LS_BLE_SUP_TIMEOUT      500u      /* 500 × 10ms = 5s */
#define LS_BLE_START_DELAY_MS   5000u     /* 官方 ble_uart 样例等待 SLE/BT 栈初始化 */
#define LS_BLE_REG_RETRY_MS     500u
#define LS_BLE_REG_RETRY_MAX    10u

#ifndef CONFIG_LINK_SCOPE_BLE_INTV_UNIT
#define CONFIG_LINK_SCOPE_BLE_INTV_UNIT 6
#endif

static const char g_ble_peer_name[] = "linkscope";
static const uint8_t g_ble_client_addr[BD_ADDR_LEN] = { 0x12, 0x22, 0x33, 0x44, 0x55, 0x66 };

static uint8_t g_client_id = 0u;
static uint16_t g_conn_id = 0u;
static uint16_t g_write_handle = 0u;      /* 对端 RX 特征：我们写这里 */
static uint16_t g_notify_handle = 0u;     /* 对端 TX 特征：对端 notify 这里 */
static uint16_t g_cccd_handle = 0u;
static volatile uint8_t g_ready = 0u;
static volatile uint8_t g_transport_ready = 0u; /* 特征发现 + CCCD 写确认完成 */
static volatile uint8_t g_scanning = 0u;
static volatile int8_t g_rssi = 0;
static volatile uint8_t g_rssi_valid = 0u;
static volatile uint8_t g_cccd_written = 0u;
static volatile uint8_t g_tune_inflight = 0u;
static uint8_t g_cccd_enable[2] = { 0x01u, 0x00u };

/* Write Request 的协议层写确认统计；Write Command 不具有这项语义。 */
static volatile uint32_t g_cfm_n;
static volatile uint32_t g_cfm_print_n;
static volatile uint32_t g_cfm_sum_us;
static volatile uint32_t g_cfm_max_us;
static uint32_t g_send_us;

static uint32_t g_last_tune_ms;
static uint16_t g_granted_intv = 0xFFFFu;

static void ble_try_enable_notify(void)
{
    gattc_handle_value_t hv;

    if (g_write_handle == 0u || g_notify_handle == 0u || g_cccd_handle == 0u || g_cccd_written != 0u) {
        return;
    }
    hv.handle = g_cccd_handle;
    hv.data_len = sizeof(g_cccd_enable);
    hv.data = g_cccd_enable;
    if (gattc_write_req(g_client_id, g_conn_id, &hv) == 0) {
        g_cccd_written = 1u;
        osal_printk("%s [A] cccd 0x%x <- 0x0001\r\n", LS_LOG, (uint32_t)g_cccd_handle);
    }
}

static void ble_uuid16(uint16_t value, bt_uuid_t *out)
{
    out->uuid_len = 2u;
    out->uuid[0] = (uint8_t)(value >> 8);
    out->uuid[1] = (uint8_t)(value & 0xFFu);
}

/* 在广播数据里找 "linkscope"：AD 结构 [len][type][data...] */
static uint8_t adv_has_peer_name(const uint8_t *data, uint8_t len)
{
    uint8_t i = 0u;
    uint8_t name_len = (uint8_t)(sizeof(g_ble_peer_name) - 1u);

    if (data == NULL) {
        return 0u;
    }
    while ((uint16_t)i + 2u <= len) {
        uint8_t ad_len = data[i];
        if (ad_len == 0u || (uint16_t)(i + 1u + ad_len) > (uint16_t)len) {
            break;
        }
        if (data[i + 1u] == 0x09u && (uint8_t)(ad_len - 1u) == name_len &&
            memcmp(&data[i + 2u], g_ble_peer_name, name_len) == 0) {
            return 1u;
        }
        i = (uint8_t)(i + 1u + ad_len);
    }
    return 0u;
}

static void ble_start_scan(void)
{
    gap_ble_scan_params_t scan;

    (void)memset_s(&scan, sizeof(scan), 0, sizeof(scan));
    scan.scan_interval = 0x30u;   /* 30 × 0.625ms ≈ 19ms */
    scan.scan_window = 0x30u;
    scan.scan_type = 0x00u;       /* 被动扫描 */
    scan.scan_phy = 0x01u;        /* 1M PHY */
    scan.scan_filter_policy = 0u;
    (void)gap_ble_set_scan_parameters(&scan);
}

static void ble_enable_cb(errcode_t status)
{
    bt_uuid_t app_uuid = { 0 };
    bd_addr_t addr;
    uint8_t name[] = "linkscope_a";
    errcode_t rc = ERRCODE_BT_FAIL;
    uint32_t attempt;

    if (status != 0) {
        osal_printk("%s [A] enable_ble failed status=0x%x\r\n", LS_LOG, (uint32_t)status);
        return;
    }
    (void)memset_s(&addr, sizeof(addr), 0, sizeof(addr));
    addr.type = (uint8_t)BT_ADDRESS_TYPE_PUBLIC_DEVICE_ADDRESS;
    if (memcpy_s(addr.addr, BD_ADDR_LEN, g_ble_client_addr, BD_ADDR_LEN) != EOK) {
        return;
    }
    (void)gap_ble_set_local_name(name, (uint8_t)(sizeof(name) - 1u));
    (void)gap_ble_set_local_addr(&addr);

    app_uuid.uuid_len = 2u;
    app_uuid.uuid[0] = 0x00u;
    app_uuid.uuid[1] = 0x00u;
    for (attempt = 1u; attempt <= LS_BLE_REG_RETRY_MAX; attempt++) {
        rc = gattc_register_client(&app_uuid, &g_client_id);
        if (rc == ERRCODE_BT_SUCCESS) {
            break;
        }
        osal_printk("%s [A] register client attempt=%u rc=0x%x id=%u\r\n", LS_LOG,
            attempt, (uint32_t)rc, (uint32_t)g_client_id);
        osal_msleep(LS_BLE_REG_RETRY_MS);
    }
    if (rc != ERRCODE_BT_SUCCESS) {
        osal_printk("%s [A] register client failed after %u attempts rc=0x%x\r\n", LS_LOG,
            (uint32_t)LS_BLE_REG_RETRY_MAX, (uint32_t)rc);
        return;
    }
    osal_printk("%s [A] client registered id=%u, scanning for \"%s\"\r\n", LS_LOG,
        (uint32_t)g_client_id, g_ble_peer_name);
    ble_start_scan();
}

static void ble_set_scan_param_cb(errcode_t status)
{
    if (status != 0) {
        osal_printk("%s [A] set scan param failed 0x%x\r\n", LS_LOG, (uint32_t)status);
        return;
    }
    if (g_scanning == 0u) {
        g_scanning = 1u;
        (void)gap_ble_start_scan();
    }
}

static void ble_scan_result_cb(gap_scan_result_data_t *result)
{
    bd_addr_t peer;

    if (result == NULL || g_scanning == 0u || g_ready != 0u) {
        return;
    }
    if (adv_has_peer_name(result->adv_data, result->adv_len) == 0u) {
        return;
    }
    g_scanning = 0u;
    (void)gap_ble_stop_scan();
    g_rssi = result->rssi;
    g_rssi_valid = 1u;
    (void)memset_s(&peer, sizeof(peer), 0, sizeof(peer));
    peer.type = result->addr.type;
    if (memcpy_s(peer.addr, BD_ADDR_LEN, result->addr.addr, BD_ADDR_LEN) != EOK) {
        return;
    }
    osal_printk("%s [A] found \"%s\" rssi=%d dBm, connecting\r\n", LS_LOG, g_ble_peer_name, (int)g_rssi);
    (void)gap_ble_connect_remote_device(&peer);
}

static void ble_conn_state_cb(uint16_t conn_id, bd_addr_t *addr, gap_ble_conn_state_t conn_state,
                              gap_ble_pair_state_t pair_state, gap_ble_disc_reason_t disc_reason)
{
    unused(addr);
    unused(pair_state);
    unused(disc_reason);

    if (conn_state == GAP_BLE_STATE_CONNECTED) {
        g_conn_id = conn_id;
        osal_printk("%s [A] connected conn_id=%u, exchange mtu %u\r\n", LS_LOG,
            (uint32_t)conn_id, (uint32_t)LS_BLE_MTU);
        (void)gattc_exchange_mtu_req(g_client_id, g_conn_id, LS_BLE_MTU);
    } else {
        osal_printk("%s [A] disconnected, rescan\r\n", LS_LOG);
        g_ready = 0u;
        g_write_handle = 0u;
        g_notify_handle = 0u;
        g_cccd_handle = 0u;
        g_cccd_written = 0u;
        g_transport_ready = 0u;
        g_tune_inflight = 0u;
        g_granted_intv = 0xFFFFu;
        ble_start_scan();
    }
}

static void ble_mtu_changed_cb(uint8_t client_id, uint16_t conn_id, uint16_t mtu_size, errcode_t status)
{
    bt_uuid_t any = { 0 };

    unused(client_id);
    if (status != 0) {
        osal_printk("%s [A] mtu change status=0x%x\r\n", LS_LOG, (uint32_t)status);
    }
    osal_printk("%s [A] mtu=%u, discover services\r\n", LS_LOG, (uint32_t)mtu_size);
    any.uuid_len = 0u;   /* 长度 0 = 发现全部服务 */
    (void)gattc_discovery_service(g_client_id, conn_id, &any);
}

static void ble_discovery_svc_cb(uint8_t client_id, uint16_t conn_id,
                                 gattc_discovery_service_result_t *service, errcode_t status)
{
    gattc_discovery_character_param_t param;

    unused(client_id);
    if (service == NULL || status != 0) {
        return;
    }
    (void)memset_s(&param, sizeof(param), 0, sizeof(param));
    param.service_handle = service->start_hdl;
    param.uuid.uuid_len = 0u;   /* 长度 0 = 该服务下全部特征 */
    (void)gattc_discovery_character(g_client_id, conn_id, &param);
}

static void ble_discovery_chara_cb(uint8_t client_id, uint16_t conn_id,
                                   gattc_discovery_character_result_t *chara, errcode_t status)
{
    bt_uuid_t tx = { 0 };
    bt_uuid_t rx = { 0 };

    unused(client_id);
    if (chara == NULL || status != 0 || chara->uuid.uuid_len != 2u) {
        return;
    }
    ble_uuid16(LS_BLE_TX_UUID, &tx);
    ble_uuid16(LS_BLE_RX_UUID, &rx);
    if (memcmp(chara->uuid.uuid, tx.uuid, 2u) == 0) {
        g_notify_handle = chara->value_handle;
        /* 找到了 notify 特征，顺手把它的描述符（CCCD）也摸出来 */
        (void)gattc_discovery_descriptor(g_client_id, conn_id, chara->declare_handle);
    } else if (memcmp(chara->uuid.uuid, rx.uuid, 2u) == 0) {
        g_write_handle = chara->value_handle;
    }
    ble_try_enable_notify();
}

static void ble_discovery_desc_cb(uint8_t client_id, uint16_t conn_id,
                                  gattc_discovery_descriptor_result_t *desc, errcode_t status)
{
    bt_uuid_t cccd = { 0 };

    if (desc == NULL || status != 0 || desc->uuid.uuid_len != 2u) {
        return;
    }
    ble_uuid16(LS_BLE_CCCD_UUID, &cccd);
    if (memcmp(desc->uuid.uuid, cccd.uuid, 2u) != 0) {
        return;
    }
    g_cccd_handle = desc->descriptor_hdl;
    unused(client_id);
    unused(conn_id);
    /* 明写一次 0x0001，且只在 RX/TX/CCCD 三个句柄都发现后提交。 */
    ble_try_enable_notify();
}

static void ble_discovery_svc_cmp_cb(uint8_t client_id, uint16_t conn_id, bt_uuid_t *uuid, errcode_t status)
{
    unused(client_id);
    unused(conn_id);
    unused(uuid);
    unused(status);
}

static void ble_discovery_chara_cmp_cb(uint8_t client_id, uint16_t conn_id,
                                       gattc_discovery_character_param_t *param, errcode_t status)
{
    unused(client_id);
    unused(conn_id);
    unused(param);
    unused(status);
}

static void ble_discovery_desc_cmp_cb(uint8_t client_id, uint16_t conn_id, uint16_t handle, errcode_t status)
{
    unused(client_id);
    unused(conn_id);
    unused(handle);
    unused(status);
}

static void ble_read_cb(uint8_t client_id, uint16_t conn_id, gattc_handle_value_t *value, gatt_status_t status)
{
    unused(client_id);
    unused(conn_id);
    unused(value);
    unused(status);
}

static void ble_read_cmp_cb(uint8_t client_id, uint16_t conn_id, gattc_read_req_by_uuid_param_t *param,
                            errcode_t status)
{
    unused(client_id);
    unused(conn_id);
    unused(param);
    unused(status);
}

static void ble_write_cb(uint8_t client_id, uint16_t conn_id, uint16_t handle, gatt_status_t status)
{
    unused(client_id);
    unused(conn_id);
    if (handle == g_cccd_handle) {
        if (status == 0) {
            g_transport_ready = 1u;
            osal_printk("%s [A] transport ready (write=0x%x notify=0x%x cccd=0x%x), tune first\r\n",
                LS_LOG, (uint32_t)g_write_handle, (uint32_t)g_notify_handle, (uint32_t)g_cccd_handle);
            (void)gap_ble_read_remote_device_rssi(g_conn_id);
        } else {
            g_cccd_written = 0u;
            osal_printk("%s [A] cccd write failed status=0x%x\r\n", LS_LOG, (uint32_t)status);
        }
        return;
    }
#if defined(CONFIG_LINK_SCOPE_BLE_WRITE_REQUEST)
    if (status == 0 && handle == g_write_handle) {
        uint32_t now = ls_now_us();
        uint32_t lat = now - g_send_us;
        g_cfm_n++;
        g_cfm_sum_us += lat;
        if (lat > g_cfm_max_us) {
            g_cfm_max_us = lat;
        }
        if (g_cfm_print_n < 3u) {
            g_cfm_print_n++;
            osal_printk("%s [A] write cfm #%u lat=%u us\r\n", LS_LOG,
                (uint32_t)g_cfm_print_n, lat);
        }
    }
#else
    unused(status);
#endif
}

/* 热路径：对端 notify 回来了，第一行取 t4 */
static void ble_notification_cb(uint8_t client_id, uint16_t conn_id, gattc_handle_value_t *data,
                                errcode_t status)
{
    uint32_t t4 = ls_now_us();

    unused(client_id);
    unused(conn_id);
    if (data == NULL || data->data == NULL || status != 0) {
        return;
    }
    ls_on_frame(data->data, data->data_len, t4);
}

static void ble_indication_cb(uint8_t client_id, uint16_t conn_id, gattc_handle_value_t *data, errcode_t status)
{
    unused(client_id);
    unused(conn_id);
    unused(data);
    unused(status);
}

static void ble_read_rssi_cb(uint16_t conn_id, int8_t rssi, errcode_t status)
{
    unused(conn_id);
    if (status == 0) {
        g_rssi = rssi;
        g_rssi_valid = 1u;
        osal_printk("%s [A] rssi=%d dBm\r\n", LS_LOG, (int)rssi);
    }
}

static void ble_conn_param_update_cb(uint16_t conn_id, errcode_t status, const gap_ble_conn_param_update_t *param)
{
    unused(conn_id);
    if (param == NULL) {
        return;
    }
    g_granted_intv = param->interval;
    osal_printk("%s [A] param updated intv=%u (%u.%02u ms) latency=%u status=0x%x\r\n", LS_LOG,
        (uint32_t)param->interval, (uint32_t)(param->interval * 125u / 100u),
        (uint32_t)((param->interval * 125u / 10u) % 10u), (uint32_t)param->latency, (uint32_t)status);
    if (status == 0 && param->interval == (uint16_t)CONFIG_LINK_SCOPE_BLE_INTV_UNIT) {
        g_ready = 1u;
        osal_printk("%s [A] measurement ready after param update\r\n", LS_LOG);
    } else {
        g_tune_inflight = 0u;
    }
}

static int ble_start(void)
{
    gap_ble_callbacks_t gap_cb;
    gattc_callbacks_t gatt_cb;

    osal_printk("%s [A] init ble, wait stack %ums\r\n", LS_LOG, LS_BLE_START_DELAY_MS);
    osal_msleep(LS_BLE_START_DELAY_MS);

    (void)memset_s(&gap_cb, sizeof(gap_cb), 0, sizeof(gap_cb));
    gap_cb.ble_enable_cb = ble_enable_cb;
    gap_cb.set_scan_param_cb = ble_set_scan_param_cb;
    gap_cb.scan_result_cb = ble_scan_result_cb;
    gap_cb.conn_state_change_cb = ble_conn_state_cb;
    gap_cb.read_rssi_cb = ble_read_rssi_cb;
    gap_cb.conn_param_update_cb = ble_conn_param_update_cb;
    (void)gap_ble_register_callbacks(&gap_cb);

    (void)memset_s(&gatt_cb, sizeof(gatt_cb), 0, sizeof(gatt_cb));
    gatt_cb.discovery_svc_cb = ble_discovery_svc_cb;
    gatt_cb.discovery_svc_cmp_cb = ble_discovery_svc_cmp_cb;
    gatt_cb.discovery_chara_cb = ble_discovery_chara_cb;
    gatt_cb.discovery_chara_cmp_cb = ble_discovery_chara_cmp_cb;
    gatt_cb.discovery_desc_cb = ble_discovery_desc_cb;
    gatt_cb.discovery_desc_cmp_cb = ble_discovery_desc_cmp_cb;
    gatt_cb.read_cb = ble_read_cb;
    gatt_cb.read_cmp_cb = ble_read_cmp_cb;
    gatt_cb.write_cb = ble_write_cb;
    gatt_cb.mtu_changed_cb = ble_mtu_changed_cb;
    gatt_cb.notification_cb = ble_notification_cb;
    gatt_cb.indication_cb = ble_indication_cb;
    (void)gattc_register_callbacks(&gatt_cb);

    if (enable_ble() != 0) {
        osal_printk("%s [A] enable_ble call failed\r\n", LS_LOG);
        return -1;
    }
    return 0;
}

static int ble_send(const uint8_t *data, uint16_t len)
{
    gattc_handle_value_t hv;

    if (g_ready == 0u || g_write_handle == 0u) {
        return -1;
    }
    hv.handle = g_write_handle;
    hv.data_len = len;
    hv.data = (uint8_t *)data;
#if defined(CONFIG_LINK_SCOPE_BLE_WRITE_REQUEST)
    g_send_us = ls_now_us();
    return (gattc_write_req(g_client_id, g_conn_id, &hv) == 0) ? 0 : -1;
#else
    return (gattc_write_cmd(g_client_id, g_conn_id, &hv) == 0) ? 0 : -1;
#endif
}

static uint8_t ble_ready(void)
{
    return g_ready;
}

/*
 * 传输层就绪后请求配置的 BLE 连接间隔（单位 1.25 ms）。
 * 参数更新回调成功且实际授予值相等后，才允许测量引擎开始发包。
 * RTT 跨几个连接事件取决于调度与包到达相位，不能只由间隔推导固定上限。
 */
static void ble_poll(uint32_t now_ms)
{
    gap_conn_param_update_t req;
    errcode_t rc;

    if (g_transport_ready == 0u || g_ready != 0u) {
        return;
    }
    if (g_tune_inflight != 0u && (uint32_t)(now_ms - g_last_tune_ms) < LS_BLE_TUNE_MS) {
        return;
    }
    g_last_tune_ms = now_ms;
    (void)memset_s(&req, sizeof(req), 0, sizeof(req));
    req.conn_handle = g_conn_id;
    req.interval_min = (uint16_t)CONFIG_LINK_SCOPE_BLE_INTV_UNIT;
    req.interval_max = (uint16_t)CONFIG_LINK_SCOPE_BLE_INTV_UNIT;
    req.slave_latency = 0u;
    req.timeout_multiplier = LS_BLE_SUP_TIMEOUT;
    g_tune_inflight = 1u;
    rc = gap_ble_connect_param_update(&req);
    osal_printk("%s [A] tune intv=0x%x rc=0x%x (granted=0x%x)\r\n", LS_LOG,
        (uint32_t)CONFIG_LINK_SCOPE_BLE_INTV_UNIT, (uint32_t)rc, (uint32_t)g_granted_intv);
    if (rc != 0) {
        g_tune_inflight = 0u;
    }
}

static void ble_dbg(uint32_t *count, uint32_t *avg_us, uint32_t *max_us)
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

static const link_ops_t g_ble_ops = {
#if defined(CONFIG_LINK_SCOPE_BLE_WRITE_REQUEST)
    .name = "ble-write_req",
#else
    .name = "ble-write_cmd",
#endif
    .start = ble_start,
    .send = ble_send,
    .ready = ble_ready,
    .poll = ble_poll,
    .dbg = ble_dbg,
};

const link_ops_t *ls_link_ops_get(void)
{
    return &g_ble_ops;
}
