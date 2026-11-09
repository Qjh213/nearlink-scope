/*
 * BLE 响应端（B）：收 WRITE -> 打 t2 -> 组 RSP -> 打 t3 -> notify 回去。
 *
 * 和 SLE 版结构一一对应，只把底座换成 BLE GATT server：
 *   - 服务/特征/CCCD 都在 add_service 回调里建（写法照 ble_uart_server.c:66-129）
 *   - 广播数据里带名字 "linkscope"，A 端按名字扫，不绑 MAC（SLE 那版吃过固定 MAC 的亏）
 *   - 热路径（write 回调）里不打印、不 malloc、不转发
 */
#include <stddef.h>
#include <string.h>
#include "common_def.h"
#include "soc_osal.h"
#include "securec.h"
#include "errcode.h"
#include "bts_def.h"
#include "bts_gatt_stru.h"
#include "bts_gatt_server.h"
#include "bts_le_gap.h"
#include "linkscope.h"

#define LS_BLE_SERVER_ID        1u
#define LS_BLE_ADV_HANDLE       1u
#define LS_BLE_SVC_UUID         0xFE30u   /* 自定义 16 位服务 */
#define LS_BLE_TX_UUID          0xFE31u   /* B -> A：notify（RSP） */
#define LS_BLE_RX_UUID          0xFE32u   /* A -> B：write（REQ） */
#define LS_BLE_CCCD_UUID        0x2902u   /* 客户端特征配置，写 0x0001 开通知 */
#define LS_BLE_START_DELAY_MS   2000u
#define LS_BLE_HB_TICKS         50u       /* 50 tick × 20ms = 1s */
#define LS_BLE_HB_FIRST_PRINTS  6u

#define LS_BLE_NAME             "linkscope"

static const char g_ble_adv_name[] = LS_BLE_NAME;
static const uint8_t g_ble_server_addr[BD_ADDR_LEN] = { 0x11, 0x22, 0x33, 0x44, 0x55, 0x66 };

static uint8_t g_server_id = 0u;
static uint16_t g_conn_id = 0u;
static uint8_t g_conn_state = 0u;
static uint16_t g_tx_handle = 0u;          /* notify 用的 value handle */
static uint16_t g_rx_handle = 0u;
static uint8_t g_rsp_buf[PROBE_RSP_LEN];

/* 诊断计数：回调里只自增，打印放到任务上下文 */
static volatile uint32_t g_dbg_req;
static volatile uint32_t g_dbg_bad;
static volatile uint32_t g_dbg_rsp;
static volatile int32_t  g_dbg_err;

static uint8_t g_tx_value[2] = { 0x00, 0x00 };
static uint8_t g_rx_value[2] = { 0x00, 0x00 };
static uint8_t g_ccc_value[2] = { 0x01, 0x00 };   /* 默认打开 notify */

static void ble_uuid16(uint16_t value, bt_uuid_t *out)
{
    out->uuid_len = 2u;                            /* 16 位 UUID 长度 */
    out->uuid[0] = (uint8_t)(value >> 8);
    out->uuid[1] = (uint8_t)(value & 0xFFu);
}

static void ble_add_tx_char(uint8_t server_id, uint16_t srvc_handle)
{
    bt_uuid_t uuid = { 0 };
    bt_uuid_t ccc_uuid = { 0 };
    gatts_add_chara_info_t chi;
    gatts_add_desc_info_t desc;

    ble_uuid16(LS_BLE_TX_UUID, &uuid);
    (void)memset_s(&chi, sizeof(chi), 0, sizeof(chi));
    chi.chara_uuid = uuid;
    chi.properties = GATT_CHARACTER_PROPERTY_BIT_NOTIFY | GATT_CHARACTER_PROPERTY_BIT_READ;
    chi.permissions = GATT_ATTRIBUTE_PERMISSION_READ | GATT_ATTRIBUTE_PERMISSION_WRITE;
    chi.value_len = sizeof(g_tx_value);
    chi.value = g_tx_value;
    (void)gatts_add_characteristic(server_id, srvc_handle, &chi);

    ble_uuid16(LS_BLE_CCCD_UUID, &ccc_uuid);
    (void)memset_s(&desc, sizeof(desc), 0, sizeof(desc));
    desc.desc_uuid = ccc_uuid;
    desc.permissions = GATT_ATTRIBUTE_PERMISSION_READ | GATT_ATTRIBUTE_PERMISSION_WRITE;
    desc.value_len = sizeof(g_ccc_value);
    desc.value = g_ccc_value;
    (void)gatts_add_descriptor(server_id, srvc_handle, &desc);
}

