/* 主机侧语法检查 shim：pinctrl */
#ifndef PINCTRL_SHIM_H
#define PINCTRL_SHIM_H

#include <stdint.h>

typedef uint32_t pin_t;

typedef enum {
    PIN_MODE_0 = 0,
    PIN_MODE_1 = 1,
} pin_mode_t;

#define PIN_NONE    0xFFFFFFFFu

int uapi_pin_set_mode(pin_t pin, pin_mode_t mode);

#endif
