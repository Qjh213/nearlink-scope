/*
 * LinkScope 入口：角色分派 + 上报节奏。
 *
 * 角色（Kconfig 选择，一次编译一种）：
 *   initiator  A：发 REQ、收 RSP、算 RTT
 *   responder  B：收 REQ、立刻回 RSP
 *   loopback   本机自环：量噪声底
 *   timing     hello_timing：量时基/调度
 *   monitor    SLE被动seek：只听公开广播，不连接
 */
#include <stddef.h>
#include "common_def.h"
#include "soc_osal.h"
#include "tcxo.h"
#include "watchdog.h"
#include "app_init.h"
#include "linkscope.h"
#include "engine.h"
#include "selftest.h"
#include "timing.h"

/* ---- Kconfig 兜底（缺宏时不至于编译不过） ---- */
#ifndef CONFIG_LINK_SCOPE_CFG_ID
#define CONFIG_LINK_SCOPE_CFG_ID          0
#endif
#ifndef CONFIG_LINK_SCOPE_PROBE_HZ
#define CONFIG_LINK_SCOPE_PROBE_HZ        100
#endif
#ifndef CONFIG_LINK_SCOPE_PAYLOAD_BYTES
#define CONFIG_LINK_SCOPE_PAYLOAD_BYTES   40
#endif
#ifndef CONFIG_LINK_SCOPE_TIMEOUT_MS
#define CONFIG_LINK_SCOPE_TIMEOUT_MS      50
#endif
#ifndef CONFIG_LINK_SCOPE_WINDOW
#define CONFIG_LINK_SCOPE_WINDOW          1
#endif
#ifndef CONFIG_LINK_SCOPE_TASK_PRIO
#define CONFIG_LINK_SCOPE_TASK_PRIO       28
#endif
#ifndef CONFIG_LINK_SCOPE_STATS_EVERY
#define CONFIG_LINK_SCOPE_STATS_EVERY     200
#endif
#ifndef CONFIG_LINK_SCOPE_LOOP_US
#define CONFIG_LINK_SCOPE_LOOP_US         500
#endif
#ifndef CONFIG_LINK_SCOPE_RAW_PRINT
/* Kconfig 里关掉时 mconfig.h 不定义这个宏；这里兜底成"关"，不能是 1，
 * 否则 -Quiet 会被这个兜底又打开（12:19 那轮就踩了）。 */
#define CONFIG_LINK_SCOPE_RAW_PRINT       0
#endif
#ifndef CONFIG_LINK_SCOPE_RUN_SELFTEST
#define CONFIG_LINK_SCOPE_RUN_SELFTEST    1
#endif

#define LS_TASK_STACK_SIZE    0x2400
#define LS_LINK_WAIT_WARN_MS  5000u
/*
 * 忙等主循环会把 CPU 占满，idle 跑不到，看门狗没人喂就会 NMI 复位
 * （实测 ~18.4s 一次）。所以循环里自己喂狗，间隔取 500ms。
 * 只在"这一轮 tick/drain 正常走完"之后喂：真卡死时不再喂，看门狗仍能兜底。
 */
#define LS_WDT_KICK_MS        500u
#define LS_NO_RSP_WARN_MS     5000u   /* 已连上但一个 RSP 都没回来，5s 报一次 */
#define LS_POLL_MS            1000u   /* 链路 poll（谈连接间隔）的节拍：要早，不能等 5s */
#define LS_DBG_MS             5000u   /* 写确认时延的统计窗口与打印节拍 */

const link_ops_t *ls_link_ops_get(void);   /* 由当前角色对应的 link_*.c 提供 */

#if defined(CONFIG_LINK_SCOPE_ROLE_INITIATOR) || defined(CONFIG_LINK_SCOPE_ROLE_LOOPBACK)

static ls_engine_t g_engine;
static uint32_t g_printed;
static uint8_t g_warned_no_link;

void ls_on_frame(const uint8_t *data, uint16_t len, uint32_t t_recv_us)
{
    ls_engine_on_frame(&g_engine, data, len, t_recv_us);
}

#endif /* INITIATOR || LOOPBACK（ls_on_frame 只在这两种角色下被调用） */

