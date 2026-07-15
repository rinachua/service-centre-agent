function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value ?? "");
  return div.innerHTML;
}

function renderTable(containerId, rows, columns, emptyMessage) {
  const container = document.getElementById(containerId);
  if (rows.length === 0) {
    container.innerHTML = `<p class="table-empty">${emptyMessage}</p>`;
    return;
  }
  const head = columns.map((c) => `<th>${escapeHtml(c.label)}</th>`).join("");
  const body = rows
    .map((row) => `<tr>${columns.map((c) => `<td>${c.render(row)}</td>`).join("")}</tr>`)
    .join("");
  container.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderError(containerId, err) {
  document.getElementById(containerId).innerHTML =
    `<p class="table-error">Could not load: ${escapeHtml(err.message)}</p>`;
}

function severityBadge(value) {
  const cls = { critical: "badge-critical", high: "badge-high", medium: "badge-medium", low: "badge-low" }[value] || "badge-low";
  return `<span class="badge ${cls}">${escapeHtml(value)}</span>`;
}

function assetStatusBadge(value) {
  // Asset status ("Running"/"Idle"/"Down") is a different vocabulary from ticket
  // severity — mapping it through severityBadge's default ("low"/green) would render
  // "Down" as green, the opposite of what an ops dashboard should show.
  const cls = { Down: "badge-critical", Idle: "badge-medium", Running: "badge-low" }[value] || "badge-medium";
  return `<span class="badge ${cls}">${escapeHtml(value)}</span>`;
}

async function loadPriority() {
  try {
    const resp = await fetch("/dashboard/priority");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const rows = await resp.json();
    renderTable(
      "priority-table",
      rows,
      [
        { label: "Ticket", render: (r) => escapeHtml(r.ticket_id) },
        { label: "Score", render: (r) => Number(r.score).toFixed(3) },
        { label: "Recurrence count", render: (r) => escapeHtml(r.recurrence_count ?? "-") },
      ],
      "No open tickets to prioritise.",
    );
  } catch (err) {
    renderError("priority-table", err);
  }
}

async function loadAssets() {
  try {
    const resp = await fetch("/dashboard/assets");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const rows = await resp.json();
    renderTable(
      "assets-table",
      rows,
      [
        { label: "Tool", render: (r) => escapeHtml(r.tool_id) },
        { label: "Line", render: (r) => escapeHtml(r.line ?? "-") },
        { label: "Process area", render: (r) => escapeHtml(r.process_area ?? "-") },
        { label: "Status", render: (r) => assetStatusBadge(r.status) },
        { label: "Downtime (7d, hrs)", render: (r) => escapeHtml(r.recent_downtime_hours_7d ?? "-") },
      ],
      "No tools found.",
    );
  } catch (err) {
    renderError("assets-table", err);
  }
}

async function loadAudit() {
  try {
    const resp = await fetch("/audit?limit=20");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const rows = await resp.json();
    renderTable(
      "audit-table",
      rows,
      [
        { label: "Request", render: (r) => escapeHtml(r.request_id) },
        { label: "Query", render: (r) => escapeHtml(r.user_query) },
        { label: "Confidence", render: (r) => (r.confidence ? severityBadge(r.confidence) : "-") },
        {
          label: "Flags",
          render: (r) =>
            (r.had_injection_flags ? '<span class="badge badge-flag">injection</span>' : "") +
            (r.had_schema_validation_failures ? '<span class="badge badge-flag">schema</span>' : ""),
        },
        { label: "When", render: (r) => escapeHtml(r.created_at) },
      ],
      "No requests logged yet.",
    );
  } catch (err) {
    renderError("audit-table", err);
  }
}

loadPriority();
loadAssets();
loadAudit();
