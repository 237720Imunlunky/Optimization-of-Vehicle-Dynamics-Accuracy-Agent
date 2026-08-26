"use strict";

const state = {
  dashboard: null,
  activeView: "dashboard",
  jobTimer: null,
  lastJobStatus: null,
};

const viewMeta = {
  dashboard: ["OVERVIEW", "优化看板"],
  control: ["CONTROL", "优化控制"],
  parameters: ["PARAMETERS", "参数空间"],
  history: ["HISTORY", "迭代历史"],
  settings: ["SETTINGS", "API 配置"],
};

function formatScore(value) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(2) : "--";
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function showToast(message, isError = false) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 2800);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
  return payload;
}

function switchView(view) {
  state.activeView = view;
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  document.querySelectorAll(".view").forEach((panel) => panel.classList.toggle("active", panel.dataset.viewPanel === view));
  setText("view-eyebrow", viewMeta[view][0]);
  setText("view-title", viewMeta[view][1]);
  if (view === "dashboard" && state.dashboard) drawScoreChart(state.dashboard);
}

function updateStatus(api, system) {
  const apiReady = Boolean(api.configured);
  const carsimReady = Boolean(system.carsim_ready);
  setText("side-api", apiReady ? "已配置" : "待配置");
  setText("side-carsim", carsimReady ? "已连接" : "未找到");
  document.getElementById("side-api-dot").className = `status-dot ${apiReady ? "ready" : "warn"}`;
  document.getElementById("side-carsim-dot").className = `status-dot ${carsimReady ? "ready" : "warn"}`;
  setText("control-api-status", apiReady ? `${api.model} · 已配置` : "未配置真实API");
  setText("control-carsim-status", carsimReady ? "求解器可用" : "未找到求解器");
  document.getElementById("check-api-icon").className = `check-icon ${apiReady ? "ready" : "warn"}`;
  document.getElementById("check-carsim-icon").className = `check-icon ${carsimReady ? "ready" : "warn"}`;
  const configState = document.getElementById("config-state");
  configState.textContent = apiReady ? "配置完整" : "等待填写";
  configState.className = `config-state ${apiReady ? "ready" : "warn"}`;
  setText("config-key", api.api_key_set ? "已填写（内容隐藏）" : "未填写");
  setText("config-url", api.base_url || "未填写");
  setText("config-model", api.model || "未填写");
  setText("config-timeout", api.timeout_s ? `${api.timeout_s}s` : "未填写");
  setText("config-path", api.path || "--");
}

function renderMetrics(rows) {
  const body = document.getElementById("metric-table-body");
  body.replaceChildren();
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    const values = [row.role_label, row.metric_label, `${formatScore(row.score_pct)}%`, `${formatScore(row.target_pct)}%`];
    values.forEach((value, index) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (index === 2) td.className = "score-cell";
      tr.appendChild(td);
    });
    const statusCell = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = `status-pill ${row.passed ? "pass" : "fail"}`;
    pill.textContent = row.passed ? "通过" : "待优化";
    statusCell.appendChild(pill);
    tr.appendChild(statusCell);
    body.appendChild(tr);
  });
  setText("metric-count", `${rows.filter((row) => row.passed).length}/${rows.length} 通过`);
}

function renderFailedDetails(rows) {
  // 显示逐条评价中未通过的工况、指标、当前精度和目标值。
  const list = document.getElementById("failed-detail-list");
  const details = document.getElementById("failed-details");
  if (!list || !details) return;
  list.replaceChildren();
  const failed = rows || [];
  details.hidden = failed.length === 0;
  failed.forEach((row) => {
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = `${row.role_label} · ${row.metric_label}`;
    const value = document.createElement("strong");
    value.textContent = `${formatScore(row.score_pct)}% / 目标 ${formatScore(row.target_pct)}%`;
    item.append(label, value);
    list.appendChild(item);
  });
}

