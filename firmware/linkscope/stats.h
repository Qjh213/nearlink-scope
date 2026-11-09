/*
 * LinkScope 统计：对数分桶直方图 + 分位数 + 抖动 + 丢包计数（纯逻辑，不依赖 SDK）
 *
 * 为什么不用缓存全部样本：H3863 SRAM 有限，1 万样本就是 40KB。
 * 8 桶/倍频程、1us..262ms 共 144 桶，每桶 u32 计数 = 576B，完全放得下；
 * 桶内按线性插值估分位数，最坏相对误差约 1/16（≈6%），对"毫秒级差异"足够。
 */
#ifndef LINK_SCOPE_STATS_H
#define LINK_SCOPE_STATS_H

#include <stdint.h>

#define LS_HIST_SUB_PER_OCTAVE     8u
#define LS_HIST_OCTAVES            18u
#define LS_HIST_BUCKETS            (LS_HIST_SUB_PER_OCTAVE * LS_HIST_OCTAVES)

typedef struct {
    /* 链路计数 */
    uint32_t sent;          /* 发出 REQ 数 */
    uint32_t recv;          /* 收到有效 RSP 数 */
    uint32_t timeout;       /* 超时未回 */
    uint32_t dup;           /* 重复/过期的 RSP */
    uint32_t unsolicited;   /* 没有在途请求时收到的 RSP */
    uint32_t bad_frame;     /* 解析失败的帧 */

    /* RTT 分布 */
    uint32_t hist[LS_HIST_BUCKETS];
    uint32_t n;             /* 有效 RTT 样本数 */
    uint32_t min_us;
    uint32_t max_us;
    uint64_t sum_us;

    /* 抖动：相邻样本 RTT 差分的平均绝对值（类 RFC3550） */
    uint32_t prev_rtt_us;
    uint8_t has_prev;
    uint64_t jitter_sum_us;
    uint32_t jitter_n;

    /* 分段：B 端处理 (t3-t2) 精确；t4-t3 含未知时钟偏差 δ，只看趋势/抖动 */
    uint64_t bproc_sum_us;
    uint32_t bproc_max_us;
    uint64_t down_sum_us;
    uint32_t down_max_us;
} ls_stats_t;

typedef struct {
    uint32_t n;
    uint32_t p50_us;
    uint32_t p90_us;
    uint32_t p99_us;
    uint32_t p999_us;
    uint32_t max_us;
    uint32_t mean_us;
    uint32_t jitter_us;
    uint32_t sent;
    uint32_t recv;
    uint32_t timeout;
    uint32_t dup;
    uint32_t unsolicited;
    uint32_t loss_ppm;
    uint32_t bproc_mean_us;
    uint32_t bproc_max_us;
    uint32_t down_mean_us;
    uint32_t down_max_us;
} ls_summary_t;

void ls_stats_reset(ls_stats_t *s);
void ls_stats_on_rtt(ls_stats_t *s, uint32_t rtt_us);
void ls_stats_on_segments(ls_stats_t *s, uint32_t bproc_us, uint32_t down_us);

/* 桶映射：us -> [0, LS_HIST_BUCKETS)，以及桶的上下界 */
uint32_t ls_hist_index(uint32_t us);
void ls_hist_bounds(uint32_t idx, uint32_t *lo_us, uint32_t *hi_us);

/* q_per_mille: 500/900/990/999 */
uint32_t ls_hist_quantile(const ls_stats_t *s, uint32_t q_per_mille);

ls_summary_t ls_stats_summary(const ls_stats_t *s);
uint32_t ls_loss_ppm(uint32_t sent, uint32_t recv);

#endif /* LINK_SCOPE_STATS_H */
