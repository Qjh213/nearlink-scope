/* 主机侧语法检查 shim：只覆盖 LinkScope 用到的那部分 OSAL */
#ifndef SOC_OSAL_SHIM_H
#define SOC_OSAL_SHIM_H

#include <stdio.h>
#include <stdint.h>

#define osal_printk(...) printf(__VA_ARGS__)

typedef struct osal_task osal_task;
typedef void *(*osal_kthread_handler)(const char *arg);

osal_task *osal_kthread_create(osal_kthread_handler handler, void *data, const char *name, unsigned int stack_size);
int osal_kthread_set_priority(osal_task *task, unsigned int priority);
void osal_kthread_lock(void);
void osal_kthread_unlock(void);
unsigned long osal_msleep(unsigned int msecs);
void osal_udelay(unsigned int usecs);
void osal_mdelay(unsigned int msecs);

#endif
