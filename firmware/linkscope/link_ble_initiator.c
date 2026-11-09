/*
 * BLE 发起端（A）：扫名字 -> 连 -> 换 MTU -> 发现特征 -> 发 REQ -> 等 notify 回来。
 *
 * 与 SLE 版的对照点：
 *   - SLE：seek(按名字广播) -> connect -> pair -> MTU 520 -> 发现属性 -> ssapc_write_req
 *   - BLE：scan(按名字过滤) -> connect -> exchange MTU -> 发现 service/char/CCCD -> gattc_write_cmd
 * 发送用"写不响应"（write_cmd），理由：热路径少一趟写响应，量到的 RTT 只剩 REQ 上去 + RSP notify 回来，
 * 和 SLE 那边 ssapc_write_req 的差别写在 docs 里，对比时要说清。
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
#define LS_BLE_INTV_SLOT        0x06u     /* BLE 最小连接间隔 7.5ms = 6 × 1.25ms */
#define LS_BLE_TUNE_MS          5000u
#define LS_BLE_SUP_TIMEOUT      500u      /* 500 × 10ms = 5s */
#define LS_BLE_START_DELAY_MS   2000u

static const char g_ble_peer_name[] = "linkscope";
static const uint8_t g_ble_client_addr[BD_ADDR_LEN] = { 0x12, 0x22, 0x33, 0x44, 0x55, 0x66 };

static uint8_t g_client_id = 0u;
static uint16_t g_conn_id = 0u;
static uint16_t g_write_handle = 0u;      /* 对端 RX 特征：我们写这里 */
static uint16_t g_notify_handle = 0u;     /* 对端 TX 特征：对端 notify 这里 */
static uint16_t g_cccd_handle = 0u;
static volatile uint8_t g_ready = 0u;
static volatile uint8_t g_scanning = 0u;
static volatile int8_t g_rssi = 0;
static volatile uint8_t g_rssi_valid = 0u;
static volatile uint8_t g_cccd_written = 0u;

/* 写确认时延（write_cmd 下协议栈不一定回调，能回就统计） */
static volatile uint32_t g_cfm_n;
static volatile uint32_t g_cfm_sum_us;
static volatile uint32_t g_cfm_max_us;
static uint32_t g_send_us;

static uint32_t g_last_tune_ms;
static uint16_t g_granted_intv = 0xFFFFu;

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
    if (gattc_register_client(&app_uuid, &g_client_id) != 0) {
        osal_printk("%s [A] register client failed\r\n", LS_LOG);
        return;
    }
    osal_printk("%s [A] client up, scanning for \"%s\"\r\n", LS_LOG, g_ble_peer_name);
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
    if (g_write_handle != 0u && g_notify_handle != 0u && g_ready == 0u) {
        g_ready = 1u;
        osal_printk("%s [A] link ready (write=0x%x notify=0x%x cccd=0x%x)\r\n", LS_LOG,
            (uint32_t)g_write_handle, (uint32_t)g_notify_handle, (uint32_t)g_cccd_handle);
        (void)gap_ble_read_remote_device_rssi(g_conn_id);
    }
}

static void ble_discovery_desc_cb(uint8_t client_id, uint16_t conn_id,
                                  gattc_discovery_descriptor_result_t *desc, errcode_t status)
{
    bt_uuid_t cccd = { 0 };
    gattc_handle_value_t hv;
    uint8_t enable[2] = { 0x01u, 0x00u };

    if (desc == NULL || status != 0 || desc->uuid.uuid_len != 2u) {
        return;
    }
    ble_uuid16(LS_BLE_CCCD_UUID, &cccd);
    if (memcmp(desc->uuid.uuid, cccd.uuid, 2u) != 0) {
        return;
    }
    g_cccd_handle = desc->descriptor_hdl;
    if (g_cccd_written != 0u) {
        return;
    }
    /* 明写一次 0x0001，避免依赖服务端建 CCCD 时的默认值 */
    hv.handle = g_cccd_handle;
    hv.data_len = sizeof(enable);
    hv.data = enable;
    if (gattc_write_req(client_id, conn_id, &hv) == 0) {
        g_cccd_written = 1u;
        osal_printk("%s [A] cccd 0x%x <- 0x0001\r\n", LS_LOG, (uint32_t)g_cccd_handle);
    }
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
    uint32_t now = ls_now_us();

    unused(client_id);
    unused(conn_id);
    unused(status);
    if (handle != g_cccd_handle) {
        uint32_t lat = now - g_send_us;
        g_cfm_n++;
        g_cfm_sum_us += lat;
        if (lat > g_cfm_max_us) {
            g_cfm_max_us = lat;
        }
    }
}

/* 热路径：对端 notify 回来了，第一行取 t4 */
static void ble_notification_cb(uint8_t client_id, uint16_t conn_id, gattc_handle_value_t *data,
                                errcode_t status)
{
    uint32_t t4;

    unused(client_id);
    unused(conn_id);
    if (data == NULL || data->data == NULL || status != 0) {
        return;
    }
    t4 = ls_now_us();
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
    g_send_us = ls_now_us();
    return (gattc_write_cmd(g_client_id, g_conn_id, &hv) == 0) ? 0 : -1;
}

static uint8_t ble_ready(void)
{
    return g_ready;
}

/*
 * 就绪后把连接间隔拉到 BLE 允许的最小值 7.5ms（0x06）。
 * 停止等模式下 RTT ≈ 2 个连接事件，7.5ms 间隔理论上 ~15ms 一往返，40Hz（25ms）够用；
 * 100Hz 在 BLE 上本来就不可能（规格下限 7.5ms，2 事件就是 66 包/秒的上限）。
 */
static void ble_poll(uint32_t now_ms)
{
    gap_conn_param_update_t req;
    errcode_t rc;

    if (g_ready == 0u) {
        return;
    }
    if (g_granted_intv != 0xFFFFu && g_granted_intv <= LS_BLE_INTV_SLOT) {
        return;   /* 已经是最小间隔，别再打扰链路 */
    }
    if ((uint32_t)(now_ms - g_last_tune_ms) < LS_BLE_TUNE_MS) {
        return;
    }
    g_last_tune_ms = now_ms;
    (void)memset_s(&req, sizeof(req), 0, sizeof(req));
    req.conn_handle = g_conn_id;
    req.interval_min = LS_BLE_INTV_SLOT;
    req.interval_max = LS_BLE_INTV_SLOT;
    req.slave_latency = 0u;
    req.timeout_multiplier = LS_BLE_SUP_TIMEOUT;
    rc = gap_ble_connect_param_update(&req);
    osal_printk("%s [A] tune intv=0x%x rc=0x%x (granted=0x%x)\r\n", LS_LOG,
        (uint32_t)LS_BLE_INTV_SLOT, (uint32_t)rc, (uint32_t)g_granted_intv);
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
    .name = "ble",
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