function renderParameters(rows) {
  const body = document.getElementById("parameter-table-body");
  body.replaceChildren();
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    const cells = [
      row.label,
      `${row.value} ${row.unit === "ratio" ? "" : row.unit}`.trim(),
      String(row.baseline),
      `${row.minimum} - ${row.maximum}`,
    ];
    cells.forEach((value, index) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (index === 1) td.className = "score-cell";
      tr.appendChild(td);
    });
    const positionCell = document.createElement("td");
    positionCell.className = "parameter-position";
    const track = document.createElement("div");
    track.className = "parameter-track";
    const fill = document.createElement("span");
    const position = Math.max(0, Math.min(100, (Number(row.value) - Number(row.minimum)) / (Number(row.maximum) - Number(row.minimum)) * 100));
    fill.style.width = `${position}%`;
    track.appendChild(fill);
    positionCell.appendChild(track);
    tr.appendChild(positionCell);
    body.appendChild(tr);
  });
}

function renderHistory(rows) {
  const body = document.getElementById("history-table-body");
  body.replaceChildren();
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = row.name;
    name.title = row.path;
    tr.appendChild(name);
    const status = document.createElement("td");
    const pill = document.createElement("span");
    const statusClass = row.status === "已提升" ? "pass" : row.status === "已回退" ? "fail" : "neutral";
    pill.className = `status-pill ${statusClass}`;
    pill.textContent = row.status;
    status.appendChild(pill);
    tr.appendChild(status);
    const score = document.createElement("td");
    score.className = "score-cell";
    score.textContent = row.score_pct == null ? "--" : `${formatScore(row.score_pct)}%`;
    tr.appendChild(score);
    const time = document.createElement("td");
    time.textContent = row.updated_at.replace("T", " ");
    tr.appendChild(time);
    body.appendChild(tr);
  });
}

function drawScoreChart(payload) {
  const canvas = document.getElementById("score-chart");
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 10 || rect.height < 10) return;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * ratio);
  canvas.height = Math.floor(rect.height * ratio);
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  const width = rect.width;
  const height = rect.height;
  const padding = { top: 18, right: 18, bottom: 35, left: 40 };
  const plotHeight = height - padding.top - padding.bottom;
  const labels = ["综合", "加速", "滑行"];
  const baseline = [
    payload.scores.baseline.longitudinal_score_pct,
    payload.scores.baseline.group_scores_pct.acceleration,
    payload.scores.baseline.group_scores_pct.coasting,
  ];
  const current = [
    payload.scores.current.longitudinal_score_pct,
    payload.scores.current.group_scores_pct.acceleration,
    payload.scores.current.group_scores_pct.coasting,
  ];
  const chart = payload.scores.chart;
  const minimum = Number(chart.minimum_pct);
  const maximum = Number(chart.maximum_pct);
  const ticks = chart.ticks_pct.map(Number);
  const target = Number(payload.scores.target_pct);
  const yFor = (value) => padding.top + (maximum - Math.max(minimum, Math.min(maximum, Number(value)))) / (maximum - minimum) * plotHeight;
  context.clearRect(0, 0, width, height);
  context.font = '10px "Segoe UI", "Microsoft YaHei", sans-serif';
  context.textAlign = "right";
  context.textBaseline = "middle";
  ticks.forEach((tick) => {
    const y = yFor(tick);
    context.strokeStyle = Math.abs(tick - target) < 1e-9 ? "#b8c6c3" : "#e8eceb";
    context.setLineDash(Math.abs(tick - target) < 1e-9 ? [4, 4] : []);
    context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
    context.fillStyle = "#7a8582"; context.fillText(`${tick}`, padding.left - 8, y);
  });
  if (target >= minimum && target <= maximum && !ticks.some((tick) => Math.abs(tick - target) < 1e-9)) {
    const y = yFor(target);
    context.strokeStyle = "#b8c6c3";
    context.setLineDash([4, 4]);
    context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
    context.setLineDash([]);
  }
  context.setLineDash([]);
  const groupWidth = (width - padding.left - padding.right) / labels.length;
  const barWidth = Math.min(30, groupWidth * 0.22);
  labels.forEach((label, index) => {
    const center = padding.left + groupWidth * (index + 0.5);
    [[baseline[index], "#bbc4c2", center - barWidth - 3], [current[index], "#087f73", center + 3]].forEach(([value, color, x]) => {
      const top = yFor(value);
      context.fillStyle = color;
      context.fillRect(x, top, barWidth, padding.top + plotHeight - top);
    });
    context.fillStyle = "#5f6a68"; context.textAlign = "center"; context.textBaseline = "top";
    context.fillText(label, center, height - padding.bottom + 12);
  });
}

