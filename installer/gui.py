"""Cross-platform GUI and CLI installer/uninstaller for Cmdiqo Agent.

Detects current installation status, presents upgrade/reinstall/uninstall options,
supports interactive WebSocket hub connection testing, and manages services.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import logging
import os
import platform
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from installer.backend import (
    IS_WINDOWS,
    compare_versions,
    get_bundled_version,
    get_existing_config,
    get_installed_version,
    get_platform_paths,
    get_service_status,
    install_or_upgrade,
    is_admin,
    perform_uninstall,
    request_elevation,
    restart_service,
    check_hub_connection,
    start_service,
    stop_service,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cmdiqo-installer-gui")

# When running as a frozen windowed binary, also log to a file next to the executable
# so failures can be diagnosed when the process silently closes with no console.
if getattr(sys, "frozen", False):
    try:
        import tempfile
        _log_dir = os.path.join(os.environ.get("TEMP", tempfile.gettempdir()), "cmdiqo")
        os.makedirs(_log_dir, exist_ok=True)
        _log_file = os.path.join(_log_dir, "installer.log")
        _fh = logging.FileHandler(_log_file, encoding="utf-8")
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logging.getLogger().addHandler(_fh)
        log.info("=== Cmdiqo Installer started (frozen) ===")
        log.info("Log file: %s", _log_file)
    except Exception:
        pass

HAS_TKINTER = False
try:
    import tkinter as tk
    from tkinter import messagebox, ttk
    HAS_TKINTER = True
except ImportError as exc:
    log.error(
        "Tkinter is unavailable; the fallback GUI cannot start. "
        "Install your distribution's Python Tk package and run the installer again: %s",
        exc,
    )


# ==============================================================================
# HTML / CSS / JS UI FOR MODERN WEBVIEW
# ==============================================================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Cmdiqo Agent Setup</title>
  <style>
    :root {
      --bg: #090d16;
      --card-bg: #111827;
      --card-border: #1f2937;
      --input-bg: #1f293d;
      --input-border: #374151;
      --text: #f9fafb;
      --text-dim: #9ca3af;
      --accent: #3b82f6;
      --accent-hover: #2563eb;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --danger-hover: #dc2626;
      --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--font);
      background-color: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
      user-select: none;
    }

    .header {
      background: linear-gradient(180deg, #1e293b 0%, #0f172a 100%);
      padding: 16px 24px;
      border-bottom: 1px solid var(--card-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .header-brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand-logo {
      width: 32px;
      height: 32px;
      border-radius: 8px;
      object-fit: contain;
    }
    .brand-title {
      font-size: 18px;
      font-weight: 700;
      letter-spacing: -0.01em;
    }
    .badge {
      display: inline-block;
      padding: 2px 8px;
      font-size: 11px;
      font-weight: 600;
      border-radius: 9999px;
      background: rgba(59, 130, 246, 0.2);
      color: #60a5fa;
      border: 1px solid rgba(59, 130, 246, 0.4);
    }

    .content {
      flex: 1;
      padding: 24px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      gap: 16px;
    }

    .card {
      width: 100%;
      max-width: 836px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 20px;
    }
    .loading-card {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 14px;
      text-align: center;
    }
    .spinner {
      width: 34px;
      height: 34px;
      border: 4px solid rgba(59, 130, 246, 0.25);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    .banner-icon {
      font-size: 36px;
      line-height: 1;
    }
    .banner-heading {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 16px;
    }
    .banner-title {
      font-size: 16px;
      font-weight: 600;
      line-height: 1.4;
    }
    .banner-desc {
      font-size: 13px;
      color: var(--text-dim);
      line-height: 1.5;
    }

    .form-group {
      margin-bottom: 16px;
    }
    .form-group:last-child {
      margin-bottom: 0;
    }
    label {
      display: block;
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-dim);
      margin-bottom: 6px;
    }
    .input-row {
      display: flex;
      gap: 8px;
    }
    input[type="text"], input[type="password"] {
      width: 100%;
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      color: var(--text);
      padding: 10px 14px;
      font-size: 13px;
      border-radius: 6px;
      outline: none;
      transition: border-color 0.15s;
    }
    input[type="text"]:focus, input[type="password"]:focus {
      border-color: var(--accent);
    }
    .checkbox-label {
      display: flex;
      align-items: center;
      gap: 10px;
      cursor: pointer;
      text-transform: none;
      font-size: 13px;
      color: var(--text);
      font-weight: 400;
    }
    .checkbox-label input {
      accent-color: var(--accent);
      width: 16px;
      height: 16px;
    }

    .footer {
      background: #0f172a;
      border-top: 1px solid var(--card-border);
      padding: 16px 24px;
      display: flex;
      justify-content: flex-end;
      gap: 12px;
    }

    button {
      padding: 10px 18px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      border: none;
      transition: all 0.15s ease;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
    }
    .btn-primary { background: var(--accent); color: white; }
    .btn-primary:hover { background: var(--accent-hover); }
    .btn-secondary { background: #374151; color: #e5e7eb; }
    .btn-secondary:hover { background: #4b5563; }
    .btn-danger { background: var(--danger); color: white; }
    .btn-danger:hover { background: var(--danger-hover); }

    .terminal-box {
      background: #030712;
      border: 1px solid #1f2937;
      border-radius: 8px;
      padding: 14px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
      color: #10b981;
      height: 180px;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-all;
    }

    .hidden { display: none !important; }
    .service-status {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin-top: 12px;
      background: var(--card-bg);
      font-size: 13px;
      font-weight: 600;
    }
    .service-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
    }
    .service-dot.running { background: #22c55e; box-shadow: 0 0 9px #22c55e; }
    .service-dot.stopped { background: #3b82f6; }
    .service-dot.not-installed { background: #ef4444; }
    .service-controls-card {
      padding: 14px;
      background: #0f172a;
      border: 1px solid var(--card-border);
      border-radius: 8px;
    }
    .service-controls-title {
      margin-bottom: 10px;
      color: var(--text-dim);
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .primary-actions, .service-controls-actions {
      display: flex;
      justify-content: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    button:disabled { cursor: not-allowed; opacity: 0.45; }
  </style>
</head>
<body>

  <div class="header">
    <div class="header-brand">
      <img class="brand-logo" src="__BRAND_ICON_DATA_URI__" alt="Cmdiqo">
      <div>
        <div class="brand-title">Cmdiqo Agent Setup</div>
        <span class="badge" id="badge_version">v0.9.0</span>
      </div>
    </div>
    <div id="installed_badge" class="badge hidden" style="display: none; background: rgba(16,185,129,0.15); color: #34d399; border-color: rgba(16,185,129,0.3);"></div>
  </div>

  <div class="content">
    <div id="view_loading" class="card loading-card">
      <div class="spinner" id="loading_spinner" role="status" aria-label="Loading"></div>
      <div class="banner-title" id="loading_message">Loading Cmdiqo Agent Setup...</div>
      <div class="banner-desc" id="loading_detail">Initializing the installer window.</div>
    </div>

    <!-- VIEW 1: SAME VERSION ALREADY INSTALLED -->
    <div id="view_same" class="card hidden">
      <div class="banner-heading">
        <div class="banner-icon">✓</div>
        <div class="banner-title" id="same_message">You have already installed version 0.9.0</div>
      </div>
      <div class="service-status" id="service_status"><span class="service-dot not-installed"></span><span>not installed</span></div>
      <div class="banner-desc">The Cmdiqo agent is installed and running as a native background service. What would you like to do?</div>
      <div class="primary-actions" style="margin-top: 20px;">
        <button type="button" class="btn-primary" onclick="reinstall()">Reinstall / Repair</button>
        <button type="button" class="btn-secondary" onclick="showView('view_form')">Reconfigure Settings</button>
        <button type="button" class="btn-danger" onclick="triggerUninstall()">Uninstall Service</button>
      </div>
    </div>

    <div id="view_service_controls" class="card service-controls-card hidden">
      <div class="service-controls-title">Service controls</div>
      <div class="service-controls-actions">
        <button type="button" class="btn-primary" id="btn_start_service" onclick="manageService('start')" disabled>&#9658; Start Service</button>
        <button type="button" class="btn-secondary" id="btn_restart_service" onclick="manageService('restart')" disabled>&#9632;&#9658; Restart Service</button>
        <button type="button" class="btn-danger" id="btn_stop_service" onclick="manageService('stop')" disabled>&#9632; Stop Service</button>
      </div>
    </div>

    <!-- VIEW 2: OLDER VERSION INSTALLED (UPGRADE) -->
    <div id="view_upgrade" class="card hidden">
      <div class="banner-icon">⬆</div>
      <div class="banner-title" id="upgrade_message">
        You're going to upgrade from 0.8.0 to 0.9.0. Do you want to continue?
      </div>
      <div class="banner-desc">Existing secrets and configuration will be preserved. The running agent service will be safely updated and restarted.</div>
      <div style="margin-top: 24px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
        <button type="button" class="btn-primary" style="padding: 10px 24px;" onclick="confirmUpgrade()">YES (Upgrade)</button>
        <button type="button" class="btn-danger" onclick="triggerUninstall()">Uninstall</button>
        <button type="button" class="btn-secondary" style="padding: 10px 24px;" onclick="closeWindow()">NO (Cancel)</button>
      </div>
    </div>

    <!-- VIEW 3: NEWER VERSION ALREADY INSTALLED -->
    <div id="view_newer" class="card hidden">
      <div class="banner-icon">ℹ</div>
      <div class="banner-title" id="newer_message">You have already installed newer 1.0.0</div>
      <div class="banner-desc">This installer contains an older release. No upgrade is required.</div>
      <div style="margin-top: 24px; display: flex; gap: 12px;">
        <button type="button" class="btn-danger" onclick="triggerUninstall()">Uninstall Service</button>
        <button type="button" class="btn-primary" onclick="closeWindow()">CLOSE</button>
      </div>
    </div>

    <!-- VIEW 4: CONFIGURATION / FRESH INSTALL FORM -->
    <div id="view_form" class="card hidden">
      <div class="form-group">
        <label for="hub_url">Hub WebSocket URL</label>
        <div class="input-row">
          <input type="text" id="hub_url" placeholder="ws://192.168.1.10:8765" value="ws://192.168.1.10:8765">
          <button type="button" class="btn-secondary" style="white-space: nowrap;" onclick="testConnection()">Test</button>
        </div>
        <div id="test_status" style="font-size: 11px; margin-top: 4px; display: none;"></div>
      </div>

      <div class="form-group">
        <label for="token">Agent Token (Shared Secret)</label>
        <div class="input-row">
          <input type="password" id="token" placeholder="Enter AGENT_TOKEN configured on Hub">
          <button type="button" class="btn-secondary" style="white-space: nowrap; padding: 10px 14px;" onclick="toggleTokenVisibility()" id="btn_toggle_token" title="Show / Hide token">Show</button>
        </div>
      </div>

      <div class="form-group">
        <label for="name">Agent Name</label>
        <input type="text" id="name" placeholder="Target host name in Slack commands">
      </div>

      <div class="form-group">
        <label class="checkbox-label" for="notify">
          <input type="checkbox" id="notify">
          Enable Slack notification on agent connect/disconnect
        </label>
      </div>

      <div class="form-group">
        <label for="install_dir">Install Directory</label>
        <input type="text" id="install_dir" readonly>
      </div>
    </div>

    <!-- VIEW 5: LOG TERMINAL & PROGRESS -->
    <div id="view_progress" class="card hidden">
      <div class="spinner" role="status" aria-label="Working"></div>
      <div class="banner-title" id="progress_title">Executing installation...</div>
      <div class="terminal-box" id="log_terminal"></div>
    </div>

  </div>

  <div class="footer hidden" id="footer_bar">
    <button type="button" class="btn-secondary" id="btn_cancel" onclick="returnToMainScreen()">Back</button>
    <button type="button" class="btn-primary" id="btn_install" onclick="submitInstall()">Install & Start Service</button>
  </div>

  <script>
    let INITIAL_STATE = null;
    try {
      INITIAL_STATE = __INITIAL_STATE_JSON__;
    } catch (e) {
      INITIAL_STATE = null;
    }

    function showView(viewId) {
      ['view_loading', 'view_same', 'view_service_controls', 'view_upgrade', 'view_newer', 'view_form', 'view_progress'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add('hidden');
      });
      const target = document.getElementById(viewId);
      if (target) target.classList.remove('hidden');
      if (viewId === 'view_same') {
        document.getElementById('view_service_controls').classList.remove('hidden');
      }

      const footer = document.getElementById('footer_bar');
      if (footer) {
        if (viewId === 'view_form') {
          footer.classList.remove('hidden');
        } else {
          footer.classList.add('hidden');
        }
      }
    }

    function toggleTokenVisibility() {
      const input = document.getElementById('token');
      const btn = document.getElementById('btn_toggle_token');
      if (!input || !btn) return;
      if (input.type === 'password') {
        input.type = 'text';
        btn.textContent = 'Hide';
      } else {
        input.type = 'password';
        btn.textContent = 'Show';
      }
    }

    function closeWindow() {
      if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.close_window === 'function') {
        window.pywebview.api.close_window();
      } else {
        window.close();
      }
    }

    function returnToMainScreen() {
      syncWithBackend(true);
    }

    function confirmUpgrade() {
      showView('view_progress');
      appendLog('[*] Upgrading Cmdiqo Agent...');
      if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.confirm_upgrade === 'function') {
        window.pywebview.api.confirm_upgrade();
      }
    }

    function reinstall() {
      showView('view_progress');
      appendLog('[*] Reinstalling Cmdiqo Agent...');
      if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.reinstall === 'function') {
        window.pywebview.api.reinstall();
      }
    }

    function triggerUninstall() {
      if (!confirm("Are you sure you want to uninstall Cmdiqo Remote Agent and stop its background service?")) {
        return;
      }
      const purge = confirm("Do you also want to remove all configuration, secrets, and log files?\\n\\n- Click OK to delete everything (full purge)\\n- Click Cancel to keep your configuration settings");
      showView('view_progress');
      appendLog('[*] Uninstalling Cmdiqo Agent Service...');
      if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.uninstall === 'function') {
        window.pywebview.api.uninstall(purge);
      }
    }

    let renderedServiceStatus = null;

    function updateServiceControls(status) {
      const isRunning = status === 'running';
      const isStopped = status === 'stopped';
      document.getElementById('btn_start_service').disabled = !isStopped;
      document.getElementById('btn_restart_service').disabled = !isRunning;
      document.getElementById('btn_stop_service').disabled = !isRunning;
    }

    function renderServiceStatus(status) {
      if (status === renderedServiceStatus) {
        return;
      }
      const statusNode = document.getElementById('service_status');
      const label = status === 'not_installed' ? 'not installed' : status;
      statusNode.innerHTML = '<span class="service-dot ' + status.replace('_', '-') + '"></span><span>' + label + '</span>';
      updateServiceControls(status);
      renderedServiceStatus = status;
    }

    function manageService(action) {
      if (!(window.pywebview && window.pywebview.api && typeof window.pywebview.api.manage_service === 'function')) {
        return;
      }

      ['btn_start_service', 'btn_restart_service', 'btn_stop_service'].forEach(id => {
        document.getElementById(id).disabled = true;
      });
      window.pywebview.api.manage_service(action).then(result => {
        if (!result.ok) {
          alert(result.message);
        }
        syncWithBackend(true);
      }).catch(error => {
        alert('Service operation failed: ' + error);
        syncWithBackend(true);
      });
    }

    function initUI(data) {
      if (!data) return;
      if (data.bundled_version) {
        document.getElementById('badge_version').textContent = 'v' + data.bundled_version;
      }
      if (data.config) {
        if (data.config.hub_url) document.getElementById('hub_url').value = data.config.hub_url;
        if (data.config.token) document.getElementById('token').value = data.config.token;
        if (data.config.name) document.getElementById('name').value = data.config.name;
        document.getElementById('notify').checked = !!data.config.agent_connection_notify;
      }
      if (data.paths && data.paths.install_dir) {
        document.getElementById('install_dir').value = data.paths.install_dir;
      }

      const installed = data.installed_version;
      const bundled = data.bundled_version;
      const badge = document.getElementById('installed_badge');
      const status = data.service_status || 'not_installed';
      renderServiceStatus(status);

      const hasConfiguration = Boolean(data.config && data.config.token && data.config.token.trim());
      if (!hasConfiguration) {
        badge.textContent = '';
        badge.classList.add('hidden');
        badge.style.display = 'none';
        showView('view_form');
      } else if (!installed) {
        // If no version is found, do NOT show (Detecting) or anything - just hide the element completely
        badge.textContent = '';
        badge.classList.add('hidden');
        badge.style.display = 'none';
        showView('view_form');
      } else {
        badge.textContent = 'Installed: v' + installed;
        badge.classList.remove('hidden');
        badge.style.display = 'inline-block';
        if (data.cmp === 0) {
          document.getElementById('same_message').textContent = 'You have already installed version ' + bundled;
          showView('view_same');
        } else if (data.cmp < 0) {
          document.getElementById('upgrade_message').textContent =
            "You're going to upgrade from " + installed + " to " + bundled + ". Do you want to continue?";
          showView('view_upgrade');
        } else {
          document.getElementById('newer_message').textContent = 'You have already installed newer ' + installed;
          showView('view_newer');
        }
      }
      if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.mark_ui_ready === 'function') {
        window.pywebview.api.mark_ui_ready();
      }
    }

    function testConnection() {
      const url = document.getElementById('hub_url').value;
      const statusDiv = document.getElementById('test_status');
      statusDiv.style.display = 'block';
      statusDiv.style.color = '#38bdf8';
      statusDiv.textContent = 'Testing connection to Hub...';

      if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.test_hub === 'function') {
        window.pywebview.api.test_hub(url).then(res => {
          if (res.ok) {
            statusDiv.style.color = '#34d399';
            statusDiv.textContent = res.message;
          } else {
            statusDiv.style.color = '#f87171';
            statusDiv.textContent = res.message;
          }
        }).catch(err => {
          statusDiv.style.color = '#f87171';
          statusDiv.textContent = 'Connection test failed: ' + err;
        });
      } else {
        statusDiv.style.color = '#f87171';
        statusDiv.textContent = 'API bridge not ready';
      }
    }

    function submitInstall() {
      const config = {
        hub_url: document.getElementById('hub_url').value.trim(),
        token: document.getElementById('token').value.trim(),
        name: document.getElementById('name').value.trim(),
        agent_connection_notify: document.getElementById('notify').checked
      };

      if (!config.token) {
        alert('Please enter AGENT_TOKEN before continuing.');
        document.getElementById('token').focus();
        return;
      }

      showView('view_progress');
      document.getElementById('log_terminal').textContent = 'Starting installation...\\n';
      if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.install === 'function') {
        window.pywebview.api.install(config);
      }
    }

    function appendLog(line) {
      const term = document.getElementById('log_terminal');
      term.textContent += line + '\\n';
      term.scrollTop = term.scrollHeight;
    }

    function setFinished(success, message) {
      document.getElementById('progress_title').textContent = message;
      if (success) {
        setTimeout(() => syncWithBackend(true), 250);
        return;
      }
      document.getElementById('footer_bar').classList.remove('hidden');
      document.getElementById('btn_cancel').classList.add('hidden');
      const btn = document.getElementById('btn_install');
      btn.textContent = 'Close';
      btn.onclick = () => closeWindow();
    }

    // 1. Synchronous initialization using injected initial state
    if (INITIAL_STATE) {
      initUI(INITIAL_STATE);
    }

    // 2. Also ensure DOMContentLoaded runs initUI if script executed early
    document.addEventListener('DOMContentLoaded', function () {
      if (INITIAL_STATE) {
        initUI(INITIAL_STATE);
      }
    });

    // Poll because Edge/WebView2 may initialize the API before this page receives
    // pywebviewready. Relying only on that one-shot event can leave the loader visible.
    let initialStateLoaded = false;
    let stateRequestInFlight = false;
    let serviceStatusPollInFlight = false;
    const startupStartedAt = Date.now();
    let startupStatusTimer = null;
    let startupStatusIndex = 0;
    const startupStatusMessages = [
      'Reading the installed Cmdiqo agent version.',
      'Loading saved connection settings.',
      'Checking the background service status.',
      'Preparing installer controls.'
    ];

    function setLoadingDetail(message) {
      document.getElementById('loading_detail').textContent = message;
    }

    function startStartupStatusUpdates() {
      if (startupStatusTimer !== null) {
        return;
      }
      setLoadingDetail(startupStatusMessages[startupStatusIndex]);
      startupStatusTimer = setInterval(() => {
        startupStatusIndex = Math.min(startupStatusIndex + 1, startupStatusMessages.length - 1);
        setLoadingDetail(startupStatusMessages[startupStatusIndex]);
      }, 750);
    }

    function stopStartupStatusUpdates() {
      if (startupStatusTimer !== null) {
        clearInterval(startupStatusTimer);
        startupStatusTimer = null;
      }
    }

    function syncWithBackend(force) {
      if (stateRequestInFlight || (!force && initialStateLoaded)) {
        return;
      }
      if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_state === 'function') {
        stateRequestInFlight = true;
        startStartupStatusUpdates();
        window.pywebview.api.get_state().then(data => {
          stateRequestInFlight = false;
          initialStateLoaded = true;
          stopStartupStatusUpdates();
          initUI(data);
        }).catch(error => {
          document.getElementById('loading_message').textContent =
            'Unable to load installer status. Retrying...';
          setLoadingDetail('The installer will retry automatically.');
          console.error(error);
          stateRequestInFlight = false;
          stopStartupStatusUpdates();
          setTimeout(syncWithBackend, 1000);
        });
        return;
      }

      if (Date.now() - startupStartedAt > 2000) {
        document.getElementById('loading_message').textContent =
          'Starting the Windows browser bridge...';
        setLoadingDetail('Waiting for the embedded browser to make the installer API available.');
      }

      // Non-destructive give-up: after ~25s without the bridge, stop polling and show a
      // clear in-page error instead of spinning forever. We never destroy the window or
      // restart the WebView backend here, which previously left disposed native controls
      // behind and crashed the installer.
      if (Date.now() - startupStartedAt > 25000) {
        stopStartupStatusUpdates();
        document.getElementById('loading_message').textContent =
          'The installer could not start its embedded browser.';
        setLoadingDetail(
          'Please ensure the Microsoft Edge WebView2 Runtime is installed, then close ' +
          'this window and run the installer again.'
        );
        const spinner = document.getElementById('loading_spinner');
        if (spinner) {
          spinner.classList.add('hidden');
        }
        return;
      }
      setTimeout(syncWithBackend, 100);
    }

    window.addEventListener('pywebviewready', () => syncWithBackend(false));
    syncWithBackend(false);

    function pollServiceStatus() {
      const installedView = document.getElementById('view_same');
      if (
        initialStateLoaded &&
        !serviceStatusPollInFlight &&
        !installedView.classList.contains('hidden') &&
        window.pywebview &&
        window.pywebview.api &&
        typeof window.pywebview.api.get_current_service_status === 'function'
      ) {
        serviceStatusPollInFlight = true;
        window.pywebview.api.get_current_service_status().then(status => {
          renderServiceStatus(status || 'not_installed');
        }).catch(error => {
          console.error('Could not refresh service status:', error);
        }).finally(() => {
          serviceStatusPollInFlight = false;
        });
      }
      setTimeout(pollServiceStatus, 1000);
    }

    setTimeout(pollServiceStatus, 1000);

  </script>
</body>
</html>
"""


