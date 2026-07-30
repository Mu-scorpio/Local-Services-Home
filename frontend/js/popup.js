(() => {
  "use strict";

  const POLL_MS = 4000;
  let services = [];

  const $ = (sel, root = document) => root.querySelector(sel);
  const listEl = $("#service-list");

  // The popup is a separate pywebview window. Mirror the main page theme on
  // load, on storage events, and through a small fallback poll for runtimes
  // that isolate localStorage between windows.
  function applyTheme(theme) {
    const next = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
  }

  function syncThemeFromStorage() {
    try {
      const stored = localStorage.getItem("lsm-theme");
      if (stored === "light" || stored === "dark") applyTheme(stored);
    } catch {
      /* ignore */
    }
  }

  syncThemeFromStorage();
  window.addEventListener("storage", (event) => {
    if (event.key === "lsm-theme") applyTheme(event.newValue);
  });
  window.addEventListener("message", (event) => {
    if (event.data?.type === "lsm-theme-change") applyTheme(event.data.theme);
  });
  window.addEventListener("lsm-theme-change", (event) => applyTheme(event.detail));
  setInterval(syncThemeFromStorage, 750);

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
      const msg =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
            : res.statusText || "请求失败";
      throw new Error(msg);
    }
    return data;
  }

  async function syncThemeFromServer() {
    try {
      const result = await api("/api/settings/theme");
      if (result?.theme === "light" || result?.theme === "dark") {
        applyTheme(result.theme);
      } else {
        const stored = localStorage.getItem("lsm-theme");
        if (stored === "light" || stored === "dark") {
          await api("/api/settings/theme", {
            method: "PUT",
            body: JSON.stringify({ theme: stored }),
          });
        }
      }
    } catch {
      /* localStorage remains the fallback if the backend is unavailable */
    }
  }

  setInterval(syncThemeFromServer, 750);

  // ---------- Toast ----------
  let toastEl = null;
  function toast(message, type = "info") {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "popup-toast";
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = message;
    toastEl.className = `popup-toast ${type} show`;
    clearTimeout(toastEl._timer);
    toastEl._timer = setTimeout(() => {
      toastEl.classList.remove("show");
    }, 2200);
  }

  // ---------- Render ----------
  function renderStats() {
    const total = services.length;
    const running = services.filter((s) => s.running).length;
    $("#stat-running").textContent = String(running);
    $("#stat-total").textContent = String(total);
  }

  function renderList() {
    renderStats();

    if (!services.length) {
      listEl.innerHTML = `<div class="empty-msg">还没有服务<br/>点击下方「添加服务」开始</div>`;
      return;
    }

    listEl.innerHTML = services
      .map((s) => {
        const dotClass = s.running ? "running" : "stopped";
        const statusText = s.running ? `:${s.port}` : `:${s.port} · 已停止`;
        return `
        <div class="svc-item" data-id="${s.id}">
          <span class="svc-dot ${dotClass}"></span>
          <div class="svc-info">
            <div class="svc-name" title="${escapeAttr(s.name)}">${escapeHtml(s.name)}</div>
            <div class="svc-port">${statusText}</div>
          </div>
          <div class="svc-actions">
            <button class="svc-btn btn-open" data-action="open" title="打开 WebUI">打开</button>
            <button class="svc-btn btn-start" data-action="start" ${s.running ? "disabled" : ""} title="启动">▶</button>
            <button class="svc-btn btn-stop" data-action="stop" ${!s.running ? "disabled" : ""} title="停止">■</button>
          </div>
        </div>`;
      })
      .join("");
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
    try {
      services = await api("/api/services");
      renderList();
      $("#footer-status").textContent = "已刷新";
    } catch (e) {
      $("#footer-status").textContent = "连接失败";
    }
  }

  async function pollStatus() {
    try {
      const statuses = await api("/api/services/status");
      const map = Object.fromEntries(statuses.map((x) => [x.id, x]));
      let changed = false;
      for (const s of services) {
        const st = map[s.id];
        if (!st) continue;
        if (s.running !== st.running || s.pid !== st.pid) {
          s.running = st.running;
          s.pid = st.pid;
          s.process_name = st.process_name;
          changed = true;
        }
      }
      if (changed) renderList();
      else renderStats();
    } catch {
      /* ignore */
    }
  }

  // ---------- Actions ----------
  async function handleAction(id, action) {
    const s = services.find((x) => x.id === id);
    if (!s) return;

    try {
      if (action === "open") {
        const url = s.webui_url || `http://127.0.0.1:${s.port}/`;
        // Try pywebview API first, fallback to window.open
        if (window.pywebview && window.pywebview.api) {
          await window.pywebview.api.open_external(url);
        } else {
          window.open(url, "_blank");
        }
        return;
      }
      if (action === "start") {
        $("#footer-status").textContent = `启动中: ${s.name}...`;
        const r = await api(`/api/services/${id}/start`, {
          method: "POST",
          body: JSON.stringify({ hidden: false }),
        });
        toast(r.message || "已启动", "success");
        $("#footer-status").textContent = "就绪";
        setTimeout(pollStatus, 1500);
        return;
      }
      if (action === "stop") {
        $("#footer-status").textContent = `停止中: ${s.name}...`;
        const r = await api(`/api/services/${id}/stop`, {
          method: "POST",
          body: JSON.stringify({ mode: "kill" }),
        });
        toast(r.message || "已停止", "success");
        $("#footer-status").textContent = "就绪";
        setTimeout(pollStatus, 800);
        setTimeout(pollStatus, 2000);
        return;
      }
    } catch (e) {
      toast(e.message, "error");
      $("#footer-status").textContent = "就绪";
    }
  }

  // ---------- Events ----------
  listEl.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const item = btn.closest(".svc-item");
    if (!item) return;
    handleAction(item.dataset.id, btn.dataset.action);
  });

  $("#btn-refresh").addEventListener("click", () => {
    loadServices();
  });

  $("#btn-close").addEventListener("click", async () => {
    if (window.pywebview && window.pywebview.api) {
      await window.pywebview.api.close_popup();
    } else {
      window.close();
    }
  });

  $("#btn-open-main").addEventListener("click", async () => {
    if (window.pywebview && window.pywebview.api) {
      await window.pywebview.api.open_main_window();
    } else {
      window.open("/", "_blank");
    }
  });

  $("#btn-add-service").addEventListener("click", async () => {
    if (window.pywebview && window.pywebview.api) {
      await window.pywebview.api.open_main_window();
    } else {
      window.open("/", "_blank");
    }
  });

  // ---------- Init ----------
  async function init() {
    await syncThemeFromServer();
    await loadServices();
    setInterval(pollStatus, POLL_MS);
  }

  // Wait for pywebview API to be ready
  if (window.pywebview && window.pywebview.api) {
    init();
  } else {
    window.addEventListener("pywebviewready", init);
    // Fallback: init anyway after short delay (for browser mode)
    setTimeout(() => {
      if (!services.length) init();
    }, 500);
  }
})();
