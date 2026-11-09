/* 主机侧语法检查 shim：app_run 在真机上是一个 section 注册宏 */
#ifndef APP_INIT_SHIM_H
#define APP_INIT_SHIM_H

#define app_run(func) static void __attribute__((unused)) ls_app_run_hook(void) { func(); }

#endif