class WebViewApi:
    """Python API bridge exposed to the webview JavaScript."""

    def __init__(self, window: Any):
        # Keep the native window reference private (underscore-prefixed) so pywebview's
        # JS bridge does not try to introspect/serialize it. A public reference would make
        # pywebview recursively walk window.native.browser.webview... on the WebView2
        # backend, producing "maximum recursion depth exceeded" and cross-thread COM
        # errors that flood the log and destabilize startup.
        self._window = window
        self.paths = get_platform_paths()
        self.bundled_ver = get_bundled_version()
        self.installed_ver: Optional[str] = None
        self.cmp: Optional[int] = None
        self.config: dict = {}
        self.service_status = "not_installed"
        self.ui_ready = threading.Event()

    def get_state(self) -> dict:
        started_at = time.monotonic()
        log.info("[startup] Reading installed Cmdiqo agent version from %s", self.paths.install_dir)
        self.installed_ver = get_installed_version(self.paths)
        self.cmp = (
            compare_versions(self.installed_ver, self.bundled_ver)
            if self.installed_ver is not None
            else None
        )
        log.info("[startup] Loading saved agent configuration from %s", self.paths.config_file)
        self.config = get_existing_config(self.paths)
        log.info("[startup] Checking background service %s", self.paths.service_name)
        self.service_status = get_service_status(self.paths)
        log.info(
            "[startup] Installer state loaded in %.2fs: version=%s, service=%s",
            time.monotonic() - started_at,
            self.installed_ver or "not installed",
            self.service_status,
        )
        return {
            "bundled_version": self.bundled_ver,
            "installed_version": self.installed_ver,
            "cmp": self.cmp,
            "service_status": self.service_status,
            "config": self.config,
            "paths": {
                "install_dir": str(self.paths.install_dir),
                "config_dir": str(self.paths.config_dir),
                "service_name": self.paths.service_name,
            },
        }

    def test_hub(self, url: str) -> dict:
        ok, msg = check_hub_connection(url)
        return {"ok": ok, "message": msg}

    def get_current_service_status(self) -> str:
        """Return only the live service state for the installed-agent UI poll."""
        return get_service_status(self.paths)

    def mark_ui_ready(self) -> None:
        """Acknowledge that the WebView rendered the initial installer screen."""
        self.ui_ready.set()

    def close_window(self) -> None:
        if self._window:
            self._window.destroy()

    def confirm_upgrade(self) -> None:
        """User clicked YES on upgrade."""
        self._window.evaluate_js("showView('view_progress')")
        self._window.evaluate_js("appendLog('[*] Upgrading Cmdiqo Agent to version " + self.bundled_ver + "...')")
        threading.Thread(target=self._run_upgrade, daemon=True).start()

    def reinstall(self) -> None:
        """User clicked Reinstall on already-same-version."""
        self._window.evaluate_js("showView('view_progress')")
        self._window.evaluate_js("appendLog('[*] Reinstalling Cmdiqo Agent version " + self.bundled_ver + "...')")
        threading.Thread(target=self._run_reinstall, daemon=True).start()

    def manage_service(self, action: str) -> dict[str, str | bool]:
        """Perform a service action from the installer and return its refreshed status."""
        actions = {
            "start": start_service,
            "restart": restart_service,
            "stop": stop_service,
        }
        operation = actions.get(action)
        if operation is None:
            return {"ok": False, "message": f"Unsupported service action: {action}"}

        ok = operation(self.paths)
        status = get_service_status(self.paths)
        if ok:
            return {
                "ok": True,
                "message": f"Service {action} completed.",
                "service_status": status,
            }
        return {
            "ok": False,
            "message": f"Could not {action} the service. Check installer.log and service.log.",
            "service_status": status,
        }

    def uninstall(self, purge: bool = False) -> None:
        """User clicked Uninstall."""
        self._window.evaluate_js("showView('view_progress')")
        self._window.evaluate_js("appendLog('[*] Uninstalling Cmdiqo Agent Service...')")
        threading.Thread(target=self._run_uninstall, args=(purge,), daemon=True).start()

    def install(self, config: dict) -> None:
        """User submitted fresh install form."""
        threading.Thread(target=self._run_install, args=(config,), daemon=True).start()

    def _log_to_ui(self, msg: str) -> None:
        safe_msg = json.dumps(msg)
        self._window.evaluate_js(f"appendLog({safe_msg})")

    def _run_install(self, config: dict) -> None:
        ok = install_or_upgrade(config, progress_callback=self._log_to_ui)
        title = "Installation completed successfully!" if ok else "Installation failed!"
        self._window.evaluate_js(f"setFinished({str(ok).lower()}, {json.dumps(title)})")

    def _run_upgrade(self) -> None:
        ok = install_or_upgrade(self.config, progress_callback=self._log_to_ui)
        title = "Upgrade completed successfully!" if ok else "Upgrade failed!"
        self._window.evaluate_js(f"setFinished({str(ok).lower()}, {json.dumps(title)})")

    def _run_reinstall(self) -> None:
        ok = install_or_upgrade(self.config, progress_callback=self._log_to_ui)
        title = "Reinstallation completed successfully!" if ok else "Reinstallation failed!"
        self._window.evaluate_js(f"setFinished({str(ok).lower()}, {json.dumps(title)})")

    def _run_uninstall(self, purge: bool) -> None:
        ok = perform_uninstall(purge_config=purge, progress_callback=self._log_to_ui)
        title = "Service uninstalled successfully!" if ok else "Uninstall encountered errors."
        self._window.evaluate_js(f"setFinished({str(ok).lower()}, {json.dumps(title)})")


