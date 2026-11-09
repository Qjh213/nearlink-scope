#!/usr/bin/env bash
# 主机侧单元测试（在 Ubuntu 虚拟机的普通 shell 里跑，不碰交叉工具链）
#
#   ./run_host_test.sh          # 用 gcc
#   CC=clang ./run_host_test.sh
set -euo pipefail
cd "$(dirname "$0")"

CC=${CC:-gcc}
OUT=${OUT:-/tmp}
CFLAGS="-std=c99 -Wall -Wextra"

echo "== 1/2 probe + stats 自检（和板端跑的是同一份代码）=="
$CC $CFLAGS -DLS_HOST_TEST -I.. ../selftest.c ../probe.c ../stats.c host_main.c -o "$OUT/ls_selftest"
"$OUT/ls_selftest"

echo "== 2/2 engine + loopback 集成测试 =="
$CC $CFLAGS -Ishim -I.. ../engine.c ../link_loopback.c ../probe.c ../stats.c host_engine_test.c -o "$OUT/ls_engine_test"
"$OUT/ls_engine_test"

echo "host tests passed"
