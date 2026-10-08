/*
 * SPDX-FileCopyrightText: 2026 LJFloor
 * SPDX-License-Identifier: Apache-2.0
 */
#include <wifi_watchdog.h>
#include <WiFi.h>
#include <log_buffer.h>
#include "ping/ping_sock.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

namespace {
    constexpr uint32_t CHECK_INTERVAL_MS = 15000;
    constexpr uint8_t FAILED_CHECKS_BEFORE_RECONNECT = 2;
    constexpr uint8_t RECONNECTS_BEFORE_RESTART = 3;

    SemaphoreHandle_t s_pingDone = nullptr;
    volatile uint32_t s_pingReplies = 0;

    void onPingSuccess(esp_ping_handle_t, void *) { s_pingReplies = s_pingReplies + 1; }
    void onPingEnd(esp_ping_handle_t, void *) { xSemaphoreGive(s_pingDone); }

    // Returns true if the gateway answered at least one of three pings. Failing
    // to even start a ping session also counts as unreachable: during a stall
    // the stack runs out of resources for it (ESP_ERR_NO_MEM).
    bool gatewayReachable() {
        const IPAddress gw = WiFi.gatewayIP();
        if (gw == IPAddress(0, 0, 0, 0))
            return true;

        esp_ping_config_t config = ESP_PING_DEFAULT_CONFIG();
        IP_ADDR4(&config.target_addr, gw[0], gw[1], gw[2], gw[3]);
        config.count = 3;
        config.interval_ms = 300;
        config.timeout_ms = 1000;
        config.task_stack_size = 3072;

        esp_ping_callbacks_t callbacks = {};
        callbacks.on_ping_success = onPingSuccess;
        callbacks.on_ping_end = onPingEnd;

        esp_ping_handle_t session = nullptr;
        if (esp_ping_new_session(&config, &callbacks, &session) != ESP_OK)
            return false;
        const uint32_t repliesBefore = s_pingReplies;
        xSemaphoreTake(s_pingDone, 0);
        esp_ping_start(session);
        xSemaphoreTake(s_pingDone, pdMS_TO_TICKS(6000));
        esp_ping_stop(session);
        esp_ping_delete_session(session);
        return s_pingReplies != repliesBefore;
    }

    void watchdogTask(void *) {
        uint8_t failedChecks = 0;
        uint8_t reconnects = 0;
        for (;;) {
            vTaskDelay(pdMS_TO_TICKS(CHECK_INTERVAL_MS));
            // Only act while the driver claims to be connected; real
            // disconnects are already handled by WiFi auto-reconnect.
            if (WiFi.status() != WL_CONNECTED) {
                failedChecks = 0;
                continue;
            }
            if (gatewayReachable()) {
                failedChecks = 0;
                reconnects = 0;
                continue;
            }
            if (++failedChecks < FAILED_CHECKS_BEFORE_RECONNECT)
                continue;
            failedChecks = 0;
            if (++reconnects > RECONNECTS_BEFORE_RESTART) {
                addLogMessage("WiFi watchdog: gateway still unreachable after reconnects, restarting");
                delay(200);
                ESP.restart();
            }
            addLogMessage("WiFi watchdog: gateway unreachable while connected, reconnecting WiFi");
            WiFi.reconnect();
        }
    }
}

void startWifiWatchdog() {
    if (s_pingDone)
        return;
    s_pingDone = xSemaphoreCreateBinary();
    xTaskCreate(watchdogTask, "wifi_watchdog", 4096, nullptr, 1, nullptr);
}