def get_brand_icon_data_uri() -> str:
    """Return the bundled Cmdiqo icon as a browser-safe data URI."""
    candidates = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "cmdiqo.ico")
    candidates.append(Path(__file__).resolve().parent.parent / "development" / "cmdiqo.ico")

    for icon_path in candidates:
        try:
            return f"data:image/x-icon;base64,{base64.b64encode(icon_path.read_bytes()).decode('ascii')}"
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.warning("Could not load Cmdiqo brand icon %s: %s", icon_path, exc)
            break
    return ""


def linux_webview_install_hint() -> str:
    """Return the WebKit2GTK installation command for the current Linux distribution."""
    try:
        os_release = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        return "Install your distribution's WebKit2GTK 4.0 or 4.1 runtime package."

    fields = {
        key: value.strip().strip('"')
        for line in os_release.splitlines()
        if "=" in line
        for key, value in [line.split("=", 1)]
    }
    distro_ids = {fields.get("ID", "").lower(), *fields.get("ID_LIKE", "").lower().split()}
    version = fields.get("VERSION_ID", "")

    if distro_ids & {"opensuse", "suse", "sles"}:
        return "sudo zypper install typelib-1_0-WebKit2-4_1"
    if distro_ids & {"ubuntu", "debian"}:
        package = "gir1.2-webkit2-4.0" if version.startswith(("20.04", "22.04")) else "gir1.2-webkit2-4.1"
        return f"sudo apt install {package}"
    if distro_ids & {"rhel", "centos", "rocky", "almalinux"}:
        return "sudo dnf install webkit2gtk3"
    if "fedora" in distro_ids:
        version_major = version.split(".", 1)[0]
        package = "webkit2gtk3" if version_major.isdigit() and int(version_major) < 40 else "webkit2gtk4.1"
        return f"sudo dnf install {package}"
    return "Install your distribution's WebKit2GTK 4.0 or 4.1 runtime package."


