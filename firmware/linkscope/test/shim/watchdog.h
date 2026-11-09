/* 主机侧语法检查 shim：watchdog */
#ifndef WATCHDOG_SHIM_H
#define WATCHDOG_SHIM_H

#include "errcode.h"

errcode_t uapi_watchdog_kick(void);
errcode_t uapi_watchdog_init(unsigned int timeout);
errcode_t uapi_watchdog_enable(int mode);
errcode_t uapi_watchdog_disable(void);

#endif
