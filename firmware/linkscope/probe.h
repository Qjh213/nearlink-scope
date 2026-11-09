/*
 * LinkScope 探针帧编解码（纯逻辑，不依赖 SDK）
 *
 * 帧格式见 docs/protocol.md，所有字段小端序：
 *
 *   REQ  (A -> B)                          RSP (B -> A)
 *   +0  magic   0xA5                       +0  magic   0xA5
 *   +1  type    0x01                       +1  type    0x02
 *   +2  seq     u16                        +2  seq     u16   原样回显
 *   +4  t1_us   u32  A 端发送时刻           +4  t1_us   u32   原样回显
 *   +8  cfg_id  u32                        +8  t2_us   u32   B 收到时刻
 *   +12 fill    n 字节（把帧长拉到档位）      +12 t3_us   u32   B 发出时刻
 *                                          +16 cfg_id  u32   原样回显
 *
 * RSP 固定 20 字节；REQ 长度 = 12 + fill，默认 40B，最大 250B（性能模式）。
 */
#ifndef LINK_SCOPE_PROBE_H
#define LINK_SCOPE_PROBE_H

#include <stdint.h>

#define PROBE_MAGIC                 0xA5u
#define PROBE_TYPE_REQ              0x01u
#define PROBE_TYPE_RSP              0x02u

#define PROBE_REQ_HDR_LEN           12u
#define PROBE_RSP_LEN               20u
#define PROBE_FRAME_MAX             250u
#define PROBE_FRAME_MIN             PROBE_REQ_HDR_LEN

/* 帧内字段偏移 */
#define PROBE_OFF_MAGIC             0u
#define PROBE_OFF_TYPE              1u
#define PROBE_OFF_SEQ               2u
#define PROBE_OFF_T1                4u
#define PROBE_OFF_CFG_ID            8u
#define PROBE_OFF_T2                8u
#define PROBE_OFF_T3                12u
#define PROBE_OFF_CFG_ID_RSP        16u

/* 返回码 */
#define PROBE_OK                    0
#define PROBE_ERR_ARG               (-1)
#define PROBE_ERR_MAGIC             (-2)
#define PROBE_ERR_TYPE              (-3)
#define PROBE_ERR_LEN               (-4)

typedef struct {
    uint16_t seq;
    uint32_t t1_us;
    uint32_t cfg_id;
    uint16_t frame_len;         /* 实际帧长 = 12 + fill，由 probe_req_len() 得出 */
} probe_req_t;

typedef struct {
    uint16_t seq;
    uint32_t t1_us;
    uint32_t t2_us;
    uint32_t t3_us;
    uint32_t cfg_id;
} probe_rsp_t;

/* 把包长（含 12B 头）夹到 [12, 250] */
uint16_t probe_req_len(uint16_t payload_bytes);

/* 返回写入的字节数（>0）或 PROBE_ERR_* */
int probe_build_req(uint8_t *out, uint16_t cap, const probe_req_t *req);
int probe_build_rsp(uint8_t *out, uint16_t cap, const probe_rsp_t *rsp);

/* 校验并解析；成功返回 PROBE_OK 并填 *out */
int probe_parse_req(const uint8_t *in, uint16_t len, probe_req_t *out);
int probe_parse_rsp(const uint8_t *in, uint16_t len, probe_rsp_t *out);

/* 定点改写帧内 u32 字段（用于 t3：先组帧、取完时间戳再回填，省一次组帧） */
void probe_patch_u32(uint8_t *frame, uint16_t off, uint32_t value);

/* 只校验 magic/type，返回 PROBE_OK 或错误码；用于快速丢弃杂包 */
int probe_check_frame(const uint8_t *in, uint16_t len, uint8_t type);

#endif /* LINK_SCOPE_PROBE_H */