def linux_gui_environment_guidance() -> str:
    """Return guidance for launching a Linux GUI installer with desktop variables."""
    return (
        "Run the installer as your desktop user, without sudo: "
        "./cmdiqo-agent-linux-x64-setup. It will request root access through pkexec. "
        "If you must diagnose a direct sudo launch, preserve the desktop variables with: "
        "sudo env DISPLAY=\"$DISPLAY\" XAUTHORITY=\"$XAUTHORITY\" "
        "WAYLAND_DISPLAY=\"$WAYLAND_DISPLAY\" XDG_RUNTIME_DIR=\"$XDG_RUNTIME_DIR\" "
        "DBUS_SESSION_BUS_ADDRESS=\"$DBUS_SESSION_BUS_ADDRESS\" "
        "./cmdiqo-agent-linux-x64-setup"
    )


def configure_linux_typelib_search_paths(repository: Any) -> list[str]:
    """Add standard system GObject typelib paths to a bundled PyGObject runtime."""
    system_paths: list[str] = []
    for typelib_dir in (
        Path("/usr/lib64/girepository-1.0"),
        Path("/usr/lib/girepository-1.0"),
    ):
        if typelib_dir.is_dir():
            path = str(typelib_dir)
            repository.prepend_search_path(path)
            system_paths.append(path)
    get_search_path = getattr(repository, "get_search_path", None)
    if callable(get_search_path):
        return [str(path) for path in get_search_path()]
    return system_paths


