const state = {
  sites: [],
  hub: null,
  selected: null,
  settings: null,
};

const $ = (id) => document.getElementById(id);

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || response.statusText);
    error.payload = data;
    throw error;
  }
  return data;
}

function fmt(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function setError(message) {
  $("error").textContent = message || "";
}

function setStatus(message) {
  $("status").textContent = message || "";
}

function renderHub() {
  const hub = state.hub || {};
  const where = `${hub.repo || "grummpy/my_domains"} ${hub.path || "configs/inventory.yaml"}`;
  if (hub.status === "ok") {
    $("hub").textContent = `Hub inventory loaded from ${where}. A local sites.yaml entry wins for the same domain.`;
  } else if (hub.status === "file") {
    $("hub").textContent = "Site list loaded from a local inventory file. Tests and overrides use this path.";
  } else if (hub.status === "disabled") {
    $("hub").textContent = "Hub fetch is off. Showing the local override and the synthetic sample.";
  } else {
    $("hub").textContent = `Hub inventory unavailable (${where}). Using the local override and the synthetic sample.`;
    $("hub").title = hub.error || "";
  }
}

function renderSites() {
  const query = $("filter").value.trim().toLowerCase();
  const list = $("sites");
  list.innerHTML = "";
  state.sites
    .filter((site) => !query || site.domain.toLowerCase().includes(query) || (site.note || "").toLowerCase().includes(query))
    .forEach((site) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "site-button";
      button.setAttribute("aria-pressed", state.selected && state.selected.domain === site.domain ? "true" : "false");
      const badge = site.report_only ? "report only" : "repo";
      button.innerHTML = `<strong></strong><span></span>`;
      button.querySelector("strong").textContent = site.domain;
      button.querySelector("span").textContent = `${site.source} · ${badge}${site.status ? " · " + site.status : ""}`;
      button.addEventListener("click", () => selectSite(site.domain));
      item.appendChild(button);
      list.appendChild(item);
    });
}

function selectSite(domain) {
  state.selected = state.sites.find((site) => site.domain === domain) || null;
  renderSites();
  const site = state.selected;
  if (!site) return;
  $("source").textContent = `${site.source} · ${site.report_only ? "report only" : site.github_repo}`;
  $("domain").textContent = site.domain;
  const preview = site.url === "internal://sample" ? "/sample/index.html" : site.url;
  $("note").textContent = site.note || "";
  if (preview.startsWith("http") || preview.startsWith("/")) {
    const link = document.createElement("a");
    link.href = preview;
    link.textContent = "Preview";
    link.target = "_blank";
    link.rel = "noopener";
    $("note").appendChild(document.createTextNode(" "));
    $("note").appendChild(link);
  }
  $("scan").disabled = false;
  $("prepare").disabled = !!site.report_only;
  $("prepare").title = site.report_only ? "No github_repo, so this site stays report-only." : "Commit a local imagediet branch. Does not push.";
  $("open-confirm").disabled = false;
  setError("");
  setStatus("Ready. Scan writes a report and leaves every original image alone.");
}

