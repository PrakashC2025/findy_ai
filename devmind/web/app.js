/* DevMind web app - no build step, no dependencies. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    token: localStorage.getItem("devmind_token") || "",
    repo: localStorage.getItem("devmind_repo") || "",
    streaming: false,
  };

  // ------------------------------------------------------------------ auth
  const params = new URLSearchParams(location.search);
  if (params.get("token")) {
    state.token = params.get("token");
    localStorage.setItem("devmind_token", state.token);
    params.delete("token");
    const qs = params.toString();
    history.replaceState({}, "", location.pathname + (qs ? "?" + qs : ""));
  }

  async function api(path, options = {}) {
    const headers = Object.assign(
      { "content-type": "application/json" },
      state.token ? { "x-devmind-token": state.token } : {},
      options.headers || {}
    );
    const response = await fetch(path, Object.assign({}, options, { headers }));
    if (response.status === 401) {
      if (!sessionStorage.getItem("devmind_asked_token")) {
        sessionStorage.setItem("devmind_asked_token", "1");
        const entered = window.prompt("This DevMind has a token. Paste it (printed by `devmind serve`):");
        if (entered) {
          state.token = entered.trim();
          localStorage.setItem("devmind_token", state.token);
          location.reload();
        }
      }
      throw new Error("unauthorized: paste the token from the terminal (or reopen with ?token=…)");
    }
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = body.detail || JSON.stringify(body);
      } catch (_) {}
      throw new Error(detail);
    }
    return response;
  }

  const escapeHtml = (text) =>
    text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function renderInto(el, text) {
    const parts = String(text).split(/```/);
    el.innerHTML = parts
      .map((part, index) => {
        if (index % 2 === 1) {
          const body = part.replace(/^[A-Za-z0-9_+-]*\n/, "");
          return "<pre><code>" + escapeHtml(body) + "</code></pre>";
        }
        return escapeHtml(part).replace(/`([^`\n]+)`/g, "<code>$1</code>");
      })
      .join("");
  }

  function paintDiff(el, diff) {
    el.innerHTML = diff
      .split("\n")
      .map((line) => {
        const safe = escapeHtml(line);
        if (line.startsWith("+") && !line.startsWith("+++")) return '<span class="add">' + safe + "</span>";
        if (line.startsWith("-") && !line.startsWith("---")) return '<span class="del">' + safe + "</span>";
        return safe;
      })
      .join("\n");
  }

  // ----------------------------------------------------------------- status
  async function loadStatus() {
    const pill = $("status-pill");
    try {
      const response = await api("/api/status");
      const data = await response.json();
      const model = data.demo ? "demo (no model)" : data.model || data.backend;
      pill.textContent = `${model} · ${data.counts.repos} project(s)`;
      pill.className = "pill " + (data.demo ? "bad" : "ok");
      pill.title = `backend ${data.backend} · cache ${data.counts.cache.entries} · sandbox: ${data.sandbox.join(" · ")}`;
    } catch (error) {
      pill.textContent = "offline";
      pill.className = "pill bad";
      pill.title = String(error.message || error);
    }
  }

  // ------------------------------------------------------------------ repos
  async function loadRepos(select) {
    const response = await api("/api/repos");
    const repos = await response.json();
    const dropdown = $("repo-select");
    dropdown.innerHTML = '<option value="">no project</option>';
    for (const entry of repos) {
      const option = document.createElement("option");
      option.value = entry.repo;
      option.textContent = `${entry.repo} (${entry.files} files)`;
      dropdown.appendChild(option);
    }
    const wanted = select || state.repo;
    if (wanted && repos.some((entry) => entry.repo === wanted)) {
      dropdown.value = wanted;
      state.repo = wanted;
    } else {
      state.repo = "";
      localStorage.removeItem("devmind_repo");
    }
    if (repos.length && !state.repo) {
      dropdown.value = repos[0].repo;
      state.repo = repos[0].repo;
      localStorage.setItem("devmind_repo", state.repo);
    }
    return repos;
  }

  async function openFolder() {
    const path = $("path-input").value.trim();
    if (!path) return;
    const button = $("open-btn");
    button.disabled = true;
    button.textContent = "indexing…";
    try {
      const response = await api("/api/index", {
        method: "POST",
        body: JSON.stringify({ path }),
      });
      const report = await response.json();
      $("path-input").value = "";
      await loadRepos(report.repo);
      await loadStatus();
      $("files-hint").textContent = `${report.repo}: ${report.indexed} files · ${report.chunks} chunks · ${report.symbols} symbols`;
    } catch (error) {
      $("files-hint").textContent = String(error.message || error);
    } finally {
      button.disabled = false;
      button.textContent = "Open folder";
    }
  }

  async function cloneRepo() {
    const url = $("git-input").value.trim();
    if (!url) return;
    const button = $("clone-btn");
    button.disabled = true;
    button.textContent = "cloning…";
    try {
      const response = await api("/api/clone", { method: "POST", body: JSON.stringify({ url }) });
      const data = await response.json();
      $("git-input").value = "";
      await loadRepos(data.repo);
      await loadStatus();
      $("files-hint").textContent = data.already
        ? `${data.repo} is already cloned at ${data.root}`
        : data.summary || `${data.repo}: ${data.indexed} files`;
    } catch (error) {
      $("files-hint").textContent = String(error.message || error);
    } finally {
      button.disabled = false;
      button.textContent = "Clone";
    }
  }

  // -------------------------------------------------------------------- ask
  function addMessage(role, meta) {
    const article = document.createElement("article");
    article.className = "msg " + role;
    const metaEl = document.createElement("div");
    metaEl.className = "meta";
    metaEl.textContent = meta;
    const body = document.createElement("div");
    body.className = "body";
    article.append(metaEl, body);
    $("messages").appendChild(article);
    article.scrollIntoView({ block: "end", behavior: "smooth" });
    return { article, metaEl, body };
  }

  function confidenceBadge(target, confidence, reasons) {
    if (!confidence) return;
    const wrap = document.createElement("div");
    wrap.className = "conf " + confidence.label;
    wrap.innerHTML = '<span class="dot"></span>';
    const text = document.createElement("span");
    text.textContent = `${confidence.label} ${confidence.score.toFixed(2)}`;
    wrap.appendChild(text);
    wrap.title = (reasons || []).join(" · ");
    target.appendChild(wrap);
  }

  function evidenceBlock(target, payload) {
    const details = document.createElement("details");
    details.className = "evidence";
    details.innerHTML = "<summary>evidence</summary>";
    const list = document.createElement("ul");
    const add = (text) => {
      const item = document.createElement("li");
      item.textContent = text;
      list.appendChild(item);
    };
    (payload.code_hits || []).forEach((hit) =>
      add(`code ${hit.location} ${hit.symbol || ""}`.trim())
    );
    (payload.citations || []).forEach((citation) => add(`${citation.id} ${citation.kind} ${citation.text}`.slice(0, 160)));
    if (payload.difficulty) add(`difficulty ${payload.difficulty.level} (${payload.difficulty.score}/100)`);
    if (payload.timings) add(`first token ${payload.timings.first_token_ms} ms · total ${payload.timings.total_ms} ms`);
    if (!list.children.length) return;
    details.appendChild(list);
    target.appendChild(details);
  }

  async function streamAnswer(prompt) {
    if (state.streaming) return;
    state.streaming = true;
    $("send-btn").disabled = true;
    $("welcome")?.remove();
    addMessage("user", "you");
    const answer = addMessage("assistant", "thinking…");
    let buffer = "";
    let metaEvent = null;

    const paint = () => {
      renderInto(answer.body, buffer);
    };

    try {
      const response = await api("/api/ask", {
        method: "POST",
        body: JSON.stringify({ prompt, repo: state.repo, deep: true }),
      });
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        const blocks = pending.split("\n\n");
        pending = blocks.pop() ||"";
        for (const block of blocks) {
          const line = block.split("\n").find((row) => row.startsWith("data: "));
          if (!line) continue;
          const event = JSON.parse(line.slice(6));
          if (event.event === "meta") {
            metaEvent = event;
            const bits = [event.model, `difficulty ${event.difficulty.level}`];
            if (event.packs?.length) bits.push(`lang ${event.packs.join("+")}`);
            if (event.code_hits?.length) bits.push(`${event.code_hits.length} code hits`);
            answer.metaEl.textContent = bits.join(" · ");
          } else if (event.event === "token") {
            buffer += event.text;
            paint();
          } else if (event.event === "replace") {
            buffer = event.text;
            paint();
            answer.metaEl.textContent += ` · upgraded to ${event.model}`;
            if (event.judge_note) answer.metaEl.title = event.judge_note;
          } else if (event.event === "done") {
            if (event.answer && event.answer !== buffer) {
              buffer = event.answer;
              paint();
            }
            confidenceBadge(answer.article, event.confidence);
            evidenceBlock(answer.article, Object.assign({}, metaEvent || {}, event));
            const timing = event.timings
              ? ` · ${event.timings.total_ms} ms${event.cached ? " · cached" : ""}`
              : "";
            answer.metaEl.textContent = `${event.model}${timing}`;
          } else if (event.event === "error") {
            const box = document.createElement("div");
            box.className = "err";
            box.textContent = event.message;
            answer.article.appendChild(box);
          }
        }
      }
    } catch (error) {
      const box = document.createElement("div");
      box.className = "err";
      box.textContent = String(error.message || error);
      answer.article.appendChild(box);
    } finally {
      state.streaming = false;
      $("send-btn").disabled = false;
      answer.article.scrollIntoView({ block: "end", behavior: "smooth" });
    }
  }

  // ------------------------------------------------------------------ brief
  async function loadBrief() {
    try {
      const response = await api("/api/brief");
      const data = await response.json();
      const notes = $("brief-notes");
      notes.innerHTML = "";
      for (const note of data.notes || []) {
        const row = document.createElement("div");
        row.textContent = "• " + note;
        notes.appendChild(row);
      }
    } catch (error) {
      $("brief-hint").textContent = String(error.message || error);
    }
  }

  async function saveBrief(clear) {
    const text = $("brief-text").value.trim();
    if (!clear && !text) {
      $("brief-hint").textContent = "write something first";
      return;
    }
    try {
      const response = await api("/api/brief", {
        method: "POST",
        body: JSON.stringify(clear ? { clear: true } : { text }),
      });
      const data = await response.json();
      $("brief-hint").textContent = clear ? "cleared" : "saved";
      if (!clear) $("brief-text").value = "";
      await loadBrief();
      void data;
    } catch (error) {
      $("brief-hint").textContent = String(error.message || error);
    }
  }

  // ---------------------------------------------------------------- analyze
  async function listFiles() {
    if (!state.repo) {
      $("files-hint").textContent = "pick or open a project first";
      return;
    }
    const response = await api(`/api/files?repo=${encodeURIComponent(state.repo)}`);
    const files = await response.json();
    const list = $("file-list");
    list.innerHTML = "";
    $("files-hint").textContent = `${files.length} indexed files`;
    for (const path of files.slice(0, 800)) {
      const button = document.createElement("button");
      button.textContent = path;
      button.onclick = () => previewFile(path);
      list.appendChild(button);
    }
  }

  async function previewFile(path) {
    const preview = $("file-preview");
    preview.hidden = false;
    preview.textContent = "loading…";
    try {
      const response = await api(
        `/api/file?repo=${encodeURIComponent(state.repo)}&path=${encodeURIComponent(path)}`
      );
      const data = await response.json();
      preview.textContent = data.text;
    } catch (error) {
      preview.textContent = String(error.message || error);
    }
  }

  async function runChecks() {
    if (!state.repo) {
      $("checks-hint").textContent = "pick a project first";
      return;
    }
    const out = $("checks-out");
    out.innerHTML = '<span class="spinner"></span> running checks…';
    try {
      const response = await api(`/api/checks?repo=${encodeURIComponent(state.repo)}&run=true`);
      const data = await response.json();
      out.innerHTML = "";
      for (const check of data.checks) {
        const row = document.createElement("div");
        row.className = "check";
        const skipped = check.skipped;
        row.innerHTML =
          `<span class="mark ${check.ok ? "ok" : "bad"}">${skipped ? "–" : check.ok ? "✓" : "✗"}</span>` +
          `<span>${check.name} <code>${(check.command || []).join(" ")}</code></span>`;
        out.appendChild(row);
        if (!check.ok && check.output) {
          const pre = document.createElement("pre");
          pre.className = "diff";
          pre.textContent = check.output.slice(-2500);
          out.appendChild(pre);
        }
      }
      if (!data.checks.length) out.innerHTML = '<p class="hint">no test/lint command detected in this project</p>';
      for (const finding of (data.security || []).slice(0, 25)) {
        const row = document.createElement("div");
        row.className = "finding";
        row.innerHTML =
          `<span class="sev ${finding.severity}">${finding.severity}</span> ` +
          `<code>${finding.path}:${finding.line}</code> ${escapeHtml(finding.message)}`;
        out.appendChild(row);
      }
      const summary = document.createElement("p");
      summary.className = "hint";
      summary.textContent = `${data.summary} · ${data.security_summary}`;
      out.appendChild(summary);
    } catch (error) {
      out.innerHTML = `<p class="err">${escapeHtml(String(error.message || error))}</p>`;
    }
  }

  // -------------------------------------------------------------------- fix
  async function proposeFix(apply) {
    if (!state.repo) {
      $("fix-out").innerHTML = '<p class="err">pick a project first</p>';
      return;
    }
    const problem = $("fix-problem").value.trim();
    if (!problem) {
      $("fix-out").innerHTML = '<p class="err">describe the problem first</p>';
      return;
    }
    const rounds = Math.max(1, Math.min(5, Number($("fix-rounds").value) || 3));
    const out = $("fix-out");
    out.innerHTML = '<span class="spinner"></span> asking for a patch, then verifying it on a copy…';
    $("fix-btn").disabled = true;
    $("fix-apply-btn").disabled = true;
    try {
      const response = await api("/api/fix", {
        method: "POST",
        body: JSON.stringify({ problem, repo: state.repo, rounds, apply }),
      });
      const data = await response.json();
      out.innerHTML = "";

      for (const attempt of data.attempts || []) {
        const row = document.createElement("div");
        row.className = "check";
        row.innerHTML =
          `<span class="mark ${attempt.ok ? "ok" : "bad"}">${attempt.ok ? "✓" : "✗"}</span>` +
          `<span>round ${attempt.round}: ${escapeHtml(attempt.error || "checks ran")}</span>`;
        out.appendChild(row);
      }
      if (data.diff) {
        const label = document.createElement("p");
        label.className = "hint";
        label.textContent = "proposed diff";
        out.appendChild(label);
        const pre = document.createElement("pre");
        pre.className = "diff";
        paintDiff(pre, data.diff);
        out.appendChild(pre);
      }
      const summary = document.createElement("p");
      summary.className = data.ok ? "" : "err";
      summary.textContent = data.summary;
      out.appendChild(summary);
      confidenceBadge(out, data.confidence);
      if (apply && data.applied) {
        const applied = document.createElement("p");
        applied.className = "hint";
        applied.textContent = "applied and re-verified on your tree";
        out.appendChild(applied);
      }
      if (data.post_checks?.length) {
        const pre = document.createElement("pre");
        pre.className = "diff";
        pre.textContent = data.post_checks
          .map((check) => `${check.ok ? "✓" : "✗"} ${check.name}\n${(check.output || "").slice(-800)}`)
          .join("\n\n");
        out.appendChild(pre);
      }
    } catch (error) {
      out.innerHTML = `<p class="err">${escapeHtml(String(error.message || error))}</p>`;
    } finally {
      $("fix-btn").disabled = false;
      $("fix-apply-btn").disabled = false;
    }
  }

  // ------------------------------------------------------------------- wiring
  function selectTab(name) {
    for (const tab of ["ask", "analyze", "fix"]) {
      $("tab-" + tab).setAttribute("aria-selected", String(tab === name));
      $("panel-" + tab).classList.toggle("active", tab === name);
    }
  }

  $("tab-ask").onclick = () => selectTab("ask");
  $("tab-analyze").onclick = () => {
    selectTab("analyze");
    loadBrief();
    if (state.repo) listFiles();
  };
  $("tab-fix").onclick = () => selectTab("fix");

  $("repo-select").onchange = (event) => {
    state.repo = event.target.value;
    if (state.repo) localStorage.setItem("devmind_repo", state.repo);
    else localStorage.removeItem("devmind_repo");
    $("file-preview").hidden = true;
    $("checks-out").innerHTML = "";
    $("fix-out").innerHTML = "";
  };
  $("refresh-btn").onclick = async () => { await loadRepos(); await loadStatus(); };
  $("open-btn").onclick = openFolder;
  $("clone-btn").onclick = cloneRepo;
  $("git-input").addEventListener("keydown", (event) => { if (event.key === "Enter") cloneRepo(); });
  $("path-input").addEventListener("keydown", (event) => { if (event.key === "Enter") openFolder(); });
  $("files-btn").onclick = listFiles;
  $("brief-save").onclick = () => saveBrief(false);
  $("brief-clear").onclick = () => saveBrief(true);
  $("checks-btn").onclick = runChecks;
  $("fix-btn").onclick = () => proposeFix(false);
  $("fix-apply-btn").onclick = () => {
    if (confirm("Apply the patch to your project only if the checks pass on the copy?")) proposeFix(true);
  };

  const send = () => {
    const prompt = $("prompt").value.trim();
    if (!prompt) return;
    $("prompt").value = "";
    streamAnswer(prompt);
  };
  $("send-btn").onclick = send;
  $("prompt").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });

  let installPrompt = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    $("install-btn").hidden = false;
  });
  $("install-btn").onclick = async () => {
    if (!installPrompt) return;
    installPrompt.prompt();
    await installPrompt.userChoice;
    installPrompt = null;
    $("install-btn").hidden = true;
  };

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/static/sw.js", { scope: "/" }).catch(() => {});
  }

  loadStatus();
  loadRepos().catch(() => {});
})();
