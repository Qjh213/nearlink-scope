# =============================================================================
# LinkScope 构建脚本（作为 application/samples/products/linkscope 使用）
# =============================================================================

set(SOURCES_LIST
    ${CMAKE_CURRENT_SOURCE_DIR}/linkscope_main.c
    ${CMAKE_CURRENT_SOURCE_DIR}/probe.c
    ${CMAKE_CURRENT_SOURCE_DIR}/stats.c
    ${CMAKE_CURRENT_SOURCE_DIR}/engine.c
    ${CMAKE_CURRENT_SOURCE_DIR}/selftest.c
    ${CMAKE_CURRENT_SOURCE_DIR}/timing.c
)

set(HEADER_LIST ${CMAKE_CURRENT_SOURCE_DIR})

if(DEFINED CONFIG_LINK_SCOPE_ROLE_INITIATOR)
    if(DEFINED CONFIG_LINK_SCOPE_LINK_BLE)
        list(APPEND SOURCES_LIST ${CMAKE_CURRENT_SOURCE_DIR}/link_ble_initiator.c)
    else()
        list(APPEND SOURCES_LIST ${CMAKE_CURRENT_SOURCE_DIR}/link_sle_initiator.c)
    endif()
elseif(DEFINED CONFIG_LINK_SCOPE_ROLE_RESPONDER)
    if(DEFINED CONFIG_LINK_SCOPE_LINK_BLE)
        list(APPEND SOURCES_LIST ${CMAKE_CURRENT_SOURCE_DIR}/link_ble_responder.c)
    else()
        list(APPEND SOURCES_LIST
            ${CMAKE_CURRENT_SOURCE_DIR}/link_sle_responder.c
            ${CMAKE_CURRENT_SOURCE_DIR}/sle/sle_uart_server.c
            ${CMAKE_CURRENT_SOURCE_DIR}/sle/sle_uart_server_adv.c
        )
        list(APPEND HEADER_LIST ${CMAKE_CURRENT_SOURCE_DIR}/sle)
    endif()
elseif(DEFINED CONFIG_LINK_SCOPE_ROLE_LOOPBACK)
    list(APPEND SOURCES_LIST ${CMAKE_CURRENT_SOURCE_DIR}/link_loopback.c)
elseif(DEFINED CONFIG_LINK_SCOPE_ROLE_MONITOR)
    list(APPEND SOURCES_LIST ${CMAKE_CURRENT_SOURCE_DIR}/link_sle_monitor.c)
endif()

set(SOURCES "${SOURCES}" ${SOURCES_LIST} PARENT_SCOPE)
set(PUBLIC_HEADER "${PUBLIC_HEADER}" ${HEADER_LIST} PARENT_SCOPE)