static void ble_add_rx_char(uint8_t server_id, uint16_t srvc_handle)
{
    bt_uuid_t uuid = { 0 };
    gatts_add_chara_info_t chi;

    ble_uuid16(LS_BLE_RX_UUID, &uuid);
    (void)memset_s(&chi, sizeof(chi), 0, sizeof(chi));
    chi.chara_uuid = uuid;
    /* 只写不响应：A 端用 gattc_write_cmd 发，省掉一次写响应，RTT 里只剩 notify 一趟 */
    chi.properties = GATT_CHARACTER_PROPERTY_BIT_READ | GATT_CHARACTER_PROPERTY_BIT_WRITE;
    chi.permissions = GATT_ATTRIBUTE_PERMISSION_READ | GATT_ATTRIBUTE_PERMISSION_WRITE;
    chi.value_len = sizeof(g_rx_value);
    chi.value = g_rx_value;
    (void)gatts_add_characteristic(server_id, srvc_handle, &chi);
}

static void ble_set_adv_and_start(void)
{
    uint8_t adv[32];
    uint8_t n = 0u;
    uint8_t name_len = (uint8_t)(sizeof(g_ble_adv_name) - 1u);
    gap_ble_adv_params_t para;
    gap_ble_config_adv_data_t cfg;

    /* flags：LE 通用可发现、不支持 BR/EDR */
    adv[n++] = 0x02u;
    adv[n++] = 0x01u;
    adv[n++] = 0x06u;
    /* 名字：A 端靠它扫，不绑 MAC */
    adv[n++] = (uint8_t)(name_len + 1u);
    adv[n++] = 0x09u;
    if (memcpy_s(&adv[n], sizeof(adv) - n, g_ble_adv_name, name_len) != EOK) {
        return;
    }
    n = (uint8_t)(n + name_len);

    (void)memset_s(&cfg, sizeof(cfg), 0, sizeof(cfg));
    cfg.adv_data = adv;
    cfg.adv_length = n;
    cfg.scan_rsp_data = NULL;
    cfg.scan_rsp_length = 0u;
    (void)gap_ble_set_adv_data(LS_BLE_ADV_HANDLE, &cfg);

    (void)memset_s(&para, sizeof(para), 0, sizeof(para));
    para.min_interval = 0x20u;                     /* 20ms */
    para.max_interval = 0x30u;                     /* 30ms */
    para.duration = 0u;                            /* 一直播 */
    para.adv_type = GAP_BLE_ADV_CONN_SCAN_UNDIR;   /* 可连接可扫描非定向 */
    para.own_addr.type = (uint8_t)BT_ADDRESS_TYPE_PUBLIC_DEVICE_ADDRESS;
    para.peer_addr.type = (uint8_t)BT_ADDRESS_TYPE_PUBLIC_DEVICE_ADDRESS;
    para.channel_map = 0x07u;                      /* 37/38/39 */
    para.adv_filter_policy = 0u;
    para.tx_power = 0;
    (void)gap_ble_set_adv_param(LS_BLE_ADV_HANDLE, &para);
    (void)gap_ble_start_adv(LS_BLE_ADV_HANDLE);
}

/* ---------------- GATT server 回调 ---------------- */

static void ble_service_add_cb(uint8_t server_id, bt_uuid_t *uuid, uint16_t handle, errcode_t status)
{
    unused(uuid);
    if (status != 0) {
        osal_printk("%s [B] add service failed status=0x%x\r\n", LS_LOG, (uint32_t)status);
        return;
    }
    ble_add_tx_char(server_id, handle);
    ble_add_rx_char(server_id, handle);
    (void)gatts_start_service(server_id, handle);
}

