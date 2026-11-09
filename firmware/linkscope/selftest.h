/*
 * 板端自检：把 probe/stats 的纯逻辑在本机（H3863）上跑一遍。
 * 目的：编译进固件就能验证算法，不依赖 PC 端工具链，也不占用空口。
 */
#ifndef LINK_SCOPE_SELFTEST_H
#define LINK_SCOPE_SELFTEST_H

/* 返回失败用例数，0 表示全过 */
int ls_selftest_run(void);

#endif /* LINK_SCOPE_SELFTEST_H */