function renderReport(report) {
  $("totals").hidden = false;
  const totals = report.totals || {};
  $("before").textContent = fmt(totals.bytes_before || 0);
  $("after").textContent = fmt(totals.bytes_after || 0);
  $("savings").textContent = `${totals.savings_pct || 0}%`;
  $("mode").textContent = report.mode || "dry-run";
  const link = $("report-link");
  link.innerHTML = "";
  if (report.report && report.report.html_url) {
    const anchor = document.createElement("a");
    anchor.href = report.report.html_url;
    anchor.textContent = "Open HTML report";
    anchor.target = "_blank";
    link.appendChild(anchor);
  }
  const diff = $("diff");
  if (report.diff_stat) {
    diff.hidden = false;
    diff.textContent = report.diff_stat;
  } else {
    diff.hidden = true;
    diff.textContent = "";
  }
  const body = $("findings-body");
  const rows = [];
  (report.pages || []).forEach((page) => {
    (page.images || []).forEach((image) => rows.push(image));
  });
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7" class="empty">No images found.</td></tr>`;
    return;
  }
  body.innerHTML = "";
  rows.forEach((image) => {
    const tr = document.createElement("tr");
    const intrinsic = image.intrinsic_px && image.intrinsic_px[0] ? `${image.intrinsic_px[0]}×${image.intrinsic_px[1]}` : "—";
    const rendered = image.max_rendered_px ? `${image.max_rendered_px}px` : "—";
    const cells = [
      image.src,
      fmt(image.old_bytes),
      fmt(image.new_bytes),
      intrinsic,
      rendered,
      image.action,
      (image.reasons || []).join(", ") || "—",
    ];
    cells.forEach((value, index) => {
      const td = document.createElement("td");
      if (index === 0) {
        const code = document.createElement("code");
        code.textContent = value;
        td.appendChild(code);
      } else if (index === 5) {
        const badge = document.createElement("span");
        badge.className = `action ${value}`;
        badge.textContent = value;
        td.appendChild(badge);
      } else {
        td.textContent = value;
      }
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
}

async function scan() {
  if (!state.selected) return;
  setError("");
  setStatus("Scanning. This is a dry-run.");
  $("scan").disabled = true;
  try {
    const report = await api("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain: state.selected.domain }),
    });
    renderReport(report);
    setStatus("Dry-run finished. No image was replaced and nothing was pushed.");
  } catch (error) {
    setError(error.message);
    setStatus("");
  } finally {
    $("scan").disabled = false;
  }
}

async function prepareBranch() {
  if (!state.selected) return;
  setError("");
  setStatus("Preparing a local branch. This does not push.");
  $("prepare").disabled = true;
  try {
    const report = await api("/api/prepare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain: state.selected.domain }),
    });
    renderReport(report);
    setStatus(`Local branch ${report.branch} is committed in the work folder. Nothing was pushed.`);
  } catch (error) {
    setError(error.message);
  } finally {
    $("prepare").disabled = !!(state.selected && state.selected.report_only);
  }
}

function openConfirm() {
  if (!state.selected) return;
  $("modal").hidden = false;
  $("modal-copy").textContent = `This is the only step that can push a branch and open a pull request for ${state.selected.domain}. Type that domain exactly. Anything else does nothing. Original images stay in the repository either way.`;
  $("confirm-domain").value = "";
  $("confirm-submit").disabled = true;
  $("modal-error").textContent = "";
  $("confirm-domain").focus();
}

function closeConfirm() {
  $("modal").hidden = true;
}

function confirmReady() {
  const typed = $("confirm-domain").value.trim().toLowerCase();
  const domain = state.selected ? state.selected.domain.trim().toLowerCase() : "";
  $("confirm-submit").disabled = !domain || typed !== domain;
}

async function submitConfirm() {
  if (!state.selected || $("confirm-submit").disabled) return;
  $("modal-error").textContent = "";
  $("confirm-submit").disabled = true;
  try {
    const result = await api("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        domain: state.selected.domain,
        typed: $("confirm-domain").value,
      }),
    });
    closeConfirm();
    setStatus(result.pr_url ? `Pull request opened: ${result.pr_url}` : "Publish finished.");
    setError("");
  } catch (error) {
    $("modal-error").textContent = error.message;
    setError(error.message);
    confirmReady();
  }
}

async function saveSettings(event) {
  event.preventDefault();
  setError("");
  try {
    state.settings = await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        max_bytes: Math.round(Number($("max-kb").value) * 1024),
        ratio: Number($("ratio").value),
        quality: Number($("quality").value),
        use_playwright: $("playwright").checked,
      }),
    });
    setStatus("Settings saved. The next scan uses them. Nothing was published.");
  } catch (error) {
    setError(error.message);
  }
}

function fillSettings() {
  const settings = state.settings || {};
  $("max-kb").value = Math.round((settings.max_bytes || 204800) / 1024);
  $("ratio").value = settings.ratio ?? 1.5;
  $("quality").value = settings.quality ?? 80;
  $("playwright").checked = !!settings.use_playwright;
}

async function boot() {
  const [sites, settings, health] = await Promise.all([
    api("/api/sites"),
    api("/api/settings"),
    api("/api/health"),
  ]);
  state.sites = sites.sites || [];
  state.hub = sites.hub || {};
  state.settings = settings;
  if (health.host !== "127.0.0.1" && health.host !== "localhost") {
    setError("Refusing to use a UI that is not bound to localhost.");
  }
  fillSettings();
  renderHub();
  renderSites();
  const sample = state.sites.find((site) => site.domain === "sample.imagediet.test");
  if (sample) selectSite(sample.domain);
}

$("filter").addEventListener("input", renderSites);
$("scan").addEventListener("click", scan);
$("prepare").addEventListener("click", prepareBranch);
$("open-confirm").addEventListener("click", openConfirm);
$("confirm-cancel").addEventListener("click", closeConfirm);
$("confirm-domain").addEventListener("input", confirmReady);
$("confirm-submit").addEventListener("click", submitConfirm);
$("settings").addEventListener("submit", saveSettings);
$("modal").addEventListener("click", (event) => {
  if (event.target === $("modal")) closeConfirm();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeConfirm();
});

boot().catch((error) => setError(error.message));