static void ble_char_add_cb(uint8_t server_id, bt_uuid_t *uuid, uint16_t service_handle,
                            gatts_add_character_result_t *result, errcode_t status)
{
    bt_uuid_t tx = { 0 };
    bt_uuid_t rx = { 0 };

    unused(server_id);
    unused(service_handle);
    unused(status);
    if (uuid == NULL || result == NULL) {
        return;
    }
    ble_uuid16(LS_BLE_TX_UUID, &tx);
    ble_uuid16(LS_BLE_RX_UUID, &rx);
    if (uuid->uuid_len == tx.uuid_len && memcmp(uuid->uuid, tx.uuid, tx.uuid_len) == 0) {
        g_tx_handle = result->value_handle;
    } else if (uuid->uuid_len == rx.uuid_len && memcmp(uuid->uuid, rx.uuid, rx.uuid_len) == 0) {
        g_rx_handle = result->value_handle;
    }
}

static void ble_desc_add_cb(uint8_t server_id, bt_uuid_t *uuid, uint16_t service_handle,
                            uint16_t handle, errcode_t status)
{
    unused(server_id);
    unused(uuid);
    unused(service_handle);
    unused(handle);
    unused(status);
}

static void ble_service_start_cb(uint8_t server_id, uint16_t handle, errcode_t status)
{
    unused(server_id);
    if (status != 0) {
        osal_printk("%s [B] start service failed status=0x%x\r\n", LS_LOG, (uint32_t)status);
        return;
    }
    osal_printk("%s [B] service up (tx=0x%x rx=0x%x), start adv\r\n", LS_LOG,
        (uint32_t)g_tx_handle, (uint32_t)g_rx_handle);
    ble_set_adv_and_start();
}

static void ble_mtu_changed_cb(uint8_t server_id, uint16_t conn_id, uint16_t mtu_size, errcode_t status)
{
    unused(server_id);
    unused(conn_id);
    unused(status);
    osal_printk("%s [B] mtu=%u\r\n", LS_LOG, (uint32_t)mtu_size);
}

/*
 * 热路径：A 的 REQ 到了。
 * t2 必须在第一行取；组帧用静态缓冲（协议栈可能还持有指针）。
 */
static void ble_write_req_cb(uint8_t server_id, uint16_t conn_id, gatts_req_write_cb_t *req, errcode_t status)
{
    probe_req_t in;
    probe_rsp_t out;
    gatts_ntf_ind_t ntf;
    uint32_t t2;
    uint32_t t3;
    errcode_t rc;

    unused(server_id);
    if (req == NULL || req->value == NULL || status != 0) {
        g_dbg_bad++;
        return;
    }
    t2 = ls_now_us();                    /* 铁律：第一行取时间戳 */
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

    (void)memset_s(&ntf, sizeof(ntf), 0, sizeof(ntf));
    ntf.attr_handle = g_tx_handle;
    ntf.value_len = PROBE_RSP_LEN;
    ntf.value = g_rsp_buf;
    rc = gatts_notify_indicate(g_server_id, conn_id, &ntf);
    if (rc != 0) {
        g_dbg_err = (int32_t)rc;
    } else {
        g_dbg_rsp++;
    }
}

/* ---------------- GAP 回调 ---------------- */

static void ble_conn_state_cb(uint16_t conn_id, bd_addr_t *addr, gap_ble_conn_state_t conn_state,
                              gap_ble_pair_state_t pair_state, gap_ble_disc_reason_t disc_reason)
{
    unused(addr);
    unused(pair_state);
    unused(disc_reason);

    g_conn_state = (uint8_t)conn_state;
    if (conn_state == GAP_BLE_STATE_CONNECTED) {
        g_conn_id = conn_id;
        osal_printk("%s [B] connected conn_id=%u\r\n", LS_LOG, (uint32_t)conn_id);
    } else {
        osal_printk("%s [B] disconnected, re-adv\r\n", LS_LOG);
        g_conn_id = 0u;
        ble_set_adv_and_start();
    }
}

