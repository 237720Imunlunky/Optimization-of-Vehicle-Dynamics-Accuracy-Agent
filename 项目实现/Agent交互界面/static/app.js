"use strict";

const state = {
  dashboard: null,
  activeView: "dashboard",
  jobTimer: null,
  lastJobStatus: null,
  admission: null,
  historyStorage: null,
  job: null,
};

const viewMeta = {
  dashboard: ["OVERVIEW", "优化看板"],
  control: ["CONTROL", "优化控制"],
  parameters: ["PARAMETERS", "参数空间"],
  history: ["HISTORY", "迭代历史"],
  admission: ["DATA", "数据准入"],
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
  const carsim = system.carsim || {};
  const carsimReady = Boolean(system.carsim_ready);
  setText("side-api", apiReady ? "已配置" : "待配置");
  setText("side-carsim", carsim.label || (carsimReady ? "文件就绪" : "未找到"));
  document.getElementById("side-api-dot").className = `status-dot ${apiReady ? "ready" : "warn"}`;
  document.getElementById("side-carsim-dot").className = `status-dot ${carsimReady ? "ready" : "warn"}`;
  setText("control-api-status", apiReady ? `${api.model} · 已配置` : "未配置真实API");
  setText("control-carsim-status", carsim.label || (carsimReady ? "文件就绪，许可证待验证" : "未找到求解器"));
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
  // 显示逐样本评价中的工况、重复编号、数据分组、指标和目标值。
  const list = document.getElementById("failed-detail-list");
  const details = document.getElementById("failed-details");
  const summary = document.getElementById("failed-detail-summary");
  if (!list || !details) return;
  list.replaceChildren();
  const failed = rows || [];
  details.hidden = failed.length === 0;
  if (summary) summary.textContent = `查看 ${failed.length} 条失败记录`;
  failed.forEach((row) => {
    const item = document.createElement("li");
    const description = document.createElement("div");
    const context = document.createElement("span");
    context.textContent = `${row.role_label} · 第${row.repeat_index}次 · ${row.dataset_split_label || "未分组"}`;
    const metric = document.createElement("small");
    metric.textContent = row.metric_label;
    description.append(context, metric);
    const value = document.createElement("strong");
    value.textContent = `${formatScore(row.score_pct)}% / 目标 ${formatScore(row.target_pct)}%`;
    item.append(description, value);
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

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  return value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(2)} GB` : `${(value / 1024 ** 2).toFixed(2)} MB`;
}

function renderStorage(payload) {
  state.historyStorage = payload;
  const summary = Number(payload.estimated_reclaim_bytes) > 0
    ? `合计 ${formatBytes(payload.total_size_bytes)} · 可释放 ${formatBytes(payload.estimated_reclaim_bytes)} · 完整保留最近 ${payload.retained_full_task_count} 次`
    : `合计 ${formatBytes(payload.total_size_bytes)} · 当前暂无满足清理条件的任务（最近任务、当前任务或摘要未验证）${payload.unarchived_task_count ? ` · ${payload.unarchived_task_count} 个旧任务尚未归档` : ""}`;
  setText("history-space-summary", summary);
  const body = document.getElementById("storage-table-body");
  body.replaceChildren();
  if (!payload.tasks.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.className = "muted-cell";
    td.textContent = "暂未发现可纳入历史管理的优化任务";
    tr.appendChild(td);
    body.appendChild(tr);
  }
  payload.tasks.forEach((task) => {
    const tr = document.createElement("tr");
    const values = [task.task_id, `${task.size_mb.toFixed(2)} MB`, task.summary_verified ? "已验证" : "未验证", task.protected ? task.protection_reasons.join("；") : "可清理", formatBytes(task.estimated_reclaim_bytes)];
    values.forEach((value, index) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (index === 3) td.className = task.protected ? "muted-cell" : "success-cell";
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
  document.getElementById("cleanup-history-button").disabled = Number(payload.estimated_reclaim_bytes) === 0;
}

function renderAdmission(payload) {
  state.admission = payload;
  const ready = Boolean(payload.ready_for_optimization);
  setText("control-admission-status", ready ? `批次 ${payload.batch_id} · 已就绪` : (payload.message || "存在待复核或样本不足"));
  document.getElementById("check-admission-icon").className = `check-icon ${ready ? "ready" : "warn"}`;
  const summary = document.getElementById("admission-summary");
  summary.textContent = payload.available ? `当前批次：${payload.batch_id} · 数据指纹：${payload.data_fingerprint} · ${ready ? "可进入完整优化" : "暂不可进入完整优化"}` : payload.message;
  const body = document.getElementById("admission-table-body");
  body.replaceChildren();
  Object.entries(payload.by_role || {}).forEach(([role, item]) => {
    const tr = document.createElement("tr");
    [role, item.accepted, item.rejected, item.pending_review, `${item.calibration}/${item.validation}`, item.ready ? "已就绪" : "未就绪"].forEach((value, index) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (index === 5) td.className = item.ready ? "success-cell" : "warning-cell";
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
  updateRunButton();
}

async function loadOperationalData(showMessage = false) {
  try {
    const [admission, storage] = await Promise.all([requestJson("/api/data-admission"), requestJson("/api/history/storage")]);
    renderAdmission(admission);
    renderStorage(storage);
    if (showMessage) showToast("准入与历史空间状态已刷新");
  } catch (error) {
    showToast(error.message, true);
  }
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
  const demoMode = Boolean(payload.system.demo_mode);
  document.getElementById("runtime-mode").hidden = !demoMode;
  setText("overall-state", demoMode ? "演示数据" : (current.formal_passed ? "已通过" : "未通过"));
  document.getElementById("overall-state").className = `metric-state ${demoMode || !current.formal_passed ? "warning" : "success"}`;
  setText("score-overall", formatScore(current.longitudinal_score_pct));
  setText("score-baseline", `${formatScore(baseline.longitudinal_score_pct)}%`);
  setText("score-overall-delta", `+${formatScore(overallDelta)}%`);
  document.getElementById("score-overall-delta").className = "positive";
  setText("score-acceleration", formatScore(current.group_scores_pct.acceleration));
  setText("score-coasting", formatScore(current.group_scores_pct.coasting));
  document.getElementById("bar-acceleration").style.width = `${Math.min(100, current.group_scores_pct.acceleration)}%`;
  document.getElementById("bar-coasting").style.width = `${Math.min(100, current.group_scores_pct.coasting)}%`;
  const currentChecks = payload.scores.metric_checks?.current || {};
  const baselineChecks = payload.scores.metric_checks?.baseline || {};
  const currentFailed = Number(currentChecks.failed ?? current.failed_metric_count ?? 0);
  const baselineFailed = Number(baselineChecks.failed ?? baseline.failed_metric_count ?? currentFailed);
  setText("failed-count", `${currentFailed}/${currentChecks.total ?? "--"}`);
  setText("baseline-failed-count", `原基线 ${baselineFailed}/${baselineChecks.total ?? "--"}`);
  setText("failed-delta", `减少 ${baselineFailed - currentFailed} 项`);
  document.getElementById("failed-delta").className = "positive";
  setText("best-source", payload.agent.best_source);
  setText("iteration-number", String(payload.agent.iteration));
  setText("no-improvement", `${payload.agent.no_improvement_iterations} 轮`);
  const syncLabels = { matched: "已同步", legacy_matched: "已核对", stale: "需重算基线", unknown: "待核对" };
  setText("config-sync", syncLabels[payload.agent.config_sync.status] || "待核对");
  const changeSummary = payload.agent.parameter_change_summary || { text: "正式基线", details: [] };
  setText("parameter-change-summary", changeSummary.text);
  document.getElementById("parameter-change-summary").title = changeSummary.details.length
    ? changeSummary.details.map((item) => `${item.label}: ${item.baseline} → ${item.current}`).join("\n")
    : "当前最优参数与正式基线一致";
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

function selectedMemoryMode() {
  return document.querySelector('input[name="memory-mode"]:checked').value;
}

function updateRunButton() {
  const mode = selectedMode();
  const button = document.getElementById("start-job-button");
  const apiReady = Boolean(state.dashboard && state.dashboard.api.configured);
  const configReady = Boolean(state.dashboard && ["matched", "legacy_matched"].includes(state.dashboard.agent.config_sync.status));
  button.querySelector("span").textContent = mode === "dry_run" ? "执行干运行" : "开始完整优化";
  const admissionReady = Boolean(state.admission && state.admission.ready_for_optimization);
  const carsimReady = Boolean(state.dashboard && state.dashboard.system.carsim_ready);
  const active = ["running", "pausing", "paused", "stopping"].includes(state.job?.status);
  button.disabled = active || (mode === "full_iteration" && (!apiReady || !configReady || !admissionReady || !carsimReady));
  const baselineButton = document.getElementById("generate-baseline-button");
  if (baselineButton) baselineButton.disabled = active || !(state.dashboard && state.dashboard.system.carsim?.solver && state.dashboard.system.carsim?.dll && state.dashboard.system.carsim?.template && admissionReady);
}

async function generateFormalBaseline() {
  if (!window.confirm("将使用当前准入数据调用CarSim，生成本车正式基线。请确认CarSim License Manager已启动。")) return;
  try {
    await requestJson("/api/jobs/start", { method: "POST", body: JSON.stringify({ mode: "formal_baseline" }) });
    showToast("正式基线生成任务已启动");
    state.lastJobStatus = "running";
    pollJob();
  } catch (error) { showToast(error.message, true); }
}

function updateJobControls(job) {
  /** 根据任务状态切换暂停、继续和停止按钮，干运行不开放暂停。 */
  state.job = job;
  const status = job?.status || "idle";
  const fullTask = job?.mode === "full_iteration";
  const pauseButton = document.getElementById("pause-job-button");
  const pauseLabel = pauseButton.querySelector("span");
  const pauseIcon = document.getElementById("pause-job-icon");
  const resumeMode = ["pausing", "paused"].includes(status);
  pauseLabel.textContent = resumeMode ? "继续" : "暂停";
  pauseIcon.setAttribute("data-lucide", resumeMode ? "play" : "pause");
  pauseButton.disabled = !fullTask || !["running", "pausing", "paused"].includes(status);
  document.getElementById("stop-job-button").disabled = !fullTask || !["running", "pausing", "paused"].includes(status);
  if (window.lucide) window.lucide.createIcons();
  updateRunButton();
}

async function startJob() {
  const mode = selectedMode();
  const memoryMode = selectedMemoryMode();
  if (mode === "full_iteration") {
    const destination = state.dashboard.api.base_url || "配置的模型服务";
    const confirmed = window.confirm(`将把当前车辆指标、参数值和物理边界发送到：\n${destination}\n\nAPI密钥由本地后端使用，不会显示在页面。确认开始？`);
    if (!confirmed) return;
  }
  try {
    await requestJson("/api/jobs/start", { method: "POST", body: JSON.stringify({ mode, memory_mode: memoryMode }) });
    state.lastJobStatus = "running";
    const badge = document.getElementById("job-status-badge");
    badge.textContent = "RUNNING";
    badge.className = "job-badge running";
    renderJob({
      status: "running",
      logs: ["任务已提交，等待后端输出..."],
      // 真实循环上限直接取后端 config.json，避免页面单独维护一个默认轮数。
      progress: { current_round: 0, max_rounds: mode === "dry_run" ? 0 : Number(state.dashboard?.agent?.maximum_iterations || 0), phase_label: "准备启动", candidates: [] },
    });
    showToast(mode === "dry_run" ? "干运行已启动" : `完整优化已启动（${memoryMode === "fresh" ? "全新经验" : "继承经验"}）`);
    pollJob();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function togglePauseJob() {
  const status = state.job?.status;
  const resume = ["pausing", "paused"].includes(status);
  try {
    const job = await requestJson(resume ? "/api/jobs/resume" : "/api/jobs/pause", { method: "POST", body: "{}" });
    renderJob(job);
    showToast(resume ? "优化已继续" : "已请求安全暂停");
    pollJob();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function stopJob() {
  if (!window.confirm("将在当前候选或模型调用结束后安全停止，并保留已完成结果与当前最优点。确认停止？")) return;
  try {
    const job = await requestJson("/api/jobs/stop", { method: "POST", body: "{}" });
    renderJob(job);
    showToast("已请求安全停止");
    pollJob();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function runAdmission() {
  if (!window.confirm("将读取现有解码数据并在输出/数据准入中生成分类副本。原始BLF不会移动或修改。确认继续？")) return;
  const button = document.getElementById("run-admission-button");
  button.disabled = true;
  try {
    const result = await requestJson("/api/data-admission/run", { method: "POST", body: "{}" });
    showToast(`准入批次 ${result.batch_id} 已生成`);
    await loadOperationalData();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function cleanupHistory() {
  const reclaim = Number(state.historyStorage?.estimated_reclaim_bytes || 0);
  if (!reclaim || !window.confirm(`将永久删除已验证且不受保护的CarSim原始历史，预计释放 ${formatBytes(reclaim)}。永久任务摘要和关键证据会保留。确认继续？`)) return;
  try {
    const result = await requestJson("/api/history/cleanup", { method: "POST", body: JSON.stringify({ confirm: "DELETE_VERIFIED_HISTORY" }) });
    showToast(`已清理 ${result.removed_task_ids.length} 个任务，释放 ${formatBytes(result.reclaimed_bytes)}`);
    await loadOperationalData();
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderCandidateCards(candidates) {
  // 将当前轮候选转换为紧凑卡片，帮助用户快速比较 C1/C2/C3。
  const strip = document.getElementById("job-candidate-strip");
  strip.replaceChildren();
  if (!candidates || !candidates.length) {
    const empty = document.createElement("span");
    empty.className = "candidate-empty";
    empty.textContent = "候选尚未生成";
    strip.appendChild(empty);
    return;
  }
  candidates.forEach((candidate) => {
    const card = document.createElement("div");
    const status = String(candidate.status || "等待评价");
    card.className = `candidate-card ${status === "已接受" ? "accepted" : status === "已回退" ? "rolled-back" : "pending"}`;
    const top = document.createElement("div");
    top.className = "candidate-card-top";
    const id = document.createElement("strong");
    id.textContent = candidate.candidate_id || "候选";
    const stateLabel = document.createElement("span");
    stateLabel.textContent = status;
    top.append(id, stateLabel);
    const score = document.createElement("b");
    score.textContent = candidate.score_pct == null ? "等待评价" : `${formatScore(candidate.score_pct)}%`;
    const metrics = document.createElement("small");
    metrics.textContent = candidate.failed_metric_count == null ? "等待 CarSim 结果" : `未通过 ${candidate.failed_metric_count} 项`;
    card.append(top, score, metrics);
    strip.appendChild(card);
  });
}

function renderJobOverview(job) {
  // 更新过程摘要、轮次进度、候选结果和当前最优值。
  const progress = job.progress || {};
  const status = job.status || "idle";
  const currentRound = Number(progress.current_round || 0);
  const maxRounds = Number(progress.max_rounds || 0);
  const completed = Number(progress.candidate_completed || 0);
  const total = Number(progress.candidate_total || 0);
  const roundPercent = maxRounds > 0 ? Math.min(100, ((Math.max(0, currentRound - 1) + (total ? completed / total : 0)) / maxRounds) * 100) : status === "completed" ? 100 : 0;
  setText("job-phase", progress.phase_label || (status === "completed" ? "优化完成" : "等待启动"));
  setText("job-round-label", maxRounds ? `第 ${currentRound || 0} / ${maxRounds} 轮` : "干运行");
  setText("job-round", maxRounds ? `${currentRound || 0} / ${maxRounds}` : "干运行");
  setText("job-candidates", total ? `${completed} / ${total}` : "尚未生成");
  setText("job-last-candidate", progress.last_candidate_id || "--");
  setText("job-last-score", progress.last_score_pct == null ? "--" : `${formatScore(progress.last_score_pct)}%`);
  setText("job-best-score", progress.best_score_pct == null ? "--" : `${formatScore(progress.best_score_pct)}%`);
  setText("job-no-improvement", `${Number(progress.no_improvement_rounds || 0)} 轮`);
  document.getElementById("job-progress-fill").style.width = `${roundPercent}%`;
  renderCandidateCards(progress.candidates || []);

  const overview = document.getElementById("job-overview");
  overview.replaceChildren();
  const summary = document.createElement("div");
  summary.className = "job-summary-line";
  summary.textContent = progress.last_decision ? `最近动作：${progress.last_candidate_id} ${progress.last_decision}` : (progress.phase_label || "等待启动");
  overview.appendChild(summary);
  const eventLines = (job.logs || []).filter((line) => line && !line.trim().startsWith("{")).slice(-5);
  eventLines.forEach((line) => {
    const event = document.createElement("div");
    event.className = "job-event";
    event.textContent = line;
    overview.appendChild(event);
  });
  if (job.error) {
    const error = document.createElement("div");
    error.className = "job-event error";
    error.textContent = job.error;
    overview.appendChild(error);
  }
}

function renderJob(job) {
  updateJobControls(job);
  const badge = document.getElementById("job-status-badge");
  badge.textContent = String(job.status || "idle").toUpperCase();
  badge.className = `job-badge ${job.status || ""}`;
  document.getElementById("job-log").textContent = job.logs && job.logs.length ? job.logs.join("\n") : "等待任务输出...";
  renderJobOverview(job);
}

function switchLogView(view) {
  const overview = document.getElementById("job-overview");
  const raw = document.getElementById("job-log");
  const showRaw = view === "raw";
  overview.hidden = showRaw;
  raw.hidden = !showRaw;
  document.querySelectorAll(".log-view-tab").forEach((button) => {
    const active = button.dataset.logView === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
}

async function pollJob() {
  try {
    const job = await requestJson("/api/job");
    const previousStatus = state.lastJobStatus;
    state.lastJobStatus = job.status;
    renderJob(job);
    const log = document.getElementById("job-log");
    log.scrollTop = log.scrollHeight;
    const configReady = ["matched", "legacy_matched"].includes(state.dashboard.agent.config_sync.status);
    updateJobControls(job);
    if (["running", "pausing", "paused", "stopping"].includes(job.status)) {
      window.clearTimeout(state.jobTimer);
      state.jobTimer = window.setTimeout(pollJob, 1000);
    } else if (job.status === "completed" && ["running", "pausing", "paused", "stopping"].includes(previousStatus)) {
      showToast("任务完成，正在刷新看板");
      await loadDashboard();
    } else if (job.status === "failed" && ["running", "pausing", "paused", "stopping"].includes(previousStatus)) {
      showToast(job.error || "任务失败，当前最优点未改变", true);
    } else if (job.status === "stopped" && ["running", "pausing", "paused", "stopping"].includes(previousStatus)) {
      showToast("任务已安全停止，已完成结果和当前最优点已保留");
      await loadDashboard();
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
  document.querySelectorAll('input[name="memory-mode"]').forEach((input) => input.addEventListener("change", updateRunButton));
  document.getElementById("start-job-button").addEventListener("click", startJob);
  document.getElementById("generate-baseline-button").addEventListener("click", generateFormalBaseline);
  document.getElementById("pause-job-button").addEventListener("click", togglePauseJob);
  document.getElementById("stop-job-button").addEventListener("click", stopJob);
  document.getElementById("open-config-button").addEventListener("click", openConfig);
  document.getElementById("open-config-shortcut").addEventListener("click", () => switchView("settings"));
  document.getElementById("check-config-button").addEventListener("click", checkConfig);
  document.getElementById("copy-config-path").addEventListener("click", copyConfigPath);
  document.getElementById("run-admission-button").addEventListener("click", runAdmission);
  document.getElementById("cleanup-history-button").addEventListener("click", cleanupHistory);
  document.querySelectorAll(".log-view-tab").forEach((button) => button.addEventListener("click", () => switchLogView(button.dataset.logView)));
  window.addEventListener("resize", () => { if (state.activeView === "dashboard" && state.dashboard) drawScoreChart(state.dashboard); });
}

document.addEventListener("DOMContentLoaded", async () => {
  if (window.lucide) window.lucide.createIcons();
  bindInteractions();
  await loadDashboard();
  await loadOperationalData();
  updateRunButton();
  await pollJob();
});
