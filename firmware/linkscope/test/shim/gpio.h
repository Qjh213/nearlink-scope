/* 主机侧语法检查 shim：gpio */
#ifndef GPIO_SHIM_H
#define GPIO_SHIM_H

#include "pinctrl.h"

typedef enum {
    GPIO_DIRECTION_INPUT = 0,
    GPIO_DIRECTION_OUTPUT = 1,
} gpio_direction_t;

typedef enum {
    GPIO_LEVEL_LOW = 0,
    GPIO_LEVEL_HIGH = 1,
} gpio_level_t;

int uapi_gpio_set_dir(pin_t pin, gpio_direction_t dir);
int uapi_gpio_set_val(pin_t pin, gpio_level_t level);
int uapi_gpio_toggle(pin_t pin);

#endif