def linux_webview_runtime_available() -> bool:
    """Return whether the target Linux system provides a WebKit2GTK typelib."""
    search_paths: list[str] = []
    try:
        import gi

        try:
            gi.require_version("GIRepository", "3.0")
        except ValueError:
            gi.require_version("GIRepository", "2.0")
        from gi.repository import GIRepository
        repository = GIRepository.Repository.get_default()
        search_paths = configure_linux_typelib_search_paths(repository)
        try:
            gi.require_version("WebKit2", "4.1")
        except ValueError:
            gi.require_version("WebKit2", "4.0")
        from gi.repository import WebKit2  # noqa: F401
    except (AttributeError, ImportError, ValueError) as exc:
        log.warning(
            "WebKit2GTK is not available; skipping the WebView UI. Error: %s. "
            "GI typelib search paths: %s. Install it with: %s",
            exc,
            search_paths,
            linux_webview_install_hint(),
        )
        return False
    return True


def run_webview_gui() -> bool:
    """Launch modern HTML-based desktop GUI using pywebview."""
    if not IS_WINDOWS and not linux_webview_runtime_available():
        return False

    try:
        if IS_WINDOWS:
            os.environ.setdefault("PYWEBVIEW_GUI", "edgechromium")
        import webview
    except ImportError:
        # pywebview is intentionally bundled only in the Windows installer. On Linux the
        # frozen installer ships the Tkinter GUI instead, so a missing module here is an
        # expected condition, not an error worth a stack trace.
        log.info("pywebview is not available; skipping the WebView GUI backend.")
        return False

    try:
        api = WebViewApi(None)
        html_rendered = (
            HTML_TEMPLATE.replace("__INITIAL_STATE_JSON__", "null")
            .replace("__BRAND_ICON_DATA_URI__", get_brand_icon_data_uri())
        )
        win = webview.create_window(
            title="Cmdiqo Agent Setup",
            html=html_rendered,
            js_api=api,
            width=620,
            height=660,
            resizable=False,
        )
        api._window = win
        # On Windows, also pass gui='edgechromium' directly to start() alongside the env var.
        # Belt-and-suspenders: some pywebview builds pick up the backend selection late.
        start_kwargs: dict = {}
        if IS_WINDOWS:
            start_kwargs["gui"] = "edgechromium"
        log.info("Starting webview event loop (kwargs: %s)", start_kwargs)
        webview.start(**start_kwargs)
        log.info("Webview event loop exited")
        return True
    except Exception as exc:
        import traceback
        log.error("Could not launch webview GUI: %s\n%s", exc, traceback.format_exc())
        return False