static void ble_enable_cb(errcode_t status)
{
    bt_uuid_t app_uuid = { 0 };
    bd_addr_t addr;
    uint8_t name[] = LS_BLE_NAME;

    if (status != 0) {
        osal_printk("%s [B] enable_ble failed status=0x%x\r\n", LS_LOG, (uint32_t)status);
        return;
    }
    (void)memset_s(&addr, sizeof(addr), 0, sizeof(addr));
    addr.type = (uint8_t)BT_ADDRESS_TYPE_PUBLIC_DEVICE_ADDRESS;
    if (memcpy_s(addr.addr, BD_ADDR_LEN, g_ble_server_addr, BD_ADDR_LEN) != EOK) {
        return;
    }
    (void)gap_ble_set_local_name(name, (uint8_t)(sizeof(LS_BLE_NAME) - 1u));
    (void)gap_ble_set_local_addr(&addr);

    app_uuid.uuid_len = 2u;
    app_uuid.uuid[0] = 0x00u;
    app_uuid.uuid[1] = 0x00u;
    if (gatts_register_server(&app_uuid, &g_server_id) != 0) {
        osal_printk("%s [B] register server failed\r\n", LS_LOG);
        return;
    }
    ble_uuid16(LS_BLE_SVC_UUID, &app_uuid);
    (void)gatts_add_service(g_server_id, &app_uuid, true);
}

static void ble_start_adv_cb(uint8_t adv_id, adv_status_t status)
{
    osal_printk("%s [B] adv on adv_id=%u status=0x%x\r\n", LS_LOG, (uint32_t)adv_id, (uint32_t)status);
}

static void ble_stop_adv_cb(uint8_t adv_id, adv_status_t status)
{
    unused(adv_id);
    unused(status);
}

static void ble_pair_result_cb(uint16_t conn_id, const bd_addr_t *addr, errcode_t status)
{
    unused(addr);
    osal_printk("%s [B] pair conn=%u status=0x%x\r\n", LS_LOG, (uint32_t)conn_id, (uint32_t)status);
}

static void resp_hb(uint32_t *last_req, uint32_t *prints)
{
    if (g_dbg_req == *last_req && *prints >= LS_BLE_HB_FIRST_PRINTS) {
        return;
    }
    *last_req = g_dbg_req;
    if (*prints < LS_BLE_HB_FIRST_PRINTS) {
        (*prints)++;
    }
    osal_printk("%s [B] hb req=%u bad=%u rsp=%u lasterr=0x%x\r\n", LS_LOG,
        (uint32_t)g_dbg_req, (uint32_t)g_dbg_bad, (uint32_t)g_dbg_rsp, (uint32_t)g_dbg_err);
}

static void ble_responder_init(void)
{
    gap_ble_callbacks_t gap_cb;
    gatts_callbacks_t gatt_cb;

    (void)memset_s(&gap_cb, sizeof(gap_cb), 0, sizeof(gap_cb));
    gap_cb.ble_enable_cb = ble_enable_cb;
    gap_cb.conn_state_change_cb = ble_conn_state_cb;
    gap_cb.pair_result_cb = ble_pair_result_cb;
    gap_cb.start_adv_cb = ble_start_adv_cb;
    gap_cb.stop_adv_cb = ble_stop_adv_cb;
    (void)gap_ble_register_callbacks(&gap_cb);

    (void)memset_s(&gatt_cb, sizeof(gatt_cb), 0, sizeof(gatt_cb));
    gatt_cb.add_service_cb = ble_service_add_cb;
    gatt_cb.add_characteristic_cb = ble_char_add_cb;
    gatt_cb.add_descriptor_cb = ble_desc_add_cb;
    gatt_cb.start_service_cb = ble_service_start_cb;
    gatt_cb.write_request_cb = ble_write_req_cb;
    gatt_cb.mtu_changed_cb = ble_mtu_changed_cb;
    (void)gatts_register_callbacks(&gatt_cb);

    if (enable_ble() != 0) {
        osal_printk("%s [B] enable_ble call failed\r\n", LS_LOG);
    }
}

void ls_responder_run(void)
{
    uint32_t hb_last_req = 0xFFFFFFFFu;
    uint32_t hb_prints = 0u;

    osal_printk("%s [B] responder booting (ble), wait stack %ums\r\n", LS_LOG, LS_BLE_START_DELAY_MS);
    osal_msleep(LS_BLE_START_DELAY_MS);
    ble_responder_init();

    while (1) {
        osal_msleep(1000);
        resp_hb(&hb_last_req, &hb_prints);   /* 1s 一次心跳，静默后自动闭嘴 */
    }
}
