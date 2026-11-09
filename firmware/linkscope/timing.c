/*
 * hello_timing 实现
 */
#include <stddef.h>
#include "common_def.h"
#include "soc_osal.h"
#include "tcxo.h"
#include "gpio.h"
#include "pinctrl.h"
#include "linkscope.h"
#include "timing.h"

#ifndef CONFIG_LINK_SCOPE_TIMING_GPIO
#define CONFIG_LINK_SCOPE_TIMING_GPIO 2
#endif

#define TIM_RES_N          2000u
#define TIM_MONO_N         20000u
#define TIM_COST_N         2000u
#define TIM_DELAY_N        200u
#define TIM_SLEEP_N        50u
#define TIM_GPIO_N         200u

static uint32_t g_ticks_per_us = 24u;   /* 实测校准后的值 */

static uint64_t now_count(void)
{
    return uapi_tcxo_get_count();
}

/* 用 TCXO 计数测一段代码的耗时（ticks） */
static uint64_t cost_ticks(void (*fn)(void), uint32_t n)
{
    uint64_t c1;
    uint64_t c2;
    uint32_t i;

    c1 = now_count();
    for (i = 0; i < n; i++) {
        fn();
    }
    c2 = now_count();
    return (c2 - c1);
}

static void tick_get_us(void)
{
    (void)uapi_tcxo_get_us();
}

static void tick_get_count(void)
{
    (void)uapi_tcxo_get_count();
}

static void measure_ticks_per_us(void)
{
    uint64_t c1;
    uint64_t c2;
    uint64_t d;

    c1 = now_count();
    (void)uapi_tcxo_delay_us(10000u);
    c2 = now_count();
    d = c2 - c1;
    if (d >= 10000u) {
        g_ticks_per_us = (uint32_t)(d / 10000u);
    }
}

static void measure_resolution(void)
{
    uint32_t i;
    uint32_t zero = 0;
    uint32_t min_delta = 0xFFFFFFFFu;
    uint32_t backwards = 0;
    uint32_t prev = ls_now_us();

    for (i = 0; i < TIM_RES_N; i++) {
        uint32_t t = ls_now_us();
        uint32_t d = (uint32_t)(t - prev);
        if (d == 0u) {
            zero++;
        } else if (d < min_delta) {
            min_delta = d;
        }
        prev = t;
    }
    osal_printk("%s [1a] get_us()    n=%u min_delta=%uus zero=%u/%u\r\n",
        LS_LOG, TIM_RES_N, min_delta, zero, TIM_RES_N);

    /* 原始计数（24 ticks/us）分辨率 */
    min_delta = 0xFFFFFFFFu;
    zero = 0;
    {
        uint64_t prevc = now_count();
        for (i = 0; i < TIM_RES_N; i++) {
            uint64_t c = now_count();
            uint32_t d = (uint32_t)(c - prevc);
            if (d == 0u) {
                zero++;
            } else if (d < min_delta) {
                min_delta = d;
            }
            prevc = c;
        }
    }
    osal_printk("%s [1b] get_count() n=%u min_delta=%uticks zero=%u/%u (ticks/us=%u)\r\n",
        LS_LOG, TIM_RES_N, min_delta, zero, TIM_RES_N, g_ticks_per_us);

    /* 单调性 */
    prev = ls_now_us();
    for (i = 0; i < TIM_MONO_N; i++) {
        uint32_t t = ls_now_us();
        if ((int32_t)(t - prev) < 0) {
            backwards++;
        }
        prev = t;
    }
    osal_printk("%s [1c] monotonic: n=%u backwards=%u\r\n", LS_LOG, TIM_MONO_N, backwards);
}

static void measure_call_cost(void)
{
    uint64_t ticks_us;
    uint64_t ticks_cnt;

    ticks_us = cost_ticks(tick_get_us, TIM_COST_N);
    ticks_cnt = cost_ticks(tick_get_count, TIM_COST_N);
    osal_printk("%s [2] call cost: get_us=%u.%02uus get_count=%u.%02uus (n=%u)\r\n",
        LS_LOG,
        (uint32_t)(ticks_us / (g_ticks_per_us * TIM_COST_N)),
        (uint32_t)((ticks_us * 100u) / (g_ticks_per_us * TIM_COST_N) % 100u),
        (uint32_t)(ticks_cnt / (g_ticks_per_us * TIM_COST_N)),
        (uint32_t)((ticks_cnt * 100u) / (g_ticks_per_us * TIM_COST_N) % 100u),
        TIM_COST_N);
}

