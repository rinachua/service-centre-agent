const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("query-input");

formEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = inputEl.value.trim();
  if (!query) return;
  appendUserMessage(query);
  inputEl.value = "";
  inputEl.disabled = true;

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    if (!response.ok) {
      appendError(`Request failed (${response.status})`);
      return;
    }
    const data = await response.json();
    appendAnswer(data.request_id, data.answer);
  } catch (err) {
    appendError(`Network error: ${err.message}`);
  } finally {
    inputEl.disabled = false;
    inputEl.focus();
  }
});

function appendUserMessage(text) {
  const el = document.createElement("div");
  el.className = "message user";
  el.textContent = text;
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendError(text) {
  const el = document.createElement("div");
  el.className = "message error";
  el.textContent = text;
  messagesEl.appendChild(el);
}

function appendAnswer(requestId, answer) {
  const el = document.createElement("div");
  el.className = "message answer";

  const rec = document.createElement("p");
  rec.className = "recommendation";
  rec.textContent = answer.recommendation;
  el.appendChild(rec);

  const badge = document.createElement("span");
  badge.className = `confidence confidence-${answer.confidence}`;
  badge.textContent = `confidence: ${answer.confidence}`;
  el.appendChild(badge);

  if (answer.evidence && answer.evidence.length) {
    const evidenceTitle = document.createElement("h4");
    evidenceTitle.textContent = "Evidence";
    el.appendChild(evidenceTitle);
    const list = document.createElement("ul");
    answer.evidence.forEach((item) => {
      const li = document.createElement("li");
      const verifiedTag = item.verified ? "" : " (unverified)";
      li.textContent = `[${item.source} / ${item.record_id}] ${item.detail}${verifiedTag}`;
      if (!item.verified) li.className = "unverified";
      list.appendChild(li);
    });
    el.appendChild(list);
  }

  if (answer.assumptions && answer.assumptions.length) {
    const assumptionsTitle = document.createElement("h4");
    assumptionsTitle.textContent = "Assumptions";
    el.appendChild(assumptionsTitle);
    const list = document.createElement("ul");
    answer.assumptions.forEach((a) => {
      const li = document.createElement("li");
      li.textContent = a;
      list.appendChild(li);
    });
    el.appendChild(list);
  }

  const nextAction = document.createElement("p");
  nextAction.innerHTML = `<strong>Next action:</strong> ${answer.next_action}`;
  el.appendChild(nextAction);

  if (answer.followup_note) {
    const note = answer.followup_note;
    const noteBox = document.createElement("div");
    noteBox.className = "followup-note";
    noteBox.innerHTML = `
      <h4>Draft follow-up note for ${note.ticket_id}</h4>
      <p><strong>Summary:</strong> ${note.summary}</p>
      <p><strong>Root cause:</strong> ${note.root_cause}</p>
      <p><strong>Next action:</strong> ${note.next_action}</p>
      <button class="save-followup">Save follow-up to ticket</button>
    `;
    noteBox.querySelector(".save-followup").addEventListener("click", async () => {
      const resp = await fetch(`/tickets/${note.ticket_id}/followups`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          summary: note.summary,
          root_cause: note.root_cause,
          next_action: note.next_action,
        }),
      });
      if (resp.ok) {
        noteBox.querySelector(".save-followup").textContent = "Saved";
        noteBox.querySelector(".save-followup").disabled = true;
      } else {
        appendError("Failed to save follow-up note.");
      }
    });
    el.appendChild(noteBox);
  }

  const traceLink = document.createElement("a");
  traceLink.href = `/audit/${requestId}`;
  traceLink.target = "_blank";
  traceLink.textContent = "Show evidence trace (raw audit log)";
  traceLink.className = "trace-link";
  el.appendChild(traceLink);

  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}
