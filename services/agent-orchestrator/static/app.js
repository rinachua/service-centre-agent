const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("query-input");
const sendButtonEl = document.getElementById("send-button");

formEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = inputEl.value.trim();
  if (!query) return;
  appendUserMessage(query);
  inputEl.value = "";
  inputEl.disabled = true;
  sendButtonEl.disabled = true;

  // A /chat call makes 1-3 sequential Claude calls (plan, synthesis, and an optional
  // revision round) and can take anywhere from a few seconds to 30-50+ seconds
  // depending on how much the model has to generate and whether a revision fires —
  // a live elapsed-seconds counter, not just a static "Loading...", is what makes a
  // long wait read as "still working" instead of "looks frozen, did something break?"
  const loading = appendLoadingMessage();
  const startedAt = performance.now();
  const tickInterval = setInterval(() => {
    const elapsedSeconds = Math.floor((performance.now() - startedAt) / 1000);
    loading.timerEl.textContent = `${elapsedSeconds}s`;
  }, 1000);

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    clearInterval(tickInterval);
    loading.el.remove();
    if (!response.ok) {
      appendError(`Request failed (${response.status})`);
      return;
    }
    const data = await response.json();
    appendAnswer(data.request_id, data.answer);
  } catch (err) {
    clearInterval(tickInterval);
    loading.el.remove();
    appendError(`Network error: ${err.message}`);
  } finally {
    inputEl.disabled = false;
    sendButtonEl.disabled = false;
    inputEl.focus();
  }
});

function appendLoadingMessage() {
  const el = document.createElement("div");
  el.className = "message loading";

  const dots = document.createElement("span");
  dots.className = "loading-dots";
  dots.textContent = "Thinking";
  el.appendChild(dots);

  const timerEl = document.createElement("span");
  timerEl.className = "loading-timer";
  timerEl.textContent = "0s";
  el.appendChild(timerEl);

  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return { el, timerEl };
}

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
  // Regression fix: unlike appendUserMessage/appendAnswer, this was missing the
  // scroll-into-view — an error appended below the currently-visible area (e.g. from
  // clicking "Save follow-up" deep inside an already-scrolled answer) rendered
  // successfully but off-screen, which looked exactly like "clicked the button and
  // nothing happened" even though an error message was actually there.
  messagesEl.scrollTop = messagesEl.scrollHeight;
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
  const nextActionLabel = document.createElement("strong");
  nextActionLabel.textContent = "Next action:";
  nextAction.appendChild(nextActionLabel);
  nextAction.appendChild(document.createTextNode(` ${answer.next_action}`));
  el.appendChild(nextAction);

  if (answer.followup_note) {
    const note = answer.followup_note;
    const noteBox = document.createElement("div");
    noteBox.className = "followup-note";

    const noteTitle = document.createElement("h4");
    noteTitle.textContent = `Draft follow-up note for ${note.ticket_id}`;
    noteBox.appendChild(noteTitle);

    const fields = [
      ["Summary:", note.summary],
      ["Root cause:", note.root_cause],
      ["Next action:", note.next_action],
    ];
    fields.forEach(([label, value]) => {
      const p = document.createElement("p");
      const strong = document.createElement("strong");
      strong.textContent = label;
      p.appendChild(strong);
      p.appendChild(document.createTextNode(` ${value}`));
      noteBox.appendChild(p);
    });

    const saveButton = document.createElement("button");
    saveButton.className = "save-followup";
    saveButton.textContent = "Save follow-up to ticket";
    noteBox.appendChild(saveButton);

    saveButton.addEventListener("click", async () => {
      // Regression fix: this had no try/catch at all — a fetch()-level failure
      // (network drop, or a URL the browser can't send, e.g. a ticket_id containing
      // "/" like "TCK-001 / TCK-002" when the synthesiser combines two tickets into
      // one draft) threw an unhandled rejection with zero UI feedback: the button
      // just silently did nothing. A non-OK HTTP response (e.g. 404 for a ticket_id
      // that doesn't exist) was already handled below, but a rejected fetch() itself
      // never reached that check.
      saveButton.disabled = true;
      try {
        const resp = await fetch(`/tickets/${encodeURIComponent(note.ticket_id)}/followups`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            summary: note.summary,
            root_cause: note.root_cause,
            next_action: note.next_action,
          }),
        });
        if (resp.ok) {
          saveButton.textContent = "Saved";
        } else {
          let detail = `HTTP ${resp.status}`;
          try {
            const body = await resp.json();
            if (body && body.detail) detail = body.detail;
          } catch {
            // Response body wasn't JSON — fall back to the HTTP status above.
          }
          appendError(`Failed to save follow-up note for ${note.ticket_id}: ${detail}`);
          saveButton.disabled = false;
        }
      } catch (err) {
        appendError(`Failed to save follow-up note for ${note.ticket_id}: ${err.message}`);
        saveButton.disabled = false;
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
