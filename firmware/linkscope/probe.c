/*
 * LinkScope 探针帧编解码（纯逻辑，不依赖 SDK）
 *
 * 手写小端编解码，不用 packed struct：解析路径上没有对齐/字节序的坑，
 * 也不会因为编译器插入填充而改变线上格式。
 */
#include <stddef.h>
#include "probe.h"

static void put_u16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
}

static uint16_t get_u16(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

static void put_u32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
    p[2] = (uint8_t)((v >> 16) & 0xFFu);
    p[3] = (uint8_t)((v >> 24) & 0xFFu);
}

static uint32_t get_u32(const uint8_t *p)
{
    return (uint32_t)p[0] |
           ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

uint16_t probe_req_len(uint16_t payload_bytes)
{
    if (payload_bytes < PROBE_FRAME_MIN) {
        return PROBE_FRAME_MIN;
    }
    if (payload_bytes > PROBE_FRAME_MAX) {
        return PROBE_FRAME_MAX;
    }
    return payload_bytes;
}

void probe_patch_u32(uint8_t *frame, uint16_t off, uint32_t value)
{
    if (frame == NULL) {
        return;
    }
    put_u32(&frame[off], value);
}

int probe_check_frame(const uint8_t *in, uint16_t len, uint8_t type)
{
    if (in == NULL || len < 2u) {
        return PROBE_ERR_ARG;
    }
    if (in[PROBE_OFF_MAGIC] != PROBE_MAGIC) {
        return PROBE_ERR_MAGIC;
    }
    if (in[PROBE_OFF_TYPE] != type) {
        return PROBE_ERR_TYPE;
    }
    return PROBE_OK;
}

int probe_build_req(uint8_t *out, uint16_t cap, const probe_req_t *req)
{
    uint16_t len;
    uint16_t i;

    if (out == NULL || req == NULL) {
        return PROBE_ERR_ARG;
    }
    len = probe_req_len(req->frame_len);
    if (cap < len) {
        return PROBE_ERR_LEN;
    }

    out[PROBE_OFF_MAGIC] = PROBE_MAGIC;
    out[PROBE_OFF_TYPE] = PROBE_TYPE_REQ;
    put_u16(&out[PROBE_OFF_SEQ], req->seq);
    put_u32(&out[PROBE_OFF_T1], req->t1_us);
    put_u32(&out[PROBE_OFF_CFG_ID], req->cfg_id);
    /* 填充字节带上 seq 特征：抓包/示波器上看得出是哪一包，也便于抓错序 */
    for (i = PROBE_REQ_HDR_LEN; i < len; i++) {
        out[i] = (uint8_t)((uint8_t)i + (uint8_t)(req->seq & 0xFFu));
    }
    return (int)len;
}

int probe_parse_req(const uint8_t *in, uint16_t len, probe_req_t *out)
{
    int ret;

    if (in == NULL || out == NULL) {
        return PROBE_ERR_ARG;
    }
    if (len < PROBE_FRAME_MIN || len > PROBE_FRAME_MAX) {
        return PROBE_ERR_LEN;
    }
    ret = probe_check_frame(in, len, PROBE_TYPE_REQ);
    if (ret != PROBE_OK) {
        return ret;
    }

    out->seq = get_u16(&in[PROBE_OFF_SEQ]);
    out->t1_us = get_u32(&in[PROBE_OFF_T1]);
    out->cfg_id = get_u32(&in[PROBE_OFF_CFG_ID]);
    out->frame_len = len;
    return PROBE_OK;
}

int probe_build_rsp(uint8_t *out, uint16_t cap, const probe_rsp_t *rsp)
{
    if (out == NULL || rsp == NULL) {
        return PROBE_ERR_ARG;
    }
    if (cap < PROBE_RSP_LEN) {
        return PROBE_ERR_LEN;
    }

    out[PROBE_OFF_MAGIC] = PROBE_MAGIC;
    out[PROBE_OFF_TYPE] = PROBE_TYPE_RSP;
    put_u16(&out[PROBE_OFF_SEQ], rsp->seq);
    put_u32(&out[PROBE_OFF_T1], rsp->t1_us);
    put_u32(&out[PROBE_OFF_T2], rsp->t2_us);
    put_u32(&out[PROBE_OFF_T3], rsp->t3_us);
    put_u32(&out[PROBE_OFF_CFG_ID_RSP], rsp->cfg_id);
    return (int)PROBE_RSP_LEN;
}

int probe_parse_rsp(const uint8_t *in, uint16_t len, probe_rsp_t *out)
{
    int ret;

    if (in == NULL || out == NULL) {
        return PROBE_ERR_ARG;
    }
    if (len != PROBE_RSP_LEN) {
        return PROBE_ERR_LEN;
    }
    ret = probe_check_frame(in, len, PROBE_TYPE_RSP);
    if (ret != PROBE_OK) {
        return ret;
    }

    out->seq = get_u16(&in[PROBE_OFF_SEQ]);
    out->t1_us = get_u32(&in[PROBE_OFF_T1]);
    out->t2_us = get_u32(&in[PROBE_OFF_T2]);
    out->t3_us = get_u32(&in[PROBE_OFF_T3]);
    out->cfg_id = get_u32(&in[PROBE_OFF_CFG_ID_RSP]);
    return PROBE_OK;
}