static void print_banner(void)
{
    osal_printk("\r\n%s ===== LinkScope =====\r\n", LS_LOG);
#if defined(CONFIG_LINK_SCOPE_ROLE_INITIATOR)
    osal_printk("%s role=initiator (A)\r\n", LS_LOG);
#elif defined(CONFIG_LINK_SCOPE_ROLE_RESPONDER)
    osal_printk("%s role=responder (B)\r\n", LS_LOG);
#elif defined(CONFIG_LINK_SCOPE_ROLE_LOOPBACK)
    osal_printk("%s role=loopback\r\n", LS_LOG);
#elif defined(CONFIG_LINK_SCOPE_ROLE_TIMING)
    osal_printk("%s role=timing\r\n", LS_LOG);
#elif defined(CONFIG_LINK_SCOPE_ROLE_MONITOR)
    osal_printk("%s role=monitor\r\n", LS_LOG);
#else
    osal_printk("%s role=UNKNOWN (check Kconfig)\r\n", LS_LOG);
#endif
}

#if defined(CONFIG_LINK_SCOPE_ROLE_INITIATOR) || defined(CONFIG_LINK_SCOPE_ROLE_LOOPBACK)

static void print_summary(void)
{
    ls_summary_t sum = ls_stats_summary(&g_engine.st);

    osal_printk("# SUM n=%u p50=%u p90=%u p99=%u p999=%u max=%u mean=%u jit=%u "
                "sent=%u recv=%u to=%u dup=%u bad=%u drop=%u "
                "bproc=%u/%u dlt=%u/%u loss_ppm=%u\r\n",
        sum.n, sum.p50_us, sum.p90_us, sum.p99_us, sum.p999_us, sum.max_us, sum.mean_us, sum.jitter_us,
        sum.sent, sum.recv, sum.timeout, sum.dup, g_engine.st.bad_frame, g_engine.ring_drop,
        sum.bproc_mean_us, sum.bproc_max_us, sum.down_mean_us, sum.down_max_us, sum.loss_ppm);
}

static void report_drain(void)
{
    ls_sample_t s;

    while (ls_engine_pop(&g_engine, &s) != 0) {
#if (CONFIG_LINK_SCOPE_RAW_PRINT != 0)
        osal_printk("S,%u,%u,%u,%u,%u,%u\r\n",
            s.seq, s.t1_us, s.t4_us, s.rtt_us, s.bproc_us, s.dlt_us);
#endif
        g_printed++;
        if ((CONFIG_LINK_SCOPE_STATS_EVERY > 0) && (g_printed % (uint32_t)CONFIG_LINK_SCOPE_STATS_EVERY == 0u)) {
            print_summary();
        }
    }
}