function renderDashboard(payload) {
  state.dashboard = payload;
  const baseline = payload.scores.baseline;
  const current = payload.scores.current;
  const overallDelta = Number(current.longitudinal_score_pct) - Number(baseline.longitudinal_score_pct);
  setText("score-overall", formatScore(current.longitudinal_score_pct));
  setText("score-baseline", `${formatScore(baseline.longitudinal_score_pct)}%`);
  setText("score-overall-delta", `+${formatScore(overallDelta)}%`);
  document.getElementById("score-overall-delta").className = "positive";
  setText("score-acceleration", formatScore(current.group_scores_pct.acceleration));
  setText("score-coasting", formatScore(current.group_scores_pct.coasting));
  document.getElementById("bar-acceleration").style.width = `${Math.min(100, current.group_scores_pct.acceleration)}%`;
  document.getElementById("bar-coasting").style.width = `${Math.min(100, current.group_scores_pct.coasting)}%`;
  setText("failed-count", String(current.failed_metric_count));
  setText("baseline-failed-count", `原基线 ${baseline.failed_metric_count ?? "--"}项`);
  setText("failed-delta", `减少 ${Number(baseline.failed_metric_count ?? current.failed_metric_count) - Number(current.failed_metric_count)} 项`);
  document.getElementById("failed-delta").className = "positive";
  setText("best-source", payload.agent.best_source);
  setText("iteration-number", String(payload.agent.iteration));
  setText("no-improvement", `${payload.agent.no_improvement_iterations} 轮`);
  const syncLabels = { matched: "已同步", legacy_matched: "已核对", stale: "需重算基线", unknown: "待核对" };
  setText("config-sync", syncLabels[payload.agent.config_sync.status] || "待核对");
  const torque = payload.parameters.find((item) => item.name === "motor_low_speed_torque_scale");
  setText("torque-scale", torque ? torque.value.toFixed(2) : "--");
  setText(
    "control-best-label",
    `当前最优点 ${formatScore(current.longitudinal_score_pct)}% · 迭代 ${payload.agent.iteration} · 经验 ${payload.agent.memory_rounds || 0} 轮`,
  );
  setText("sync-time", `同步 ${payload.generated_at.replace("T", " ")}`);
  updateStatus(payload.api, payload.system);
  renderMetrics(payload.metrics);
  renderFailedDetails(payload.failed_details);
  renderParameters(payload.parameters);
  renderHistory(payload.history);
  if (state.activeView === "dashboard") drawScoreChart(payload);
}

async function loadDashboard(showMessage = false) {
  try {
    const payload = await requestJson("/api/dashboard");
    renderDashboard(payload);
    if (showMessage) showToast("看板已刷新");
  } catch (error) {
    showToast(error.message, true);
  }
}

function selectedMode() {
  return document.querySelector('input[name="run-mode"]:checked').value;
}

function updateRunButton() {
  const mode = selectedMode();
  const button = document.getElementById("start-job-button");
  const apiReady = Boolean(state.dashboard && state.dashboard.api.configured);
  const configReady = Boolean(state.dashboard && ["matched", "legacy_matched"].includes(state.dashboard.agent.config_sync.status));
  button.querySelector("span").textContent = mode === "dry_run" ? "执行干运行" : "开始完整优化";
  button.disabled = mode === "full_iteration" && (!apiReady || !configReady);
}