def has_interactive_stdin() -> bool:
    """Return whether the process can safely show the terminal installer."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, OSError):
        return False


def show_gui_startup_failure() -> None:
    """Report a GUI startup failure without requiring a console window."""
    if IS_WINDOWS:
        log_hint = os.path.join(os.environ.get("TEMP", ""), "cmdiqo", "installer.log")
        message = (
            "Cmdiqo Agent Setup could not start its embedded browser window.\n\n"
            "Please ensure that the Microsoft Edge WebView2 Runtime is installed, then "
            "run the installer again.\n\n"
            f"Details were written to:\n{log_hint}"
        )
    else:
        install_hint = linux_webview_install_hint()
        message = (
            "Cmdiqo Agent Setup could not open a graphical window.\n\n"
            "Please ensure a desktop display is available and the Tkinter (python3-tk) "
            f"package is installed. For the modern WebView UI, install:\n{install_hint}\n\n"
            "Re-run the installer without sudo so it can request desktop authentication.\n\n"
            "Details were written to the installer log."
        )
    log.error("Unable to start a graphical installer. %s", message.replace("\n", " "))

    if IS_WINDOWS:
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                message,
                "Cmdiqo Agent Setup",
                0x10,  # MB_ICONERROR
            )
            return
        except (AttributeError, OSError) as exc:
            log.error("Could not display the Windows installer error dialog: %s", exc)

    if HAS_TKINTER:
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Cmdiqo Agent Setup", message)
            root.destroy()
        except tk.TclError as exc:
            log.error("Could not display the Tkinter installer error dialog: %s", exc)


# ==============================================================================
# TKINTER GUI IMPLEMENTATION (STANDARDIZED PYTHON DESKTOP FALLBACK)
# ==============================================================================
def run_tkinter_gui() -> bool:
    """Launch Tkinter GUI if available."""
    if not HAS_TKINTER:
        return False

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        log.warning("Tkinter could not open display: %s", exc)
        if not IS_WINDOWS:
            log.error("%s", linux_gui_environment_guidance())
        return False

    paths = get_platform_paths()
    bundled_ver = get_bundled_version()
    installed_ver = get_installed_version(paths)
    cmp = compare_versions(installed_ver, bundled_ver) if installed_ver is not None else None
    config = get_existing_config(paths)
    service_status = get_service_status(paths)

    root.title("Cmdiqo Agent Setup")
    root.geometry("540x520")
    root.resizable(False, False)

    # Style
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass

    # Header frame
    header = ttk.Frame(root, padding=15)
    header.pack(fill="x")
    ttk.Label(
        header,
        text=f"Cmdiqo Agent Setup (v{bundled_ver})",
        font=("Segoe UI" if IS_WINDOWS else "Helvetica", 14, "bold"),
    ).pack(anchor="w")

    content = ttk.Frame(root, padding=20)
    content.pack(fill="both", expand=True)

    def close():
        root.destroy()

    # Case 1: Same version installed
    if installed_ver is not None and cmp == 0:
        ttk.Label(
            content,
            text=f"You have already installed version {bundled_ver}",
            font=("Segoe UI" if IS_WINDOWS else "Helvetica", 12, "bold"),
            foreground="#16a34a",
        ).pack(pady=15)
        status_colors = {
            "running": "#22c55e",
            "stopped": "#3b82f6",
            "not_installed": "#ef4444",
        }
        status_labels = {
            "running": "running",
            "stopped": "stopped",
            "not_installed": "not installed",
        }
        status_row = tk.Frame(content)
        status_row.pack(pady=(0, 5))
        status_icon = tk.Canvas(
            status_row,
            width=16,
            height=16,
            highlightthickness=0,
            background=root.cget("background"),
        )
        if service_status == "running":
            status_icon.create_oval(1, 1, 15, 15, fill="#bbf7d0", outline="")
            status_icon.create_oval(4, 4, 12, 12, fill=status_colors[service_status], outline="")
        else:
            status_icon.create_oval(4, 4, 12, 12, fill=status_colors[service_status], outline="")
        status_icon.pack(side="left")
        status_label = tk.Label(
            status_row,
            text=status_labels[service_status],
            foreground=status_colors[service_status],
            font=("Segoe UI" if IS_WINDOWS else "Helvetica", 11, "bold"),
            background=root.cget("background"),
        )
        status_label.pack(side="left", padx=(5, 0))
        ttk.Label(
            content,
            text="The service is already up to date. What would you like to do?",
            wraplength=480,
        ).pack(pady=5)

        btn_box = ttk.Frame(content)
        btn_box.pack(pady=25)

        def do_reinstall():
            if install_or_upgrade(config):
                refresh_service_controls()
                messagebox.showinfo("Success", f"Cmdiqo Agent v{bundled_ver} reinstalled successfully.")
            else:
                messagebox.showerror("Repair failed", "Cmdiqo Agent could not be reinstalled. Check installer.log.")

        def do_uninstall():
            if not messagebox.askyesno("Uninstall", "Are you sure you want to uninstall Cmdiqo Agent service?"):
                return
            purge = messagebox.askyesno(
                "Remove Configuration?",
                "Do you also want to delete all configuration, secrets, and log files?\n\n- Click [Yes] to delete everything (full purge)\n- Click [No] to keep configuration",
            )
            perform_uninstall(purge_config=purge)
            messagebox.showinfo("Uninstalled", "Cmdiqo Agent service and files have been removed.")
            close()

        ttk.Button(btn_box, text="Reinstall / Repair", command=do_reinstall).pack(side="left", padx=5)
        ttk.Button(btn_box, text="Uninstall", command=do_uninstall).pack(side="left", padx=5)
        ttk.Button(btn_box, text="Close", command=close).pack(side="left", padx=5)

        service_btn_box = ttk.Frame(content)
        service_btn_box.pack(pady=(0, 12))

        def refresh_service_controls(force: bool = False):
            nonlocal service_status
            current_status = get_service_status(paths)
            if not force and current_status == service_status:
                return
            service_status = current_status
            status_icon.delete("all")
            if service_status == "running":
                status_icon.create_oval(1, 1, 15, 15, fill="#bbf7d0", outline="")
            status_icon.create_oval(
                4, 4, 12, 12, fill=status_colors[service_status], outline=""
            )
            status_label.configure(
                text=status_labels[service_status],
                foreground=status_colors[service_status],
            )
            start_button.configure(state="normal" if service_status == "stopped" else "disabled")
            restart_button.configure(state="normal" if service_status == "running" else "disabled")
            stop_button.configure(state="normal" if service_status == "running" else "disabled")

        def manage_tk_service(action: str) -> None:
            operations = {
                "start": start_service,
                "restart": restart_service,
                "stop": stop_service,
            }
            if not operations[action](paths):
                messagebox.showerror(
                    "Service operation failed",
                    f"Could not {action} the Cmdiqo service. Check installer.log and service.log.",
                )
            refresh_service_controls(force=True)

            def poll_tk_service_status():
                refresh_service_controls()
                root.after(1000, poll_tk_service_status)

            root.after(1000, poll_tk_service_status)

        start_button = ttk.Button(
            service_btn_box,
            text="Start Service",
            command=lambda: manage_tk_service("start"),
        )
        restart_button = ttk.Button(
            service_btn_box,
            text="Restart Service",
            command=lambda: manage_tk_service("restart"),
        )
        stop_button = ttk.Button(
            service_btn_box,
            text="Stop Service",
            command=lambda: manage_tk_service("stop"),
        )
        start_button.pack(side="left", padx=5)
        restart_button.pack(side="left", padx=5)
        stop_button.pack(side="left", padx=5)
        refresh_service_controls()

        root.mainloop()
        return True

    # Case 2: Older version installed (Upgrade)
    elif installed_ver is not None and cmp < 0:
        ttk.Label(
            content,
            text=f"You're going to upgrade from {installed_ver} to {bundled_ver}.\nDo you want to continue?",
            font=("Segoe UI" if IS_WINDOWS else "Helvetica", 11, "bold"),
            foreground="#2563eb",
            justify="center",
        ).pack(pady=25)

        btn_box = ttk.Frame(content)
        btn_box.pack(pady=20)

        def do_upgrade():
            install_or_upgrade(config)
            messagebox.showinfo("Upgrade Complete", f"Successfully upgraded to version {bundled_ver}!")
            close()

        def do_uninstall_upgrade():
            if not messagebox.askyesno("Uninstall", "Are you sure you want to uninstall Cmdiqo Agent service?"):
                return
            purge = messagebox.askyesno(
                "Remove Configuration?",
                "Do you also want to delete all configuration, secrets, and log files?\n\n- Click [Yes] to delete everything (full purge)\n- Click [No] to keep configuration",
            )
            perform_uninstall(purge_config=purge)
            messagebox.showinfo("Uninstalled", "Cmdiqo Agent service and files have been removed.")
            close()

        ttk.Button(btn_box, text="YES (Upgrade)", command=do_upgrade, width=14).pack(side="left", padx=5)
        ttk.Button(btn_box, text="Uninstall", command=do_uninstall_upgrade, width=10).pack(side="left", padx=5)
        ttk.Button(btn_box, text="NO (Cancel)", command=close, width=12).pack(side="left", padx=5)

        root.mainloop()
        return True

    # Case 3: Newer version installed
    elif installed_ver is not None and cmp > 0:
        ttk.Label(
            content,
            text=f"You have already installed newer {installed_ver}",
            font=("Segoe UI" if IS_WINDOWS else "Helvetica", 12, "bold"),
            foreground="#d97706",
        ).pack(pady=25)
        ttk.Label(content, text="No upgrade is needed.").pack(pady=5)

        btn_box = ttk.Frame(content)
        btn_box.pack(pady=20)

        def do_uninstall_newer():
            if not messagebox.askyesno("Uninstall", "Are you sure you want to uninstall Cmdiqo Agent service?"):
                return
            purge = messagebox.askyesno(
                "Remove Configuration?",
                "Do you also want to delete all configuration, secrets, and log files?\n\n- Click [Yes] to delete everything (full purge)\n- Click [No] to keep configuration",
            )
            perform_uninstall(purge_config=purge)
            messagebox.showinfo("Uninstalled", "Cmdiqo Agent service and files have been removed.")
            close()

        ttk.Button(btn_box, text="Uninstall", command=do_uninstall_newer, width=12).pack(side="left", padx=5)
        ttk.Button(btn_box, text="CLOSE", command=close, width=12).pack(side="left", padx=5)
        root.mainloop()
        return True

    # Case 4: Fresh Installation Form
    else:
        ttk.Label(content, text="Hub WebSocket URL:").pack(anchor="w", pady=(0, 2))
        hub_entry = ttk.Entry(content, width=50)
        hub_entry.insert(0, config.get("hub_url", "ws://192.168.1.10:8765"))
        hub_entry.pack(anchor="w", pady=(0, 10))

        ttk.Label(content, text="Agent Token (Shared Secret):").pack(anchor="w", pady=(0, 2))
        token_entry = ttk.Entry(content, width=50, show="*")
        token_entry.insert(0, config.get("token", ""))
        token_entry.pack(anchor="w", pady=(0, 10))

        ttk.Label(content, text="Agent Name:").pack(anchor="w", pady=(0, 2))
        name_entry = ttk.Entry(content, width=50)
        name_entry.insert(0, config.get("name", socket.gethostname()))
        name_entry.pack(anchor="w", pady=(0, 10))

        notify_var = tk.BooleanVar(value=bool(config.get("agent_connection_notify", False)))
        ttk.Checkbutton(content, text="Enable Slack notification on connect/disconnect", variable=notify_var).pack(anchor="w", pady=(0, 15))

        def do_install():
            token_val = token_entry.get().strip()
            if not token_val:
                messagebox.showerror("Error", "Agent Token cannot be empty!")
                return
            new_cfg = {
                "hub_url": hub_entry.get().strip(),
                "token": token_val,
                "name": name_entry.get().strip(),
                "agent_connection_notify": notify_var.get(),
            }
            ok = install_or_upgrade(new_cfg)
            if ok:
                messagebox.showinfo("Success", "Cmdiqo Agent installed and service started!")
                close()
            else:
                messagebox.showerror("Error", "Installation encountered errors. Please check logs.")

        btn_box = ttk.Frame(content)
        btn_box.pack(side="bottom", fill="x", pady=10)
        ttk.Button(btn_box, text="Cancel", command=close).pack(side="right", padx=5)
        ttk.Button(btn_box, text="Install & Start", command=do_install).pack(side="right", padx=5)

        root.mainloop()
        return True


# ==============================================================================
# CLI / TERMINAL INTERACTIVE FALLBACK (HEADLESS SERVERS & AUTOMATION)
# ==============================================================================
def run_cli_installer() -> None:
    """Run interactive text terminal installer."""
    paths = get_platform_paths()
    bundled_ver = get_bundled_version()
    installed_ver = get_installed_version(paths)
    cmp = compare_versions(installed_ver, bundled_ver) if installed_ver is not None else None
    config = get_existing_config(paths)

    print("\n" + "=" * 60)
    print(f"  Cmdiqo Agent Setup (Version {bundled_ver})")
    print("=" * 60)

    if installed_ver is not None:
        def ask_cli_purge() -> bool:
            ans = input("Do you also want to delete all configuration, secrets, and log files? (y/N) [n]: ").strip().lower()
            return ans in ("y", "yes")

        if cmp == 0:
            print(f"\n[i] You have already installed version {bundled_ver}")
            print("\nOptions:")
            print("  1. Reinstall / Repair")
            print("  2. Uninstall service")
            print("  3. Exit")
            choice = input("\nSelect an option [1-3, default: 3]: ").strip()
            if choice == "1":
                install_or_upgrade(config)
            elif choice == "2":
                perform_uninstall(purge_config=ask_cli_purge())
            return

        elif cmp < 0:
            print(f"\n[!] You're going to upgrade from {installed_ver} to {bundled_ver}. Do you want to continue?")
            print("\nOptions:")
            print("  1. Yes (Upgrade)")
            print("  2. No (Cancel)")
            print("  3. Uninstall")
            ans = input("\nEnter choice [1-3, default: 1]: ").strip()
            if ans in ("1", "yes", "y", ""):
                install_or_upgrade(config)
            elif ans in ("3", "uninstall", "u"):
                perform_uninstall(purge_config=ask_cli_purge())
            else:
                print("[-] Upgrade aborted by user.")
            return

        else:
            print(f"\n[!] You have already installed newer {installed_ver}")
            print("\nOptions:")
            print("  1. Close")
            print("  2. Uninstall")
            ans = input("\nEnter choice [1-2, default: 1]: ").strip()
            if ans in ("2", "uninstall", "u"):
                perform_uninstall(purge_config=ask_cli_purge())
            return

    # Fresh install prompts
    print("\n[*] Fresh installation detected. Please configure agent settings:")
    hub = input(f"Hub WebSocket URL [default: {config['hub_url']}]: ").strip() or config["hub_url"]
    tok = input(f"Agent Token (shared secret): ").strip()
    while not tok:
        print("[-] Agent Token cannot be empty!")
        tok = input("Agent Token (shared secret): ").strip()
    name = input(f"Agent Name [default: {config['name']}]: ").strip() or config["name"]
    notify = input("Enable Slack notification on connect? (yes/no) [no]: ").strip().lower() in ("yes", "y", "true", "1")

    new_cfg = {
        "hub_url": hub,
        "token": tok,
        "name": name,
        "agent_connection_notify": notify,
    }
    install_or_upgrade(new_cfg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cmdiqo Agent Setup Installer")
    parser.add_argument("--cli", action="store_true", help="Force interactive CLI mode instead of GUI")
    parser.add_argument("--uninstall", action="store_true", help="Directly uninstall agent service")
    parser.add_argument("--quiet", "-q", action="store_true", help="Silent/quiet execution (no GUI prompts)")
    parser.add_argument("--purge", action="store_true", help="Purge configuration files when uninstalling")
    args = parser.parse_args()

    # Handle direct command-line uninstallation
    if args.uninstall:
        # Request admin if needed
        if not is_admin():
            if request_elevation():
                sys.exit(0)
            if not args.quiet and IS_WINDOWS:
                try:
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        "Administrator privileges are required to uninstall Cmdiqo Remote Agent.",
                        "Cmdiqo Setup",
                        0x10,
                    )
                except Exception:
                    pass
            print("[ERROR] Administrator/root privileges are required to uninstall.")
            sys.exit(1)

        # Interactive confirmation on Windows if not quiet
        purge = args.purge
        if IS_WINDOWS and not args.quiet:
            try:
                ret = ctypes.windll.user32.MessageBoxW(
                    0,
                    "Are you sure you want to uninstall Cmdiqo Remote Agent and remove its background service?",
                    "Cmdiqo Remote Agent Uninstall",
                    0x04 | 0x20,  # MB_YESNO | MB_ICONQUESTION
                )
                if ret != 6:  # IDYES
                    sys.exit(0)

                if not args.purge:
                    ret_purge = ctypes.windll.user32.MessageBoxW(
                        0,
                        "Do you also want to remove all configuration settings, secrets, and log files?\n\n- Click [Yes] to delete everything (full purge)\n- Click [No] to keep configuration settings",
                        "Cmdiqo Remote Agent Uninstall",
                        0x04 | 0x20,  # MB_YESNO | MB_ICONQUESTION
                    )
                    if ret_purge == 6:  # IDYES
                        purge = True
            except Exception:
                pass

        ok = perform_uninstall(purge_config=purge)

        if IS_WINDOWS and not args.quiet:
            try:
                if ok:
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        "Cmdiqo Remote Agent service and components have been successfully uninstalled.",
                        "Cmdiqo Remote Agent Uninstall",
                        0x40,  # MB_ICONINFORMATION
                    )
                else:
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        "Uninstallation completed with warnings. Please check system event logs.",
                        "Cmdiqo Remote Agent Uninstall",
                        0x30,  # MB_ICONWARNING
                    )
            except Exception:
                pass

        sys.exit(0 if ok else 1)

    # Request Administrator / root privileges for install/upgrade
    if not is_admin():
        log.info("Administrator/root privileges are required before starting the installer UI.")
        if not args.cli and not args.quiet:
            if request_elevation():
                sys.exit(0)
            log.error("Administrator/root privileges are required to install or manage services.")
            show_gui_startup_failure()
        else:
            log.error("Please re-run this installer as Administrator (Windows) or root with sudo (Linux).")
            sys.exit(1)
        sys.exit(1)

    # If --cli or --quiet specified, run CLI mode only when a console is available.
    if args.cli or args.quiet:
        if not has_interactive_stdin():
            log.error("CLI installer was requested but stdin is not interactive.")
            if not args.quiet:
                show_gui_startup_failure()
            sys.exit(1)
        run_cli_installer()
        return

    # Both platforms now use the richer WebView UI as the primary interface: Edge WebView2
    # on Windows and GTK/WebKit2 on Linux. Tkinter is the graphical fallback, and the
    # terminal wizard is used only when an interactive console is available (e.g. headless
    # servers over SSH). We never fall through to CLI without an interactive stdin.
    if run_webview_gui():
        return

    if HAS_TKINTER and run_tkinter_gui():
        return

    if has_interactive_stdin():
        run_cli_installer()
    else:
        show_gui_startup_failure()


if __name__ == "__main__":
    main()
