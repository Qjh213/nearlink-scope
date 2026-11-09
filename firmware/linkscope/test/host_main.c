/*
 * 主机侧自检入口：把板端 selftest 原样搬到 PC 上跑一遍。
 * 用法（见同目录 run_host_test.ps1）：
 *   zig cc -DLS_HOST_TEST -std=c99 -Wall -Wextra -I.. selftest.c probe.c stats.c host_main.c -o ls_selftest.exe
 */
#include "selftest.h"

int main(void)
{
    return ls_selftest_run();
}