async function startJob() {
  const mode = selectedMode();
  if (mode === "full_iteration") {
    const destination = state.dashboard.api.base_url || "配置的模型服务";
    const confirmed = window.confirm(`将把当前车辆指标、参数值和物理边界发送到：\n${destination}\n\nAPI密钥由本地后端使用，不会显示在页面。确认开始？`);
    if (!confirmed) return;
  }
  try {
    await requestJson("/api/jobs/start", { method: "POST", body: JSON.stringify({ mode }) });
    state.lastJobStatus = "running";
    const badge = document.getElementById("job-status-badge");
    badge.textContent = "RUNNING";
    badge.className = "job-badge running";
    document.getElementById("job-log").textContent = "任务已提交，等待后端输出...";
    showToast(mode === "dry_run" ? "干运行已启动" : "完整优化已启动");
    pollJob();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function pollJob() {
  try {
    const job = await requestJson("/api/job");
    const previousStatus = state.lastJobStatus;
    state.lastJobStatus = job.status;
    const badge = document.getElementById("job-status-badge");
    badge.textContent = job.status.toUpperCase();
    badge.className = `job-badge ${job.status}`;
    const log = document.getElementById("job-log");
    log.textContent = job.logs.length ? job.logs.join("\n") : "等待任务输出...";
    log.scrollTop = log.scrollHeight;
    const configReady = ["matched", "legacy_matched"].includes(state.dashboard.agent.config_sync.status);
    document.getElementById("start-job-button").disabled = job.status === "running" || (selectedMode() === "full_iteration" && (!state.dashboard.api.configured || !configReady));
    if (job.status === "running") {
      window.clearTimeout(state.jobTimer);
      state.jobTimer = window.setTimeout(pollJob, 1000);
    } else if (job.status === "completed" && previousStatus === "running") {
      showToast("任务完成，正在刷新看板");
      await loadDashboard();
    } else if (job.status === "failed" && previousStatus === "running") {
      showToast(job.error || "任务失败，当前最优点未改变", true);
    }
  } catch (error) {
    showToast(error.message, true);
  }
}

async function openConfig() {
  try {
    await requestJson("/api/config/open", { method: "POST", body: "{}" });
    showToast("配置文件已打开");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function checkConfig() {
  try {
    const api = await requestJson("/api/config/check", { method: "POST", body: "{}" });
    state.dashboard.api = api;
    updateStatus(api, state.dashboard.system);
    updateRunButton();
    showToast(api.configured ? "API配置完整" : "API配置尚未填写", !api.configured);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function copyConfigPath() {
  try {
    await navigator.clipboard.writeText(document.getElementById("config-path").textContent);
    showToast("配置路径已复制");
  } catch (error) {
    showToast("浏览器未允许复制，请直接查看页面路径", true);
  }
}

function bindInteractions() {
  document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  document.querySelectorAll("[data-go-control]").forEach((button) => button.addEventListener("click", () => switchView("control")));
  document.getElementById("refresh-button").addEventListener("click", () => loadDashboard(true));
  document.getElementById("history-refresh").addEventListener("click", () => loadDashboard(true));
  document.querySelectorAll('input[name="run-mode"]').forEach((input) => input.addEventListener("change", updateRunButton));
  document.getElementById("start-job-button").addEventListener("click", startJob);
  document.getElementById("open-config-button").addEventListener("click", openConfig);
  document.getElementById("open-config-shortcut").addEventListener("click", () => switchView("settings"));
  document.getElementById("check-config-button").addEventListener("click", checkConfig);
  document.getElementById("copy-config-path").addEventListener("click", copyConfigPath);
  window.addEventListener("resize", () => { if (state.activeView === "dashboard" && state.dashboard) drawScoreChart(state.dashboard); });
}

document.addEventListener("DOMContentLoaded", async () => {
  if (window.lucide) window.lucide.createIcons();
  bindInteractions();
  await loadDashboard();
  updateRunButton();
  await pollJob();
});
