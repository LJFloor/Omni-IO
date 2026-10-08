/*
 * SPDX-FileCopyrightText: 2026 LJFloor
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef WIFI_WATCHDOG_H
#define WIFI_WATCHDOG_H

// Detects a stalled WiFi data path (station still associated, but no traffic
// gets through, see issue #8) by pinging the gateway, and recovers by
// reconnecting WiFi, or restarting if reconnecting does not help.
void startWifiWatchdog();

#endif
