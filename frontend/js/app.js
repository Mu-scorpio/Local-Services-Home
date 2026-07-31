(() => {
  "use strict";

  const POLL_MS = 8000;
  const POLL_MS_HIDDEN = 60000;
  let services = [];
  let pollTimer = null;
  let editingId = null;
  let pollInFlight = false;

  const $ = (sel, root = document) => root.querySelector(sel);
  const grid = $("#service-grid");
  const emptyState = $("#empty-state");
  const modal = $("#modal");
  const form = $("#service-form");
  const storageModal = $("#storage-modal");
  const storageForm = $("#storage-form");
  const THEME_KEY = "lsm-theme";
  let storageSettings = null;

  // ---------- Theme ----------
  function getTheme() {
    return document.documentElement.getAttribute("data-theme") === "light"
      ? "light"
      : "dark";
  }

  function setTheme(theme, { syncServer = true } = {}) {
    const t = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", t);
    try {
      localStorage.setItem(THEME_KEY, t);
    } catch {
      /* ignore */
    }
    // Keep the separate pywebview popup in sync while it is already open.
    window.dispatchEvent(new CustomEvent("lsm-theme-change", { detail: t }));
    window.postMessage({ type: "lsm-theme-change", theme: t }, "*");
    if (syncServer) saveSharedTheme(t);
    syncThemeToggle();
  }

  function syncThemeToggle() {
    const t = getTheme();
    const lightBtn = $("#theme-light");
    const darkBtn = $("#theme-dark");
    if (!lightBtn || !darkBtn) return;
    lightBtn.classList.toggle("active", t === "light");
    darkBtn.classList.toggle("active", t === "dark");
    lightBtn.setAttribute("aria-pressed", t === "light" ? "true" : "false");
    darkBtn.setAttribute("aria-pressed", t === "dark" ? "true" : "false");
  }

  // ---------- API ----------
  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let data = null;
    const text = await res.text();
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    if (!res.ok) {
      const detail = data?.detail;
      const msg = typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
          : res.statusText || "请求失败";
      throw new Error(msg);
    }
    return data;
  }

  async function saveSharedTheme(theme) {
    try {
      await api("/api/settings/theme", {
        method: "PUT",
        body: JSON.stringify({ theme }),
      });
    } catch {
      /* localStorage remains the fallback if the backend is unavailable */
    }
  }

  async function loadSharedTheme() {
    try {
      const result = await api("/api/settings/theme");
      if (result?.theme === "light" || result?.theme === "dark") {
        setTheme(result.theme, { syncServer: false });
      } else {
        await saveSharedTheme(getTheme());
      }
    } catch {
      syncThemeToggle();
    }
  }

  // ---------- Toast ----------
  function toast(message, type = "info") {
    const host = $("#toast-host");
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transition = "opacity 0.25s";
      setTimeout(() => el.remove(), 250);
    }, 3200);
  }

  // ---------- Render ----------
  function defaultIconSvg() {
    return `<span class="default-icon" aria-hidden="true">◈</span>`;
  }

  function renderStats() {
    const total = services.length;
    const running = services.filter((s) => s.running).length;
    $("#stat-total").textContent = String(total);
    $("#stat-running").textContent = String(running);
    $("#stat-stopped").textContent = String(total - running);
  }

  function renderCard(s) {
    const badge = s.running
      ? `<span class="badge badge-running">运行中</span>`
      : `<span class="badge badge-stopped">已停止</span>`;

    const icon = s.has_icon
      ? `<img src="${s.icon_url}?t=${Date.now()}" alt="" loading="lazy" />`
      : defaultIconSvg();

    const notes = s.notes
      ? `<p class="card-notes" title="${escapeAttr(s.notes)}">${escapeHtml(s.notes)}</p>`
      : "";

    return `
      <article class="card" data-id="${s.id}">
        <button type="button" class="card-edit" data-action="edit" title="编辑" aria-label="编辑服务">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M12 20h9"/>
            <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>
          </svg>
        </button>
        <div class="card-top">
          <div class="card-icon" data-action="open" title="打开 WebUI">${icon}</div>
          <div class="card-meta">
            <div class="card-title-row">
              <h3 class="card-title" title="${escapeAttr(s.name)}">${escapeHtml(s.name)}</h3>
              ${badge}
            </div>
            ${notes}
          </div>
        </div>
        <div class="card-info">
          <div class="row-line"><span class="label">端口</span><span class="value">${s.port}</span></div>
          <div class="row-line"><span class="label">进程</span><span class="value">${
            s.running
              ? escapeHtml(
                  [s.process_name, s.pid != null ? `PID ${s.pid}` : null]
                    .filter(Boolean)
                    .join(" · ") || "监听中"
                )
              : "—"
          }</span></div>
          <div class="row-line"><span class="label">WebUI</span><span class="value" title="${escapeAttr(s.webui_url || "")}">${escapeHtml(s.webui_url || "—")}</span></div>
          <div class="row-line"><span class="label">目录</span><span class="value" title="${escapeAttr(s.directory || "")}">${escapeHtml(s.directory || "—")}</span></div>
        </div>
        <div class="card-actions">
          <button type="button" class="btn btn-sm btn-ghost" data-action="open">打开</button>
          <button type="button" class="btn btn-sm btn-success" data-action="start" ${s.running ? "disabled" : ""}>启动</button>
          <button type="button" class="btn btn-sm btn-success" data-action="start-hidden" ${s.running ? "disabled" : ""}>无窗口</button>
          <button type="button" class="btn btn-sm btn-danger" data-action="stop" ${!s.running ? "disabled" : ""}>停止</button>
          <button type="button" class="btn btn-sm btn-ghost" data-action="delete">删除</button>
        </div>
      </article>
    `;
  }

  function render() {
    renderStats();
    if (!services.length) {
      grid.innerHTML = "";
      emptyState.hidden = false;
      return;
    }
    emptyState.hidden = true;
    grid.innerHTML = services.map(renderCard).join("");
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(str) {
    return escapeHtml(str).replace(/'/g, "&#39;");
  }

  // ---------- Data ----------
  async function loadServices() {
    services = await api("/api/services");
    render();
  }

  // ---------- Persistent storage ----------
  function renderStorageSettings(settings) {
    storageSettings = settings;
    const pathEl = $("#storage-path");
    const statusEl = $("#storage-status");
    const resetBtn = $("#btn-storage-reset");
    if (!pathEl || !statusEl || !resetBtn || !settings) return;
    pathEl.textContent = settings.path;
    pathEl.title = settings.path;
    statusEl.textContent = settings.is_custom ? "自定义位置" : "默认独立保存";
    resetBtn.hidden = !settings.is_custom;
  }

  async function loadStorageSettings() {
    const settings = await api("/api/settings/storage");
    renderStorageSettings(settings);
  }

  function openStorageModal() {
    if (!storageSettings) return;
    $("#f-storage-path").value = storageSettings.path;
    $("#storage-default-hint").textContent = `默认目录：${storageSettings.default_path}`;
    storageModal.hidden = false;
    $("#f-storage-path").focus();
    $("#f-storage-path").select();
  }

  function closeStorageModal() {
    storageModal.hidden = true;
    storageForm.reset();
  }

  async function browseStorageDirectory() {
    const btn = $("#btn-storage-browse");
    const prev = btn.textContent;
    btn.disabled = true;
    btn.textContent = "选择中…";
    try {
      const result = await api("/api/browse-folder", {
        method: "POST",
        body: JSON.stringify({ initial_dir: $("#f-storage-path").value.trim() || null }),
      });
      if (result.cancelled || !result.path) return;
      $("#f-storage-path").value = result.path;
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = prev;
    }
  }

  async function saveStorageDirectory(e) {
    e.preventDefault();
    const path = $("#f-storage-path").value.trim();
    if (!path) {
      toast("请输入数据目录", "error");
      return;
    }
    const btn = $("#btn-storage-save");
    const prev = btn.textContent;
    btn.disabled = true;
    btn.textContent = "保存中…";
    try {
      const settings = await api("/api/settings/storage", {
        method: "PUT",
        body: JSON.stringify({ path }),
      });
      renderStorageSettings(settings);
      closeStorageModal();
      toast("数据目录已更新，现有数据已保留", "success");
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = prev;
    }
  }

  function isPageVisible() {
    return document.visibilityState !== "hidden";
  }

  function pollIntervalMs() {
    return isPageVisible() ? POLL_MS : POLL_MS_HIDDEN;
  }

  function stopPolling() {
    if (pollTimer != null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(() => {
      pollStatus();
    }, pollIntervalMs());
  }

  async function pollStatus() {
    if (pollInFlight) return;
    pollInFlight = true;
    try {
      const statuses = await api("/api/services/status");
      const map = Object.fromEntries(statuses.map((x) => [x.id, x]));
      let changed = false;
      for (const s of services) {
        const st = map[s.id];
        if (!st) continue;
        if (
          s.running !== st.running ||
          s.pid !== st.pid ||
          s.process_name !== st.process_name
        ) {
          s.running = st.running;
          s.pid = st.pid;
          s.pids = st.pids;
          s.process_name = st.process_name;
          changed = true;
        }
      }
      if (changed) render();
      else renderStats();
    } catch {
      /* ignore poll errors */
    } finally {
      pollInFlight = false;
    }
  }

  // ---------- Modal ----------
  function fillSelect(select, options, selected, emptyLabel = "— 未选择 —") {
    const all = new Set(options.filter(Boolean));
    if (selected) all.add(selected);
    const list = [...all];
    select.innerHTML =
      `<option value="">${escapeHtml(emptyLabel)}</option>` +
      list
        .map(
          (o) =>
            `<option value="${escapeAttr(o)}" ${o === selected ? "selected" : ""}>${escapeHtml(o)}</option>`
        )
        .join("");
  }

  function openModal(service = null) {
    editingId = service?.id || null;
    $("#modal-title").textContent = editingId ? "编辑服务" : "添加服务";
    $("#btn-refresh-icon").hidden = !editingId;
    $("#f-id").value = editingId || "";
    $("#f-name").value = service?.name || "";
    $("#f-notes").value = service?.notes || "";
    $("#f-directory").value = service?.directory || "";
    $("#f-port").value = service?.port || "";
    $("#f-webui").value = service?.webui_url || "";
    fillSelect(
      $("#f-start"),
      service?.start_script ? [service.start_script] : [],
      service?.start_script || ""
    );
    fillSelect(
      $("#f-stop"),
      service?.stop_script ? [service.stop_script] : [],
      service?.stop_script || "",
      "— 不使用，按端口结束进程 —"
    );
    $("#scan-result").hidden = true;
    $("#scan-result").textContent = "";
    modal.hidden = false;
    $("#f-port").focus();
  }

  function closeModal() {
    modal.hidden = true;
    editingId = null;
    form.reset();
  }

  async function doScan() {
    const directory = $("#f-directory").value.trim();
    if (!directory) {
      toast("请先填写服务目录", "error");
      return;
    }
    try {
      const result = await api("/api/services/scan", {
        method: "POST",
        body: JSON.stringify({ directory }),
      });
      const startOpts = [
        ...result.start_scripts,
        ...result.other_scripts,
      ];
      const stopOpts = result.stop_scripts;
      const curStart = $("#f-start").value;
      const curStop = $("#f-stop").value;
      fillSelect(
        $("#f-start"),
        startOpts,
        curStart || result.suggested_start || ""
      );
      fillSelect(
        $("#f-stop"),
        stopOpts,
        curStop || "",
        "— 不使用，按端口结束进程 —"
      );

      const box = $("#scan-result");
      box.hidden = false;
      box.innerHTML = `
        扫描完成：启动候选 <strong>${result.start_scripts.length}</strong>，
        停止脚本候选 <strong>${result.stop_scripts.length}</strong>（可选），
        其他脚本 <strong>${result.other_scripts.length}</strong>
      `;
      toast("脚本扫描完成", "success");
    } catch (e) {
      toast(e.message, "error");
    }
  }

  async function discoverFromPort() {
    const port = Number($("#f-port").value);
    if (!port || port < 1 || port > 65535) {
      toast("请先填写有效端口", "error");
      return;
    }
    const btn = $("#btn-discover");
    const prev = btn.textContent;
    btn.disabled = true;
    btn.textContent = "探测中…";
    try {
      const result = await api("/api/services/discover", {
        method: "POST",
        body: JSON.stringify({ port }),
      });

      if (!$("#f-webui").value.trim()) {
        $("#f-webui").value = `http://127.0.0.1:${port}/`;
      }
      if (result.suggested_directory) {
        $("#f-directory").value = result.suggested_directory;
        $("#f-directory").readOnly = true;
      }
      if (!$("#f-name").value.trim() && result.suggested_name) {
        $("#f-name").value = result.suggested_name;
      }

      const startOpts = [
        ...(result.start_scripts || []),
        ...(result.other_scripts || []),
      ];
      fillSelect($("#f-start"), startOpts, result.suggested_start || "");
      fillSelect(
        $("#f-stop"),
        result.stop_scripts || [],
        "",
        "— 不使用，按端口结束进程 —"
      );

      const box = $("#scan-result");
      box.hidden = false;
      const cand = (result.directory_candidates || []).slice(0, 3).join(" | ");
      box.innerHTML = `
        探测成功：进程 <strong>${escapeHtml(result.process_name || "?")}</strong>
        (PID ${result.pid}) · 目录
        <strong>${escapeHtml(result.suggested_directory || "未识别")}</strong>
        · 启动脚本 <strong>${escapeHtml(result.suggested_start || "未找到")}</strong>
        ${cand ? `<br/><span class="hint">候选目录: ${escapeHtml(cand)}</span>` : ""}
      `;
      toast("已根据端口完成探测", "success");
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = prev;
    }
  }

  // ---------- Actions ----------
  async function handleCardAction(id, action) {
    const s = services.find((x) => x.id === id);
    if (!s) return;

    try {
      if (action === "open") {
        const url = s.webui_url || `http://127.0.0.1:${s.port}/`;
        window.open(url, "_blank", "noopener,noreferrer");
        return;
      }
      if (action === "edit") {
        openModal(s);
        return;
      }
      if (action === "delete") {
        if (!confirm(`确定删除服务「${s.name}」？\n不会删除服务目录本身。`)) return;
        await api(`/api/services/${id}`, { method: "DELETE" });
        toast("已删除", "success");
        await loadServices();
        return;
      }
      if (action === "start") {
        const r = await api(`/api/services/${id}/start`, {
          method: "POST",
          body: JSON.stringify({ hidden: false }),
        });
        toast(r.message || "已发送启动命令", "success");
        setTimeout(pollStatus, 1500);
        return;
      }
      if (action === "start-hidden") {
        const r = await api(`/api/services/${id}/start`, {
          method: "POST",
          body: JSON.stringify({ hidden: true }),
        });
        toast(r.message || "已无窗口启动", "success");
        setTimeout(pollStatus, 1500);
        return;
      }
      if (action === "stop") {
        const r = await api(`/api/services/${id}/stop`, {
          method: "POST",
          body: JSON.stringify({ mode: "kill" }),
        });
        toast(r.message || "已停止占用端口的进程", r.ok === false ? "error" : "success");
        setTimeout(pollStatus, 800);
        setTimeout(pollStatus, 2000);
        return;
      }
    } catch (e) {
      toast(e.message, "error");
    }
  }

  async function saveService(e) {
    e.preventDefault();
    const portVal = Number($("#f-port").value);
    const payload = {
      name: $("#f-name").value.trim() || null,
      notes: $("#f-notes").value.trim(),
      directory: $("#f-directory").value.trim() || null,
      port: portVal,
      webui_url: $("#f-webui").value.trim() || null,
      start_script: $("#f-start").value || null,
      stop_script: $("#f-stop").value || null,
      auto_discover: true,
    };

    try {
      if (editingId) {
        // updates require explicit fields; keep directory if present
        const updateBody = { ...payload };
        delete updateBody.auto_discover;
        if (!updateBody.name) {
          toast("编辑时名称不能为空", "error");
          return;
        }
        await api(`/api/services/${editingId}`, {
          method: "PUT",
          body: JSON.stringify(updateBody),
        });
        toast("已保存", "success");
      } else {
        await api("/api/services", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        toast("服务已添加", "success");
      }
      closeModal();
      await loadServices();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  // ---------- Events ----------
  $("#btn-add").addEventListener("click", () => openModal());
  $("#btn-add-empty").addEventListener("click", () => openModal());
  $("#btn-refresh").addEventListener("click", async () => {
    try {
      await loadServices();
      toast("已刷新", "success");
    } catch (e) {
      toast(e.message, "error");
    }
  });
  $("#modal-close").addEventListener("click", closeModal);
  $("#btn-cancel").addEventListener("click", closeModal);
  $("#btn-scan").addEventListener("click", doScan);
  $("#btn-discover").addEventListener("click", discoverFromPort);
  $("#btn-browse").addEventListener("click", browseFolder);
  $("#btn-storage-change").addEventListener("click", openStorageModal);
  $("#btn-storage-reset").addEventListener("click", async () => {
    if (!storageSettings?.is_custom) return;
    $("#f-storage-path").value = storageSettings.default_path;
    await saveStorageDirectory({ preventDefault() {} });
  });
  $("#storage-modal-close").addEventListener("click", closeStorageModal);
  $("#btn-storage-cancel").addEventListener("click", closeStorageModal);
  $("#btn-storage-browse").addEventListener("click", browseStorageDirectory);
  storageForm.addEventListener("submit", saveStorageDirectory);
  // Single click on path field also opens folder picker
  $("#f-directory").addEventListener("click", () => {
    if ($("#f-directory").readOnly) browseFolder();
  });
  // Double-click to type path manually
  $("#f-directory").addEventListener("dblclick", (e) => {
    e.preventDefault();
    $("#f-directory").readOnly = false;
    $("#f-directory").focus();
    $("#f-directory").select();
  });
  form.addEventListener("submit", saveService);

  async function browseFolder() {
    const btn = $("#btn-browse");
    const prev = btn.textContent;
    btn.disabled = true;
    btn.textContent = "选择中…";
    try {
      const body = {
        initial_dir: $("#f-directory").value.trim() || null,
      };
      const r = await api("/api/browse-folder", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (r.cancelled || !r.path) {
        toast("已取消选择", "info");
        return;
      }
      $("#f-directory").value = r.path;
      $("#f-directory").readOnly = true;
      // Auto-fill name from folder name if empty
      if (!$("#f-name").value.trim()) {
        const parts = r.path.replace(/[\\/]+$/, "").split(/[/\\]/);
        const base = parts[parts.length - 1] || "";
        if (base) $("#f-name").value = base;
      }
      toast("已选择目录", "success");
      await doScan();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = prev;
    }
  }

  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  storageModal.addEventListener("click", (e) => {
    if (e.target === storageModal) closeStorageModal();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.hidden) closeModal();
  });

  $("#f-port").addEventListener("change", () => {
    const port = $("#f-port").value;
    const webui = $("#f-webui");
    if (port && !webui.value.trim()) {
      webui.value = `http://127.0.0.1:${port}/`;
    }
  });

  // Enter on port field triggers discover when adding
  $("#f-port").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      discoverFromPort();
    }
  });

  $("#btn-refresh-icon").addEventListener("click", async () => {
    if (!editingId) return;
    try {
      const r = await api(`/api/services/${editingId}/refresh-icon`, { method: "POST" });
      if (r.ok) {
        toast("图标已更新", "success");
        await loadServices();
      } else {
        toast("未能获取图标（服务是否在运行？）", "error");
      }
    } catch (e) {
      toast(e.message, "error");
    }
  });

  grid.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const card = btn.closest(".card");
    if (!card) return;
    handleCardAction(card.dataset.id, btn.dataset.action);
  });

  // ---------- Theme events ----------
  document.querySelectorAll("[data-theme-set]").forEach((btn) => {
    btn.addEventListener("click", () => setTheme(btn.getAttribute("data-theme-set")));
  });

  // ---------- Init ----------
  async function init() {
    syncThemeToggle();
    try {
      await loadSharedTheme();
      await loadServices();
      await loadStorageSettings();
    } catch (e) {
      toast("加载失败: " + e.message, "error");
    }
    startPolling();
    document.addEventListener("visibilitychange", () => {
      // Hidden main/popup WebViews still run JS — slow down hard when not visible.
      startPolling();
      if (isPageVisible()) pollStatus();
    });
  }

  init();
})();
