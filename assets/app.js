(() => {
  "use strict";

  const categories = {
    marketing: "Marketing terrain",
    evenementiel: "Événementiel",
    institutionnel: "Territoires & institutions",
    emploi: "Emploi & appels d’offres",
    autre: "Autres signaux"
  };
  const state = {
    entries: [],
    sources: [],
    snapshots: [],
    status: {},
    category: "all",
    period: "all",
    query: "",
    page: "dashboard",
    note: "",
    archives: [],
    favorites: loadLocal("veille66:favorites", []),
    prospects: loadLocal("veille66:prospects", []),
    compared: []
  };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function loadLocal(key, fallback) {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "null");
      return Array.isArray(value) ? value : fallback;
    } catch {
      return fallback;
    }
  }

  function saveLocal(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      showToast("Le stockage local est indisponible : vos repères ne pourront pas être conservés.");
    }
  }

  function escapeHtml(value = "") {
    return String(value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[character]);
  }

  function safeUrl(value) {
    try {
      const url = new URL(value, window.location.href);
      return ["https:", "http:"].includes(url.protocol) ? url.href : "#";
    } catch {
      return "#";
    }
  }

  function formatDate(value, options = { day: "numeric", month: "short", year: "numeric" }) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat("fr-FR", { ...options, timeZone: "Europe/Paris" }).format(date);
  }

  function apaDate(value) {
    if (!value) return "s. d.";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "s. d.";
    const parts = new Intl.DateTimeFormat("fr-FR", {
      day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Paris"
    }).formatToParts(date);
    const part = (type) => parts.find((item) => item.type === type)?.value;
    return `${part("year")}, ${part("day")} ${part("month")}`;
  }

  function sourceFor(entry) {
    return state.sources.find((source) => source.id === entry.sourceId) || {};
  }

  function apaReferenceHtml(entry) {
    const source = sourceFor(entry);
    const author = entry.sourceAuthor || source.author || entry.sourceName || "Source publique";
    const siteName = entry.sourceSiteName || source.site_name || "";
    const title = entry.title || "Publication sans titre";
    const url = safeUrl(entry.url);
    const titleEnd = /[.!?]$/.test(title.trim()) ? "" : ".";
    return `${escapeHtml(author)}. (${escapeHtml(apaDate(entry.publishedAt))}). <em>${escapeHtml(title)}</em>${titleEnd}${siteName && siteName.toLocaleLowerCase("fr") !== author.toLocaleLowerCase("fr") ? ` ${escapeHtml(siteName)}.` : ""} <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(entry.url || "")}</a>`;
  }

  function apaSourceReferenceHtml(source) {
    const author = source.author || source.name || "Source publique";
    const siteName = source.site_name || "";
    const title = source.apa_title || source.name || "Flux RSS/Atom";
    const url = safeUrl(source.url);
    return `${escapeHtml(author)}. (s. d.). <em>${escapeHtml(title)}</em>.${siteName && siteName.toLocaleLowerCase("fr") !== author.toLocaleLowerCase("fr") ? ` ${escapeHtml(siteName)}.` : ""} <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.url || "")}</a>`;
  }

  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.hidden = false;
    window.clearTimeout(showToast.timeout);
    showToast.timeout = window.setTimeout(() => { toast.hidden = true; }, 4200);
  }

  async function getJson(path) {
    const response = await fetch(path, { cache: "no-store" });
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
  }

  async function loadData() {
    const [latestResult, historyResult, sourcesResult, statusResult, archiveResult, noteResult] = await Promise.allSettled([
      getJson("data/latest.json"),
      getJson("data/history.json"),
      getJson("config/sources.json"),
      getJson("data/collection-status.json"),
      getJson("notes/weekly/index.json"),
      fetch("notes/latest.md", { cache: "no-store" }).then((response) => response.ok ? response.text() : "")
    ]);

    if (latestResult.status === "fulfilled") {
      const latest = latestResult.value;
      state.entries = Array.isArray(latest.entries) ? latest.entries : [];
      state.latest = latest;
    } else {
      state.latest = { status: "unavailable", entries: [], generatedAt: null };
    }
    state.snapshots = historyResult.status === "fulfilled" && Array.isArray(historyResult.value.snapshots) ? historyResult.value.snapshots : [];
    state.sources = sourcesResult.status === "fulfilled" && Array.isArray(sourcesResult.value.sources) ? sourcesResult.value.sources.filter((source) => source.active !== false) : [];
    state.configUnavailable = sourcesResult.status !== "fulfilled";
    state.status = statusResult.status === "fulfilled" ? statusResult.value : { status: "unavailable", message: "État de collecte indisponible." };
    state.archives = archiveResult.status === "fulfilled" && Array.isArray(archiveResult.value.archives) ? archiveResult.value.archives : [];
    state.note = noteResult.status === "fulfilled" ? noteResult.value : "";
    updateDashboard();
    renderHistory();
    renderSources();
    renderNotes();
    renderDataBanner();
  }

  function currentEntries() {
    const query = state.query.trim().toLocaleLowerCase("fr");
    const periodDays = state.period === "all" ? null : Number(state.period);
    const cutoff = periodDays ? Date.now() - periodDays * 86400000 : null;
    return state.entries.filter((entry) => {
      const id = String(entry.id || entry.url || entry.title);
      if (state.category === "favorites" && !state.favorites.includes(id)) return false;
      if (state.category === "prospects" && !state.prospects.includes(id)) return false;
      if (!["all", "favorites", "prospects"].includes(state.category) && entry.category !== state.category) return false;
      if (cutoff) {
        const published = Date.parse(entry.publishedAt || entry.collectedAt || "");
        if (!Number.isFinite(published) || published < cutoff) return false;
      }
      if (!query) return true;
      return [entry.title, entry.summary, entry.sourceName, entry.location, ...(entry.tags || [])]
        .join(" ").toLocaleLowerCase("fr").includes(query);
    }).sort((a, b) => Number(b.score || 0) - Number(a.score || 0) || Date.parse(b.publishedAt || "") - Date.parse(a.publishedAt || ""));
  }

  function updateDashboard() {
    const entries = currentEntries();
    const configured = state.sources.length;
    $("#stat-total").textContent = state.entries.length.toLocaleString("fr-FR");
    $("#stat-total-caption").textContent = state.entries.length ? "Signaux de la dernière collecte réussie" : "Aucun signal collecté pour le moment";
    $("#stat-sources").textContent = configured.toLocaleString("fr-FR");
    $("#stat-sources-caption").textContent = configured ? `${configured} flux autorisé${configured > 1 ? "s" : ""} actif${configured > 1 ? "s" : ""}` : "Aucun flux encore configuré";
    $("#stat-prospects").textContent = state.prospects.length.toLocaleString("fr-FR");
    $("#stat-updated").textContent = state.latest?.generatedAt ? formatDate(state.latest.generatedAt, { day: "numeric", month: "short" }) : "—";
    $("#stat-updated-caption").textContent = state.latest?.generatedAt ? `Mise à jour à ${formatDate(state.latest.generatedAt, { hour: "2-digit", minute: "2-digit" })}` : "Aucune donnée en direct";
    $("#results-summary").textContent = state.entries.length
      ? `${entries.length} signal${entries.length > 1 ? "ux" : ""} affiché${entries.length > 1 ? "s" : ""} sur ${state.entries.length} collecté${state.entries.length > 1 ? "s" : ""}.`
      : "Les publications apparaîtront ici après l’activation de flux publics autorisés.";
    for (const [key, selector] of Object.entries({ all: "#count-all", ...Object.fromEntries(Object.keys(categories).filter((key) => key !== "autre").map((key) => [key, `#count-${key}`])) })) {
      const count = key === "all" ? state.entries.length : state.entries.filter((entry) => entry.category === key).length;
      $(selector).textContent = count.toLocaleString("fr-FR");
    }
    $("#count-favorites").textContent = state.favorites.length.toLocaleString("fr-FR");
    $("#count-prospects").textContent = state.prospects.length.toLocaleString("fr-FR");
    renderSignals(entries);
    renderComparePanel();
    renderMiniChart();
    renderInsight(entries);
  }

  function renderSignals(entries) {
    const container = $("#signal-list");
    if (!entries.length) {
      const title = state.entries.length ? "Aucun résultat pour ces filtres" : "Votre veille commence ici";
      const body = state.entries.length
        ? "Essayez une autre période, une autre thématique ou une recherche plus courte."
        : "Aucune statistique ni publication de démonstration n’est mélangée aux données réelles. Ajoutez des flux publics autorisés pour démarrer.";
      container.innerHTML = `<div class="empty-state"><span class="empty-symbol" aria-hidden="true">⌁</span><h3>${title}</h3><p>${body}</p>${state.entries.length ? "" : '<button class="button button-outline" data-open-view="sources">Voir comment ajouter une source</button>'}</div>`;
      return;
    }
    container.innerHTML = entries.map((entry) => {
      const rawId = String(entry.id || entry.url || entry.title);
      const id = escapeHtml(rawId);
      const favorite = state.favorites.includes(rawId);
      const prospect = state.prospects.includes(rawId);
      const category = categories[entry.category] || categories.autre;
      const tags = Array.isArray(entry.tags) ? entry.tags.slice(0, 3) : [];
      return `<article class="signal-card">
        <div class="signal-meta"><span class="category-pill category-${escapeHtml(entry.category || "autre")}">${escapeHtml(category)}</span><span class="signal-date">${escapeHtml(formatDate(entry.publishedAt || entry.collectedAt))}</span><span class="score-pill" title="Indice de pertinence, pas une mesure de qualité">Pertinence ${Math.round(Number(entry.score) || 0)}</span></div>
        <h3><a href="${escapeHtml(safeUrl(entry.url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(entry.title || "Publication sans titre")}</a></h3>
        <p class="signal-summary">${escapeHtml(entry.summary || "Aucun extrait fourni par la source.")}</p>
        <div class="signal-footer"><span class="source-label"><span class="source-dot"></span>${escapeHtml(entry.sourceName || "Source publique")}${entry.location ? ` <span class="source-separator">·</span> ${escapeHtml(entry.location)}` : ""}</span><div class="signal-actions">
          ${tags.length ? `<span class="tag-list">${tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</span>` : ""}
          <button class="small-action ${favorite ? "is-active" : ""}" data-action="favorite" data-id="${id}" aria-pressed="${favorite}" title="${favorite ? "Retirer des favoris" : "Ajouter aux favoris"}">${favorite ? "★" : "☆"}<span class="visually-hidden">${favorite ? "Retirer des favoris" : "Ajouter aux favoris"}</span></button>
          <button class="small-action ${prospect ? "is-active" : ""}" data-action="prospect" data-id="${id}" aria-pressed="${prospect}" title="${prospect ? "Retirer des pistes" : "Marquer comme piste"}">✦<span class="visually-hidden">${prospect ? "Retirer des pistes" : "Marquer comme piste"}</span></button>
          <label class="compare-toggle"><input type="checkbox" data-action="compare" data-id="${id}" ${state.compared.includes(rawId) ? "checked" : ""}><span>Comparer</span></label>
        </div></div><details class="apa-reference"><summary>Référence APA 7</summary><p>${apaReferenceHtml(entry)}</p></details></article>`;
    }).join("");
  }

  function renderInsight(entries) {
    if (!entries.length) return;
    const best = entries[0];
    $("#insight-title").textContent = best.title || "Signal récent";
    $("#insight-body").textContent = `${best.sourceName || "Source publique"}${best.location ? ` · ${best.location}` : ""}. Score de pertinence indicatif : ${Math.round(Number(best.score) || 0)}/100.`;
  }

  function svgLineChart(snapshots, compact = false) {
    const rows = snapshots.slice(-14);
    if (!rows.length) return '<p class="empty-inline">L’historique apparaîtra après la collecte.</p>';
    const width = compact ? 300 : 820;
    const height = compact ? 92 : 250;
    const pad = compact ? { t: 10, r: 8, b: 10, l: 8 } : { t: 18, r: 14, b: 32, l: 34 };
    const max = Math.max(1, ...rows.map((row) => Number(row.total) || 0));
    const x = (index) => pad.l + (rows.length === 1 ? (width - pad.l - pad.r) / 2 : index * (width - pad.l - pad.r) / (rows.length - 1));
    const y = (value) => pad.t + (height - pad.t - pad.b) * (1 - (Number(value) || 0) / max);
    const points = rows.map((row, index) => `${x(index)},${y(row.total)}`).join(" ");
    const circles = rows.map((row, index) => `<circle cx="${x(index)}" cy="${y(row.total)}" r="${compact ? 2 : 3.5}" fill="#315bd6"><title>${escapeHtml(formatDate(row.date))} : ${Number(row.total) || 0}</title></circle>`).join("");
    const labels = compact ? "" : rows.map((row, index) => index % Math.ceil(rows.length / 7) === 0 || index === rows.length - 1
      ? `<text x="${x(index)}" y="${height - 7}" text-anchor="middle">${escapeHtml(formatDate(row.date, { day: "numeric", month: "short" }))}</text>` : "").join("");
    const grid = compact ? "" : [0, 0.5, 1].map((ratio) => {
      const lineY = pad.t + (height - pad.t - pad.b) * ratio;
      const value = Math.round(max * (1 - ratio));
      return `<line x1="${pad.l}" x2="${width - pad.r}" y1="${lineY}" y2="${lineY}" class="chart-grid"/><text x="${pad.l - 7}" y="${lineY + 4}" text-anchor="end">${value}</text>`;
    }).join("");
    return `<svg class="${compact ? "sparkline" : "line-chart"}" viewBox="0 0 ${width} ${height}" role="img" aria-label="Évolution des signaux collectés">${grid}<polyline points="${points}" fill="none" stroke="#315bd6" stroke-width="${compact ? 3 : 3}" stroke-linecap="round" stroke-linejoin="round"/>${circles}${labels}</svg>`;
  }

  function renderMiniChart() {
    $("#mini-chart").innerHTML = svgLineChart(state.snapshots, true);
  }

  function renderHistory() {
    const snapshots = [...state.snapshots].sort((a, b) => String(a.date).localeCompare(String(b.date)));
    const latest = snapshots[snapshots.length - 1];
    const previous = snapshots[snapshots.length - 2];
    const delta = latest && previous ? Number(latest.total || 0) - Number(previous.total || 0) : null;
    $("#history-metrics").innerHTML = `
      <article class="stat-card"><div class="stat-top"><span>Jours archivés</span><span class="stat-icon blue">▦</span></div><div class="stat-value">${snapshots.length}</div><div class="stat-caption">Journées avec une collecte réelle</div></article>
      <article class="stat-card"><div class="stat-top"><span>Dernier relevé</span><span class="stat-icon green">◷</span></div><div class="stat-value">${latest ? Number(latest.total || 0).toLocaleString("fr-FR") : "—"}</div><div class="stat-caption">${latest ? escapeHtml(formatDate(latest.date)) : "Aucune donnée"}</div></article>
      <article class="stat-card"><div class="stat-top"><span>Écart au relevé précédent</span><span class="stat-icon orange">↕</span></div><div class="stat-value">${delta === null ? "—" : `${delta > 0 ? "+" : ""}${delta}`}</div><div class="stat-caption">${delta === null ? "Deux collectes nécessaires" : "Variation du nombre de signaux"}</div></article>`;
    $("#history-chart").innerHTML = svgLineChart(snapshots);
    renderCategoryComparison(snapshots);
    $("#history-table").innerHTML = snapshots.length
      ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Signaux</th><th>Thématique principale</th><th>Sources</th></tr></thead><tbody>${snapshots.slice().reverse().map((row) => `<tr><td>${escapeHtml(formatDate(row.date))}</td><td>${Number(row.total) || 0}</td><td>${escapeHtml(topCategory(row.categories || {}))}</td><td>${Number(row.sourceCount) || 0}</td></tr>`).join("")}</tbody></table></div>`
      : '<div class="empty-state compact-empty"><h3>Aucun historique disponible</h3><p>Les instantanés apparaîtront après une collecte réussie. Les périodes sans données restent vides.</p></div>';
  }

  function topCategory(counts) {
    const best = Object.entries(counts).sort((a, b) => Number(b[1]) - Number(a[1]))[0];
    return best ? `${categories[best[0]] || best[0]} (${best[1]})` : "—";
  }

  function renderCategoryComparison(snapshots) {
    const latest = snapshots.slice(-7);
    const previous = snapshots.slice(-14, -7);
    if (!latest.length) {
      $("#category-comparison").innerHTML = '<p class="empty-inline">La comparaison sera disponible après plusieurs collectes.</p>';
      return;
    }
    const totalByCategory = (rows) => rows.reduce((acc, row) => {
      for (const [key, value] of Object.entries(row.categories || {})) acc[key] = (acc[key] || 0) + Number(value || 0);
      return acc;
    }, {});
    const now = totalByCategory(latest);
    const then = totalByCategory(previous);
    $("#category-comparison").innerHTML = Object.entries(categories).filter(([key]) => key !== "autre").map(([key, label]) => {
      const current = now[key] || 0;
      const earlier = then[key] || 0;
      const difference = previous.length ? current - earlier : null;
      return `<div class="comparison-row"><span>${escapeHtml(label)}</span><strong>${current}</strong><span class="comparison-delta">${difference === null ? "Comparaison en attente" : `${difference > 0 ? "+" : ""}${difference} vs. période précédente`}</span></div>`;
    }).join("");
  }

  function renderSources() {
    const active = state.sources;
    const failed = Number(state.status.sourcesFailed || 0);
    const errors = Array.isArray(state.status.errors) ? state.status.errors : [];
    $("#source-summary").innerHTML = `<div class="source-kpi"><strong>${active.length}</strong><span>flux actifs configurés</span></div><div class="source-kpi"><strong>${Number(state.status.sourcesSucceeded || 0)}</strong><span>succès au dernier passage</span></div><div class="source-kpi"><strong>${failed}</strong><span>erreurs au dernier passage</span></div>${errors.map((error) => `<div class="source-error"><strong>${escapeHtml(error.sourceName || error.sourceId || "Flux")}</strong><span>${escapeHtml(error.message || "Erreur inconnue")}</span></div>`).join("")}`;
    if (!active.length) {
      $("#source-list").innerHTML = '<div class="empty-state"><span class="empty-symbol" aria-hidden="true">◎</span><h3>Aucun flux configuré</h3><p>Le dépôt démarre sans source préremplie afin d’éviter la collecte non autorisée ou l’invention de données. Le fichier <code>config/sources.json</code> est la liste de référence.</p></div>';
      return;
    }
    $("#source-list").innerHTML = active.map((source) => `<article class="source-card"><div class="source-avatar">${escapeHtml((source.name || "S").slice(0, 1).toLocaleUpperCase("fr"))}</div><div class="source-details"><div class="source-title-row"><h3>${escapeHtml(source.name || source.id)}</h3><span class="source-active"><i></i> Flux actif</span></div><p>${escapeHtml(source.description || "Flux public autorisé")}</p><div class="source-tags"><span>${escapeHtml(categories[source.category] || source.category || "Non classé")}</span><span>${escapeHtml(source.location || "Occitanie")}</span>${source.terms_url ? `<a href="${escapeHtml(safeUrl(source.terms_url))}" target="_blank" rel="noopener noreferrer">Conditions · vérifiées le ${escapeHtml(source.terms_reviewed_at || "date non renseignée")} ↗</a>` : ""}</div><p class="apa-source-reference"><strong>Référence du flux · APA 7 :</strong> ${apaSourceReferenceHtml(source)}</p></div><a class="source-open" href="${escapeHtml(safeUrl(source.homepage || source.url))}" target="_blank" rel="noopener noreferrer" aria-label="Ouvrir ${escapeHtml(source.name || source.id)}">↗</a></article>`).join("");
  }

  function markdownInline(value) {
    const output = [];
    const pattern = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|\*([^*]+)\*/g;
    let cursor = 0;
    for (const match of value.matchAll(pattern)) {
      output.push(escapeHtml(value.slice(cursor, match.index)));
      output.push(match[2]
        ? `<a href="${escapeHtml(safeUrl(match[2]))}" target="_blank" rel="noopener noreferrer">${escapeHtml(match[1])}</a>`
        : `<em>${escapeHtml(match[3])}</em>`);
      cursor = match.index + match[0].length;
    }
    output.push(escapeHtml(value.slice(cursor)));
    return output.join("");
  }

  function markdownToHtml(markdown) {
    const lines = markdown.split(/\r?\n/);
    const output = [];
    let inList = false;
    for (const line of lines) {
      if (/^\s*[-*]\s+/.test(line)) {
        if (!inList) output.push("<ul>");
        inList = true;
        output.push(`<li>${markdownInline(line.replace(/^\s*[-*]\s+/, ""))}</li>`);
        continue;
      }
      if (inList) { output.push("</ul>"); inList = false; }
      if (/^###\s+/.test(line)) output.push(`<h3>${markdownInline(line.replace(/^###\s+/, ""))}</h3>`);
      else if (/^##\s+/.test(line)) output.push(`<h2>${markdownInline(line.replace(/^##\s+/, ""))}</h2>`);
      else if (/^#\s+/.test(line)) output.push(`<h1>${markdownInline(line.replace(/^#\s+/, ""))}</h1>`);
      else if (/^>\s?/.test(line)) output.push(`<blockquote>${markdownInline(line.replace(/^>\s?/, ""))}</blockquote>`);
      else if (line.trim()) output.push(`<p>${markdownInline(line)}</p>`);
    }
    if (inList) output.push("</ul>");
    return output.join("");
  }

  function renderNotes() {
    const note = $("#weekly-note");
    if (!state.note.trim()) {
      note.innerHTML = '<div class="empty-state compact-empty"><span class="empty-symbol">▤</span><h3>Aucune synthèse publiée</h3><p>Une note sera créée après la première publication hebdomadaire. La note d’exemple fictive ci-dessous est exclue des données de veille.</p></div>';
    } else {
      note.innerHTML = `<div class="note-document">${markdownToHtml(state.note)}</div>`;
    }
    $("#weekly-archives").innerHTML = state.archives.length
      ? state.archives.map((archive) => `<a class="archive-item" href="${escapeHtml(safeUrl(archive.url))}" target="_blank" rel="noopener noreferrer"><span class="archive-icon">▤</span><span><strong>${escapeHtml(archive.label || archive.week)}</strong><small>${escapeHtml(archive.week || "")}</small></span><span class="archive-arrow">↗</span></a>`).join("")
      : '<p class="empty-inline">Aucune archive hebdomadaire pour le moment.</p>';
  }

  function renderDataBanner() {
    const banner = $("#data-banner");
    const status = state.status.status || state.latest?.status || "unconfigured";
    const checkedAt = state.status.checkedAt ? `Dernier contrôle : ${formatDate(state.status.checkedAt, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}. ` : "";
    if (status === "error") {
      banner.className = "data-banner banner-error";
      banner.textContent = `Collecte en erreur. ${checkedAt}${state.status.message || "Les indicateurs conservés correspondent à la dernière collecte réussie, pas à une nouvelle collecte."}`;
      banner.hidden = false;
    } else if (state.latest?.status === "unavailable" || state.configUnavailable || status === "unavailable") {
      banner.className = "data-banner banner-error";
      banner.textContent = "Les fichiers de données publiés sont indisponibles ou illisibles. Les indicateurs ne sont pas remplacés par des données de démonstration.";
      banner.hidden = false;
    } else if (status === "unconfigured" || !state.sources.length) {
      banner.className = "data-banner banner-neutral";
      banner.textContent = "Veille non configurée : aucun flux public autorisé n’a encore été ajouté. Les statistiques restent vides jusqu’à une collecte réelle.";
      banner.hidden = false;
    } else if (status === "empty" || status === "partial") {
      banner.className = status === "partial" ? "data-banner banner-warning" : "data-banner banner-neutral";
      banner.textContent = `${status === "partial" ? "Collecte partielle." : "Aucun signal pertinent à afficher."} ${checkedAt}${state.status.message || ""}`;
      banner.hidden = false;
    } else {
      banner.hidden = true;
    }
  }

  function switchView(page) {
    if (!document.querySelector(`#view-${page}`)) return;
    state.page = page;
    $$(".view").forEach((view) => view.classList.toggle("active", view.dataset.page === page));
    $$(".nav-link").forEach((button) => button.classList.toggle("active", button.dataset.view === page));
    if (window.location.hash !== `#${page}`) history.replaceState(null, "", `#${page}`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function exportCsv(rows, filename, columns = ["title", "sourceName", "category", "location", "publishedAt", "score", "url", "apa7Reference"]) {
    if (!rows.length) { showToast("Aucun élément réel à exporter."); return; }
    const value = (cell) => {
      const raw = String(cell ?? "");
      const safe = /^[\s\x00-\x1f]*[=+\-@]/.test(raw) ? `'${raw}` : raw;
      return `"${safe.replace(/"/g, '""')}"`;
    };
    const csv = `\uFEFF${columns.join(";")}\r\n${rows.map((row) => columns.map((column) => value(column === "apa7Reference" ? apaReferenceText(row) : row[column])).join(";")).join("\r\n")}`;
    download(csv, filename, "text/csv;charset=utf-8");
  }

  function apaReferenceText(entry) {
    const source = sourceFor(entry);
    const author = entry.sourceAuthor || source.author || entry.sourceName || "Source publique";
    const siteName = entry.sourceSiteName || source.site_name || "";
    const title = entry.title || "Publication sans titre";
    const site = siteName && siteName.toLocaleLowerCase("fr") !== author.toLocaleLowerCase("fr") ? ` ${siteName}.` : "";
    const titleEnd = /[.!?]$/.test(title.trim()) ? "" : ".";
    return `${author}. (${apaDate(entry.publishedAt)}). ${title}${titleEnd}${site} ${entry.url || ""}`.trim();
  }

  function download(content, filename, type) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  function handleClick(event) {
    const target = event.target.closest("[data-view], [data-open-view], [data-action], .filter-chip");
    if (!target) return;
    if (target.matches(".filter-chip")) {
      state.category = target.dataset.category;
      $$(".filter-chip").forEach((button) => button.classList.toggle("selected", button === target));
      updateDashboard();
      return;
    }
    if (target.dataset.view || target.dataset.openView) {
      switchView(target.dataset.view || target.dataset.openView);
      return;
    }
    const id = target.dataset.id;
    if (target.dataset.action === "favorite") {
      state.favorites = state.favorites.includes(id) ? state.favorites.filter((item) => item !== id) : [...state.favorites, id];
      saveLocal("veille66:favorites", state.favorites);
      renderSignals(currentEntries());
      renderComparePanel();
    } else if (target.dataset.action === "prospect") {
      state.prospects = state.prospects.includes(id) ? state.prospects.filter((item) => item !== id) : [...state.prospects, id];
      saveLocal("veille66:prospects", state.prospects);
      $("#stat-prospects").textContent = state.prospects.length.toLocaleString("fr-FR");
      renderSignals(currentEntries());
      renderComparePanel();
    } else if (target.dataset.action === "compare") {
      return;
    }
  }

  document.addEventListener("click", handleClick);
  document.addEventListener("change", (event) => {
    if (event.target.matches(".filter-chip")) {
      state.category = event.target.dataset.category;
      $$(".filter-chip").forEach((button) => button.classList.toggle("selected", button === event.target));
      updateDashboard();
    } else if (event.target.matches("#date-filter")) {
      state.period = event.target.value;
      updateDashboard();
    } else if (event.target.matches('[data-action="compare"]')) {
      const { id } = event.target.dataset;
      state.compared = event.target.checked ? [...new Set([...state.compared, id])] : state.compared.filter((item) => item !== id);
      if (state.compared.length > 3) {
        state.compared.shift();
        showToast("La comparaison est limitée aux trois signaux sélectionnés les plus récents.");
        renderSignals(currentEntries());
      }
      renderComparePanel();
    }
  });
  $("#global-search").addEventListener("input", (event) => {
    state.query = event.target.value;
    updateDashboard();
  });
  $("#refresh-button").addEventListener("click", async () => {
    $("#refresh-button").classList.add("spinning");
    await loadData().catch(() => showToast("Impossible de recharger les données publiées."));
    $("#refresh-button").classList.remove("spinning");
  });
  $("#export-csv").addEventListener("click", () => exportCsv(currentEntries(), "veille-terrain-66-signaux.csv"));
  $("#history-export").addEventListener("click", () => {
    const rows = state.snapshots.map((row) => ({ date: row.date, total: row.total, sourceCount: row.sourceCount, ...Object.fromEntries(Object.entries(row.categories || {}).map(([key, value]) => [`${key}_count`, value])) }));
    exportCsv(rows, "veille-terrain-66-historique.csv", ["date", "total", "sourceCount", "marketing_count", "evenementiel_count", "institutionnel_count", "emploi_count", "autre_count"]);
  });
  $("#print-button").addEventListener("click", () => window.print());
  $("#notes-print").addEventListener("click", () => { switchView("notes"); window.print(); });
  window.addEventListener("keydown", (event) => {
    if (event.key === "/" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
      event.preventDefault();
      $("#global-search").focus();
    }
  });
  window.addEventListener("hashchange", () => switchView(location.hash.slice(1) || "dashboard"));
  window.addEventListener("storage", (event) => {
    if (event.key === "veille66:favorites") state.favorites = loadLocal("veille66:favorites", []);
    if (event.key === "veille66:prospects") state.prospects = loadLocal("veille66:prospects", []);
    updateDashboard();
  });

  function renderComparePanel() {
    let panel = $("#compare-panel");
    if (!state.compared.length) {
      panel?.remove();
      return;
    }
    if (!panel) {
      panel = document.createElement("section");
      panel.id = "compare-panel";
      panel.className = "compare-panel";
      $("#signal-list").prepend(panel);
    }
    const selected = state.compared.map((id) => state.entries.find((entry) => String(entry.id || entry.url || entry.title) === id)).filter(Boolean);
    panel.innerHTML = `<div class="compare-heading"><strong>Comparaison (${selected.length}/3)</strong><button class="text-button" id="clear-compare">Effacer</button></div><div class="compare-items">${selected.map((entry) => `<article><span>${escapeHtml(categories[entry.category] || categories.autre)}</span><strong>${escapeHtml(entry.title)}</strong><small>${escapeHtml(entry.sourceName || "Source publique")} · score ${Math.round(Number(entry.score) || 0)}</small></article>`).join("")}</div>`;
    $("#clear-compare").addEventListener("click", () => { state.compared = []; updateDashboard(); });
  }

  const dateLine = $("#date-line");
  if (dateLine) dateLine.textContent = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Paris" }).format(new Date()).toLocaleUpperCase("fr");
  const initialPage = location.hash.slice(1);
  if (initialPage) switchView(initialPage);
  loadData().catch(() => {
    state.status = { status: "unavailable", message: "Les fichiers publiés sont indisponibles. Vérifiez le déploiement GitHub Pages." };
    renderDataBanner();
    updateDashboard();
    renderHistory();
    renderSources();
    renderNotes();
  });
})();
