const form = document.querySelector("#run-form");
const fileInput = document.querySelector("#data-file");
const fileLabel = document.querySelector("#file-label");
const dropZone = document.querySelector("#drop-zone");
const submitButton = document.querySelector("#submit-button");
const formError = document.querySelector("#form-error");
const eventLog = document.querySelector("#event-log");
const statusBadge = document.querySelector("#status-badge");
const runIdLabel = document.querySelector("#run-id");
const progressBar = document.querySelector("#progress-bar");
const results = document.querySelector("#results");

let eventSource = null;
let activeRunId = null;

const stageOrder = ["split", "clean", "eda", "feature", "model", "bat"];

function stageGroup(stage = "") {
  const value = stage.toLowerCase();
  if (value.includes("split") || value.includes("profile") || value.includes("plan") || value === "initializing") return "split";
  if (value.includes("clean") || value.includes("dataset_loaded")) return "clean";
  if (value.includes("eda")) return "eda";
  if (value.includes("feature") || value.includes("target_encode")) return "feature";
  if (value.includes("model")) return "model";
  if (value.includes("bat") || value.includes("business") || value === "completed") return "bat";
  return "";
}

function setStage(stage) {
  const group = stageGroup(stage);
  if (!group) return;
  const current = stageOrder.indexOf(group);
  document.querySelectorAll("#stage-grid [data-key]").forEach((item, index) => {
    item.classList.toggle("done", index < current);
    item.classList.toggle("active", index === current);
  });
  progressBar.style.width = `${Math.max(8, ((current + 1) / stageOrder.length) * 100)}%`;
}

function setStatus(status) {
  statusBadge.textContent = status.toUpperCase();
  statusBadge.className = `status-badge ${status}`;
}

function appendEvent(event) {
  const empty = eventLog.querySelector(".empty-state");
  if (empty) empty.remove();
  const row = document.createElement("div");
  row.className = "event";
  const time = document.createElement("time");
  time.textContent = new Date(event.created_at).toLocaleTimeString("zh-CN", { hour12: false });
  const stage = document.createElement("code");
  stage.textContent = event.stage || event.type;
  const message = document.createElement("span");
  message.textContent = event.message || event.type;
  row.append(time, stage, message);
  eventLog.append(row);
  eventLog.scrollTop = eventLog.scrollHeight;
  setStage(event.stage);
}

function formatValue(value) {
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(4);
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function addMetric(label, value) {
  const card = document.createElement("div");
  card.className = "metric";
  const name = document.createElement("span");
  name.textContent = label;
  const number = document.createElement("strong");
  number.textContent = formatValue(value);
  card.append(name, number);
  document.querySelector("#metric-grid").append(card);
}

function renderReport(markdown) {
  const root = document.querySelector("#report-content");
  root.replaceChildren();
  if (!markdown) {
    root.textContent = "本次运行未生成业务报告。";
    return;
  }
  let paragraph = [];
  const flush = () => {
    if (!paragraph.length) return;
    const p = document.createElement("p");
    p.textContent = paragraph.join("\n");
    root.append(p);
    paragraph = [];
  };
  markdown.split("\n").forEach((line) => {
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) {
      flush();
      const h = document.createElement(`h${heading[1].length}`);
      h.textContent = heading[2].replaceAll("**", "");
      root.append(h);
    } else if (!line.trim()) {
      flush();
    } else {
      paragraph.push(line.replace(/^[-*]\s+/, "• ").replaceAll("**", ""));
    }
  });
  flush();
}

function artifactUrl(runId, path) {
  return `/api/runs/${encodeURIComponent(runId)}/artifacts/${path.split("/").map(encodeURIComponent).join("/")}`;
}

async function loadResult(runId) {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/result`);
  if (!response.ok) throw new Error("无法读取分析结果");
  const data = await response.json();
  const metricGrid = document.querySelector("#metric-grid");
  metricGrid.replaceChildren();
  const best = data.best_model || {};
  addMetric("Best model", best.model_name);
  Object.entries(best.final_test_metrics || best.metrics || {}).slice(0, 2).forEach(([key, value]) => addMetric(key, value));
  addMetric("Total tokens", (data.token_usage || {}).total);
  renderReport(data.business_report);

  const artifactList = document.querySelector("#artifact-list");
  artifactList.replaceChildren();
  (data.artifacts || []).filter((item) => ["md", "json", "csv", "pkl"].includes(item.kind)).slice(0, 18).forEach((item) => {
    const link = document.createElement("a");
    link.className = "artifact";
    link.href = artifactUrl(runId, item.path);
    link.textContent = item.path;
    const size = document.createElement("small");
    size.textContent = `${Math.max(1, Math.round(item.size / 1024))} KB`;
    link.append(size);
    artifactList.append(link);
  });

  const chartGrid = document.querySelector("#chart-grid");
  chartGrid.replaceChildren();
  (data.charts || []).slice(0, 9).forEach((item) => {
    const figure = document.createElement("figure");
    const image = document.createElement("img");
    image.src = artifactUrl(runId, item.path);
    image.alt = item.path;
    image.loading = "lazy";
    const caption = document.createElement("figcaption");
    caption.textContent = item.path;
    figure.append(image, caption);
    chartGrid.append(figure);
  });
  results.hidden = false;
  results.scrollIntoView({ behavior: "smooth", block: "start" });
}

function watchRun(runId) {
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  eventSource.onmessage = async (message) => {
    const event = JSON.parse(message.data);
    appendEvent(event);
    if (["running", "pipeline_started", "stage"].includes(event.type)) setStatus("running");
    if (event.type === "succeeded") {
      setStatus("succeeded");
      progressBar.style.width = "100%";
      eventSource.close();
      submitButton.disabled = false;
      await loadResult(runId);
    }
    if (event.type === "failed") {
      setStatus("failed");
      formError.textContent = event.message;
      eventSource.close();
      submitButton.disabled = false;
    }
  };
  eventSource.onerror = () => {
    if (eventSource.readyState === EventSource.CLOSED) return;
    formError.textContent = "进度连接暂时中断，浏览器将自动重连。";
  };
}

fileInput.addEventListener("change", () => {
  fileLabel.textContent = fileInput.files[0]?.name || "选择或拖入 CSV 数据";
});
["dragenter", "dragover"].forEach((name) => dropZone.addEventListener(name, (event) => {
  event.preventDefault();
  dropZone.classList.add("dragging");
}));
["dragleave", "drop"].forEach((name) => dropZone.addEventListener(name, (event) => {
  event.preventDefault();
  dropZone.classList.remove("dragging");
}));
dropZone.addEventListener("drop", (event) => {
  if (!event.dataTransfer.files.length) return;
  fileInput.files = event.dataTransfer.files;
  fileLabel.textContent = fileInput.files[0].name;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.textContent = "";
  results.hidden = true;
  eventLog.innerHTML = "";
  submitButton.disabled = true;
  setStatus("running");
  progressBar.style.width = "3%";
  try {
    const response = await fetch("/api/runs", { method: "POST", body: new FormData(form) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "任务创建失败");
    activeRunId = data.run_id;
    runIdLabel.textContent = `${data.run_id} · queue ${data.queue_position}`;
    watchRun(activeRunId);
  } catch (error) {
    setStatus("failed");
    formError.textContent = error.message;
    submitButton.disabled = false;
  }
});