static void measure_delay_us(void)
{
    static const uint32_t targets[] = {1u, 10u, 100u, 1000u};
    uint32_t k;

    for (k = 0; k < (uint32_t)(sizeof(targets) / sizeof(targets[0])); k++) {
        uint32_t i;
        uint32_t target = targets[k];
        uint32_t min_err = 0xFFFFFFFFu;
        uint32_t max_err = 0;
        uint64_t sum_err = 0;

        for (i = 0; i < TIM_DELAY_N; i++) {
            uint64_t c1 = now_count();
            uint32_t actual;
            uint32_t err;
            (void)uapi_tcxo_delay_us(target);
            actual = (uint32_t)((now_count() - c1) / g_ticks_per_us);
            err = (actual > target) ? (actual - target) : (target - actual);
            if (err < min_err) {
                min_err = err;
            }
            if (err > max_err) {
                max_err = err;
            }
            sum_err += err;
        }
        osal_printk("%s [3] delay_us(%u): err min=%u mean=%u max=%u us (n=%u)\r\n",
            LS_LOG, target, min_err, (uint32_t)(sum_err / TIM_DELAY_N), max_err, TIM_DELAY_N);
    }
}

static void measure_sleep(void)
{
    uint32_t i;
    uint32_t min_us = 0xFFFFFFFFu;
    uint32_t max_us = 0;
    uint64_t sum_us = 0;

    for (i = 0; i < TIM_SLEEP_N; i++) {
        uint64_t c1 = now_count();
        uint32_t actual_us;
        osal_msleep(1);
        actual_us = (uint32_t)((now_count() - c1) / g_ticks_per_us);
        if (actual_us < min_us) {
            min_us = actual_us;
        }
        if (actual_us > max_us) {
            max_us = actual_us;
        }
        sum_us += actual_us;
    }
    osal_printk("%s [4] osal_msleep(1): mean=%u min=%u max=%u us (n=%u) -> 调度粒度\r\n",
        LS_LOG, (uint32_t)(sum_us / TIM_SLEEP_N), min_us, max_us, TIM_SLEEP_N);

    /* udelay 的调度粒度（忙等，不参与调度） */
    {
        uint64_t c1 = now_count();
        osal_udelay(1000);
        osal_printk("%s [4b] osal_udelay(1000): actual=%u us\r\n",
            LS_LOG, (uint32_t)((now_count() - c1) / g_ticks_per_us));
    }
}

static void measure_gpio(void)
{
    uint32_t i;
    uint32_t min_t = 0xFFFFFFFFu;
    uint32_t max_t = 0;
    uint64_t sum_t = 0;

    uapi_pin_set_mode(CONFIG_LINK_SCOPE_TIMING_GPIO, PIN_MODE_0);
    (void)uapi_gpio_set_dir(CONFIG_LINK_SCOPE_TIMING_GPIO, GPIO_DIRECTION_OUTPUT);
    (void)uapi_gpio_set_val(CONFIG_LINK_SCOPE_TIMING_GPIO, GPIO_LEVEL_LOW);

    for (i = 0; i < TIM_GPIO_N; i++) {
        uint64_t c1;
        uint64_t c2;
        uint32_t t;
        (void)uapi_gpio_set_val(CONFIG_LINK_SCOPE_TIMING_GPIO, GPIO_LEVEL_HIGH);
        c1 = now_count();
        (void)uapi_gpio_set_val(CONFIG_LINK_SCOPE_TIMING_GPIO, GPIO_LEVEL_LOW);
        c2 = now_count();
        t = (uint32_t)((c2 - c1) / g_ticks_per_us);
        if (t < min_t) {
            min_t = t;
        }
        if (t > max_t) {
            max_t = t;
        }
        sum_t += t;
    }
    osal_printk("%s [5] gpio%u high->low + 打点: mean=%u min=%u max=%u us (n=%u)\r\n",
        LS_LOG, (uint32_t)CONFIG_LINK_SCOPE_TIMING_GPIO, (uint32_t)(sum_t / TIM_GPIO_N), min_t, max_t,
        (uint32_t)TIM_GPIO_N);
}

void ls_timing_run(void)
{
    errcode_t ret;

    osal_printk("\r\n%s ===== hello_timing =====\r\n", LS_LOG);
    ret = uapi_tcxo_init();
    osal_printk("%s tcxo_init ret=0x%x\r\n", LS_LOG, (uint32_t)ret);
    if (ret != ERRCODE_SUCC) {
        osal_printk("%s tcxo init failed, timing test aborted\r\n", LS_LOG);
        return;
    }

    measure_ticks_per_us();
    osal_printk("%s ticks_per_us=%u (SDK 期望 24)\r\n", LS_LOG, g_ticks_per_us);

    measure_resolution();
    measure_call_cost();
    measure_delay_us();
    measure_sleep();
    measure_gpio();

    osal_printk("%s ===== hello_timing done =====\r\n", LS_LOG);
}
