"use strict";
(() => {
  // addon/frontend/monitor.ts
  var message = document.getElementById("message");
  async function action(action2, extra = {}) {
    const response = await fetch(location.pathname, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: action2, ...extra }) });
    if (!response.ok) throw new Error(`\u64CD\u4F5C\u5931\u8D25 (${response.status})`);
    message.textContent = "\u64CD\u4F5C\u5DF2\u63D0\u4EA4";
    await refresh();
  }
  async function loadHistory(studentId) {
    const response = await fetch(`${location.pathname}?history=${studentId}`);
    if (!response.ok) throw new Error("\u65E0\u6CD5\u8BFB\u53D6\u56FE\u7247");
    const { rows } = await response.json();
    const host = document.getElementById("monitor-history");
    host.replaceChildren();
    for (const row of rows) {
      const details = document.createElement("details");
      const title = document.createElement("summary");
      title.textContent = new Date(row.completedAt).toLocaleString();
      details.append(title);
      details.addEventListener("toggle", () => {
        if (details.open && !details.querySelector("img")) {
          const image = document.createElement("img");
          image.src = `${location.pathname}?image=${encodeURIComponent(row._id)}`;
          image.style.maxWidth = "100%";
          details.append(image);
        }
      });
      host.append(details);
    }
  }
  async function refresh() {
    const response = await fetch(`${location.pathname}?status=1`);
    if (!response.ok) throw new Error("\u72B6\u6001\u83B7\u53D6\u5931\u8D25");
    const data = await response.json();
    const host = document.getElementById("monitor-list");
    host.replaceChildren();
    const labels = { UNREACHABLE: "\u8BBE\u5907\u4E0D\u53EF\u8FBE", TLS_FAILED: "\u8BC1\u4E66\u6821\u9A8C\u5931\u8D25", AUTH_FAILED: "\u8BA4\u8BC1\u5931\u8D25", FRAME_UNAVAILABLE: "\u622A\u56FE\u4E0D\u53EF\u7528", TIMEOUT: "\u4EFB\u52A1\u8D85\u65F6", IMAGE_TOO_LARGE: "\u56FE\u7247\u8FC7\u5927", CANCELLED: "\u5DF2\u53D6\u6D88", INVALID_JPEG: "\u56FE\u7247\u683C\u5F0F\u9519\u8BEF", CAPTURE_FAILED: "\u91C7\u96C6\u5931\u8D25" };
    if (data.active && (!data.collectorSeenAt || Date.now() - data.collectorSeenAt > 1e4)) message.textContent = "\u91C7\u96C6\u8FDB\u7A0B\u672A\u66F4\u65B0\u72B6\u6001\uFF0C\u8BF7\u68C0\u67E5\u670D\u52A1";
    for (const mapping of data.mappings) {
      const row = data.rows.find((r) => r._id === mapping.studentId)?.latest;
      const runtime = data.runtime.find((r) => r.studentId === mapping.studentId);
      const success = data.successes.find((r) => r._id === mapping.studentId);
      const line = document.createElement("p");
      line.textContent = `\u5B66\u751F ${mapping.studentId} \xB7 ${mapping.deviceId} \xB7 ${row ? (labels[row.error] ?? row.error ?? "\u622A\u56FE\u6210\u529F") + " \xB7 " + new Date(row.completedAt).toLocaleTimeString() : "\u7B49\u5F85\u9996\u6B21\u68C0\u6D4B"} \xB7 ${runtime?.pending ? "\u6392\u961F " + Math.round(runtime.queueDelayMs / 1e3) + "\u79D2" : runtime?.running ? "\u6B63\u5728\u91C7\u96C6" : "\u7B49\u5F85\u4E0B\u6B21\u68C0\u6D4B"} \xB7 \u6700\u8FD1\u6210\u529F ${success ? new Date(success.lastSuccessAt).toLocaleTimeString() : "\u6682\u65E0"}`;
      for (const [label, fn] of [["\u62BD\u67E5", () => action("capture", { studentId: mapping.studentId })], ["\u5386\u53F2\u56FE\u7247", () => loadHistory(mapping.studentId)]]) {
        const button = document.createElement("button");
        button.textContent = label;
        button.onclick = () => void fn().catch(showError);
        line.append(button);
      }
      host.append(line);
    }
  }
  function showError(e) {
    message.textContent = e.message;
  }
  document.querySelectorAll("[data-action]").forEach((button) => button.onclick = () => void action(button.dataset.action, button.dataset.action === "import" ? { mappings: document.getElementById("mapping").value } : {}).catch(showError));
  void refresh().catch(showError);
  setInterval(() => void refresh().catch(showError), 15e3);
})();