static void engine_loop(void)
{
    const link_ops_t *link = ls_link_ops_get();
    uint32_t period_us;
    uint32_t start_ms;
    uint32_t last_kick_ms;
    uint32_t last_hb_ms;
    uint32_t last_diag_ms;
    uint32_t last_poll_ms;

    period_us = 1000000u / (uint32_t)CONFIG_LINK_SCOPE_PROBE_HZ;
    ls_engine_init(&g_engine, link, period_us, (uint32_t)CONFIG_LINK_SCOPE_TIMEOUT_MS * 1000u,
        (uint16_t)CONFIG_LINK_SCOPE_PAYLOAD_BYTES, (uint32_t)CONFIG_LINK_SCOPE_CFG_ID);
    ls_engine_set_window(&g_engine, (uint8_t)CONFIG_LINK_SCOPE_WINDOW);

    osal_printk("%s link=%s cfg=%u payload=%uB rate=%uHz win=%u timeout=%ums\r\n",
        LS_LOG, link->name, (uint32_t)CONFIG_LINK_SCOPE_CFG_ID,
        (uint32_t)g_engine.payload_bytes, (uint32_t)CONFIG_LINK_SCOPE_PROBE_HZ,
        (uint32_t)g_engine.window, (uint32_t)CONFIG_LINK_SCOPE_TIMEOUT_MS);
#if (CONFIG_LINK_SCOPE_RAW_PRINT != 0)
    osal_printk("# seq,t1_us,t4_us,rtt_us,bproc_us,dlt_us\r\n");
#endif

    if (link->start != NULL && link->start() != 0) {
        osal_printk("%s link start failed, loop still runs (waiting)\r\n", LS_LOG);
    }

    start_ms = (uint32_t)(uapi_tcxo_get_ms() & 0xFFFFFFFFu);
    last_kick_ms = start_ms;
    last_hb_ms = start_ms;
    last_diag_ms = start_ms;
    last_poll_ms = start_ms;
    while (1) {
        uint32_t now = ls_now_us();
        uint32_t now_ms = (uint32_t)(uapi_tcxo_get_ms() & 0xFFFFFFFFu);

        ls_engine_tick(&g_engine, now);
        report_drain();

        if ((uint32_t)(now_ms - last_kick_ms) >= LS_WDT_KICK_MS) {
            (void)uapi_watchdog_kick();
            last_kick_ms = now_ms;
        }

        if ((uint32_t)(now_ms - last_poll_ms) >= LS_POLL_MS) {
            /* 每 1s 给链路一次"调参/自检"机会：连接间隔要尽早谈下来，
             * 否则默认 12.5ms 那段会把停止等的积压撑爆（实测 25s 内积压 600 包）。 */
            last_poll_ms = now_ms;
            if (link->poll != NULL) {
                link->poll(now_ms);
            }
        }

        if ((uint32_t)(now_ms - last_diag_ms) >= LS_DBG_MS) {
            last_diag_ms = now_ms;
            if (link->dbg != NULL) {
                uint32_t cfm_n = 0u;
                uint32_t cfm_avg = 0u;
                uint32_t cfm_max = 0u;
                link->dbg(&cfm_n, &cfm_avg, &cfm_max);
                osal_printk("%s wr(5s): cfm_n=%u avg=%uus max=%uus\r\n", LS_LOG, cfm_n, cfm_avg, cfm_max);
            }
        }

        if (link->ready != NULL && link->ready() == 0u) {
            if (g_warned_no_link == 0u && (uint32_t)(now_ms - start_ms) > LS_LINK_WAIT_WARN_MS) {
                osal_printk("%s link not ready yet (%ums), still waiting\r\n", LS_LOG, LS_LINK_WAIT_WARN_MS);
                g_warned_no_link = 1u;
            }
        } else if ((g_engine.st.recv == 0u) && ((uint32_t)(now_ms - last_hb_ms) >= LS_NO_RSP_WARN_MS)) {
            /* 连上了但一个 RSP 都没收到：把发包侧的账摊开，判断是没发出去还是没回来。
             * to=超时数 unsol=无在途请求 bad=解析失败 dup=seq/t1 对不上 */
            last_hb_ms = now_ms;
            osal_printk("%s no rsp yet: sent=%u recv=%u to=%u unsol=%u bad=%u dup=%u\r\n", LS_LOG,
                g_engine.st.sent, g_engine.st.recv, g_engine.st.timeout,
                g_engine.st.unsolicited, g_engine.st.bad_frame, g_engine.st.dup);
            if (g_engine.rej_count != 0u) {
                /* 被拒帧的现场：age 就是这一帧的真实 RTT（同机精确），
                 * bproc 是 B 端从回调进来到发出 notify 的耗时（同一时钟，精确）。
                 * age 明显大于 bproc -> 延迟在空口/协议栈；bproc 本身就大 -> 延迟在 B 的回调调度。 */
                osal_printk("%s rej: rsp_seq=%u pend_seq=%u age_us=%u bproc_us=%u n=%u\r\n", LS_LOG,
                    (uint32_t)g_engine.rej_seq, (uint32_t)g_engine.rej_pend,
                    g_engine.rej_age_us, g_engine.rej_bproc_us, g_engine.rej_count);
            }
        }
        osal_udelay(CONFIG_LINK_SCOPE_LOOP_US);
    }
}

#endif /* INITIATOR || LOOPBACK */

static void *ls_task(const char *arg)
{
    unused(arg);

    print_banner();
    (void)uapi_tcxo_init();

#if (CONFIG_LINK_SCOPE_RUN_SELFTEST != 0)
    (void)ls_selftest_run();
#endif

#if defined(CONFIG_LINK_SCOPE_ROLE_TIMING)
    ls_timing_run();
    while (1) {
        osal_msleep(1000);
    }
#elif defined(CONFIG_LINK_SCOPE_ROLE_RESPONDER)
    ls_responder_run();
#elif defined(CONFIG_LINK_SCOPE_ROLE_MONITOR)
    ls_monitor_run();
#elif defined(CONFIG_LINK_SCOPE_ROLE_INITIATOR) || defined(CONFIG_LINK_SCOPE_ROLE_LOOPBACK)
    engine_loop();
#else
    osal_printk("%s no role selected, halt\r\n", LS_LOG);
    while (1) {
        osal_msleep(1000);
    }
#endif
    return NULL;
}

static void ls_entry(void)
{
    osal_task *task = NULL;

    osal_kthread_lock();
    task = osal_kthread_create((osal_kthread_handler)ls_task, NULL, "LinkScope", LS_TASK_STACK_SIZE);
    if (task != NULL) {
        (void)osal_kthread_set_priority(task, CONFIG_LINK_SCOPE_TASK_PRIO);
    } else {
        osal_printk("%s task create failed\r\n", LS_LOG);
    }
    osal_kthread_unlock();
}

app_run(ls_entry);
