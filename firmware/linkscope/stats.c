/*
 * LinkScope 统计实现（纯逻辑，不依赖 SDK）
 */
#include <stddef.h>
#include "stats.h"

static void stats_zero(void *p, uint32_t len)
{
    uint8_t *b = (uint8_t *)p;
    uint32_t i;
    for (i = 0; i < len; i++) {
        b[i] = 0;
    }
}

void ls_stats_reset(ls_stats_t *s)
{
    if (s == NULL) {
        return;
    }
    stats_zero(s, (uint32_t)sizeof(*s));
}

uint32_t ls_hist_index(uint32_t us)
{
    uint32_t oct = 0;
    uint32_t base;
    uint32_t sub;
    uint32_t v;

    if (us == 0u) {
        return 0u;
    }
    v = us;
    while (v > 1u) {
        v >>= 1;
        oct++;
    }
    if (oct >= LS_HIST_OCTAVES) {
        return LS_HIST_BUCKETS - 1u;
    }
    base = 1u << oct;
    sub = ((us - base) * LS_HIST_SUB_PER_OCTAVE) >> oct;
    if (sub >= LS_HIST_SUB_PER_OCTAVE) {
        sub = LS_HIST_SUB_PER_OCTAVE - 1u;
    }
    return oct * LS_HIST_SUB_PER_OCTAVE + sub;
}

void ls_hist_bounds(uint32_t idx, uint32_t *lo_us, uint32_t *hi_us)
{
    uint32_t oct;
    uint32_t sub;
    uint32_t base;
    uint32_t lo;
    uint32_t hi;

    if (lo_us == NULL || hi_us == NULL) {
        return;
    }
    if (idx >= LS_HIST_BUCKETS) {
        idx = LS_HIST_BUCKETS - 1u;
    }
    oct = idx / LS_HIST_SUB_PER_OCTAVE;
    sub = idx % LS_HIST_SUB_PER_OCTAVE;
    base = 1u << oct;
    lo = base + (base * sub) / LS_HIST_SUB_PER_OCTAVE;
    hi = base + (base * (sub + 1u)) / LS_HIST_SUB_PER_OCTAVE;
    if (hi <= lo) {
        hi = lo + 1u;
    }
    *lo_us = lo;
    *hi_us = hi;
}

void ls_stats_on_rtt(ls_stats_t *s, uint32_t rtt_us)
{
    uint32_t idx;

    if (s == NULL) {
        return;
    }
    idx = ls_hist_index(rtt_us);
    s->hist[idx]++;
    s->n++;
    s->sum_us += rtt_us;
    if (s->n == 1u || rtt_us < s->min_us) {
        s->min_us = rtt_us;
    }
    if (rtt_us > s->max_us) {
        s->max_us = rtt_us;
    }
    if (s->has_prev != 0u) {
        uint32_t diff = (rtt_us > s->prev_rtt_us) ? (rtt_us - s->prev_rtt_us) : (s->prev_rtt_us - rtt_us);
        s->jitter_sum_us += diff;
        s->jitter_n++;
    }
    s->prev_rtt_us = rtt_us;
    s->has_prev = 1u;
}

void ls_stats_on_segments(ls_stats_t *s, uint32_t bproc_us, uint32_t down_us)
{
    if (s == NULL) {
        return;
    }
    s->bproc_sum_us += bproc_us;
    if (bproc_us > s->bproc_max_us) {
        s->bproc_max_us = bproc_us;
    }
    s->down_sum_us += down_us;
    if (down_us > s->down_max_us) {
        s->down_max_us = down_us;
    }
}

uint32_t ls_hist_quantile(const ls_stats_t *s, uint32_t q_per_mille)
{
    uint32_t total;
    uint32_t target;
    uint32_t cum = 0;
    uint32_t i;

    if (s == NULL || s->n == 0u) {
        return 0u;
    }
    if (q_per_mille > 1000u) {
        q_per_mille = 1000u;
    }
    total = s->n;
    target = (uint32_t)(((uint64_t)total * q_per_mille + 999u) / 1000u);
    if (target == 0u) {
        target = 1u;
    }
    for (i = 0; i < LS_HIST_BUCKETS; i++) {
        uint32_t c = s->hist[i];
        uint32_t lo;
        uint32_t hi;

        if (c == 0u) {
            continue;
        }
        if (cum + c >= target) {
            uint32_t v;
            ls_hist_bounds(i, &lo, &hi);
            /* 桶内线性插值，取秩中点，避免整体偏到桶上沿 */
            v = lo + (uint32_t)(((uint64_t)(hi - lo) * ((uint64_t)(target - cum) * 2u - 1u)) / ((uint64_t)c * 2u));
            if (v > s->max_us) {
                v = s->max_us;                 /* 插值不应超过实测最大值 */
            }
            return v;
        }
        cum += c;
    }
    return s->max_us;
}

uint32_t ls_loss_ppm(uint32_t sent, uint32_t recv)
{
    uint32_t lost;

    if (sent == 0u) {
        return 0u;
    }
    lost = (sent > recv) ? (sent - recv) : 0u;
    return (uint32_t)(((uint64_t)lost * 1000000u) / sent);
}

ls_summary_t ls_stats_summary(const ls_stats_t *s)
{
    ls_summary_t out;

    stats_zero(&out, (uint32_t)sizeof(out));
    if (s == NULL) {
        return out;
    }
    out.n = s->n;
    out.sent = s->sent;
    out.recv = s->recv;
    out.timeout = s->timeout;
    out.dup = s->dup;
    out.unsolicited = s->unsolicited;
    out.max_us = s->max_us;
    out.p50_us = ls_hist_quantile(s, 500u);
    out.p90_us = ls_hist_quantile(s, 900u);
    out.p99_us = ls_hist_quantile(s, 990u);
    out.p999_us = ls_hist_quantile(s, 999u);
    out.mean_us = (s->n == 0u) ? 0u : (uint32_t)(s->sum_us / s->n);
    out.jitter_us = (s->jitter_n == 0u) ? 0u : (uint32_t)(s->jitter_sum_us / s->jitter_n);
    out.loss_ppm = ls_loss_ppm(s->sent, s->recv);
    out.bproc_mean_us = (s->n == 0u) ? 0u : (uint32_t)(s->bproc_sum_us / s->n);
    out.bproc_max_us = s->bproc_max_us;
    out.down_mean_us = (s->n == 0u) ? 0u : (uint32_t)(s->down_sum_us / s->n);
    out.down_max_us = s->down_max_us;
    return out;
}
