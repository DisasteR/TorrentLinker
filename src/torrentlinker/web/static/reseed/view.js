"use strict";
// Shared owner workspace. All data and mutations come from the host bridge.
const labels = {
  find: "À retrouver",
  candidates: "Comparer",
  links: "Préparer les noms",
  verify: "Vérifier",
  incomplete: "Compléter",
  seeding: "En partage",
};
let snapshot = {
    rows: [],
    setup: { folders: [], clients: [] },
    progress: {},
    running: false,
  },
  tab = "reseed",
  filter = "",
  query = "",
  selected = "",
  checked = new Set(),
  limit = 100,
  drawer = false,
  active = false,
  generation = 0,
  busy = false;
let environment = { mode: "web", language: "fr", theme: "dark" },
  browseTarget = "",
  browseForm = null;
const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const bytes = (n) =>
  n >= 1073741824
    ? (n / 1073741824).toFixed(1) + " Go"
    : n >= 1048576
      ? (n / 1048576).toFixed(1) + " Mo"
      : (n / 1024).toFixed(0) + " Ko";
const api = async (action, body = {}) =>
  (await window.reseedTransport)(action, {
    ...body,
    _client: environment.client,
  });
async function refresh() {
  if (!active || busy) return;
  const g = generation;
  try {
    const value = await api("state");
    if (g !== generation) return;
    if (JSON.stringify(snapshot) === JSON.stringify(value)) return;
    snapshot = value;
    $("error").textContent = value.error || "";
    if (!snapshot.rows.some((r) => r.path === selected))
      selected = snapshot.rows[0]?.path || "";
    render();
  } catch (e) {
    $("error").textContent = e.message;
  }
}
async function perform(action, body = {}) {
  busy = true;
  try {
    const value = await api(action, body);
    $("error").textContent = "";
    $("dialog-error").textContent = "";
    return value;
  } catch (e) {
    $("error").textContent = e.message;
    $("dialog-error").textContent = e.message;
    throw e;
  } finally {
    busy = false;
  }
}
const visible = () =>
  snapshot.rows.filter(
    (r) =>
      (!filter || r.stage === filter) &&
      r.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
function stateFor(r) {
  const p = snapshot.progress?.active?.[r.path];
  if (snapshot.progress?.paused_paths?.includes(r.path)) return "En pause";
  if (r.error) return "À examiner";
  if (p)
    return (
      (labels[r.stage] || p.phase) +
      (p.fraction != null
        ? " · " + Math.round(p.fraction * 100) + " %"
        : " · en cours")
    );
  return r.stage === "seeding"
    ? "En partage"
    : "En attente · " + labels[r.stage];
}
function render() {
  const focusSearch = document.activeElement?.id === "search";
  const cursor = focusSearch ? document.activeElement.selectionStart : 0;
  const scroll = window.scrollY;
  const oldStages = new Map(
    [...document.querySelectorAll("[data-item]")].map((n) => [
      n.dataset.item,
      n.dataset.stage,
    ]),
  );
  document.body.dataset.mode = environment.mode;
  document.documentElement.dataset.theme = environment.theme;
  document
    .querySelectorAll("[data-tab]")
    .forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  let folders = snapshot.setup.folders || [];
  if (tab === "folders") {
    $("content").innerHTML =
      `<div class="row between"><h2>Dossiers de bibliothèque</h2><button data-command="manage-folders">Modifier les dossiers</button><button data-command="add-folder">+ Ajouter un dossier</button></div><div class="panel folder-list" style="margin-top:20px">${folders.map((f) => `<div class="tools"><b>${esc(f.local)}</b><small>${esc(f.kind)} · ${f.depth == null ? "Automatique" : "Profondeur " + f.depth}</small></div>`).join("") || "<p>Aucun dossier configuré.</p>"}</div>`;
    return;
  }
  if (tab === "activity") {
    $("content").innerHTML =
      '<h2>Activité du parcours</h2><div class="panel" style="margin-top:20px">' +
      snapshot.rows
        .flatMap((r) => (r.events || []).map((e) => ({ ...e, name: r.name })))
        .sort((a, b) => b.at - a.at)
        .map(
          (e) =>
            `<div class="tools"><small>${new Date(e.at * 1000).toLocaleString()}</small><b>${esc(e.name)}</b><span>${esc({ prepared: "Liens prêts", verified: "Contenu vérifié", checking: "Vérification du client" }[e.phase] || labels[e.phase] || e.phase)}</span></div>`,
        )
        .join("") +
      "</div>";
    return;
  }
  const rows = visible(),
    progress = snapshot.progress || {},
    running = snapshot.running,
    current = Object.values(progress.active || {});
  $("content").innerHTML =
    `<div class="intro row between"><div><h2>Chaque fichier avance à son rythme.</h2><p class="muted">Retrouvez, vérifiez et partagez. Intervenez seulement quand c’est nécessaire.</p></div><button class="primary" data-command="run">${running ? (snapshot.paused ? "Reprendre le parcours" : "Suspendre le parcours") : "Tout enchaîner"}</button></div><div class="guided"><button data-command="settings"><strong>01</strong><span>Réglages<small>${esc(snapshot.setup.name || "Client à configurer")} · ${snapshot.setup.reseed_link_root ? "Dossier de liens configuré" : "Dossier de liens à choisir"}</small></span></button><button data-command="disk"><strong>02</strong><span>Disque<small>${folders.length} dossiers · ${snapshot.scanning ? "Analyse en cours…" : "parcourir et analyser"}</small></span></button><span class="hint">Les éléments avancent indépendamment. Les décisions attendues ne bloquent pas les autres.</span></div><div class="stages"><button class="stage ${filter ? "" : "on"}" data-filter=""><b>${snapshot.rows.length}</b>Tous les éléments<br><small>Vue d’ensemble</small></button>${Object.entries(
      labels,
    )
      .map(
        ([key, label]) =>
          `<button class="stage ${filter === key ? "on" : ""}" data-filter="${key}"><b>${snapshot.rows.filter((r) => r.stage === key).length}</b>${label}<br><small>${snapshot.rows.filter((r) => r.stage === key && progress.active?.[r.path]).length} en cours</small></button>`,
      )
      .join(
        "",
      )}</div><div class="running"><span><i class="dot" style="animation-play-state:${running && !snapshot.paused ? "running" : "paused"}"></i>${running ? (snapshot.paused ? "Parcours suspendu" : "Parcours actif") : "Prêt à commencer"}</span><small>${current.length} opérations simultanées · ${snapshot.rows.filter((r) => r.error).length} décisions attendues</small><small style="margin-left:auto">Vérification : 1 à la fois · Recherche : 2 maximum</small>${running ? '<button data-command="stop">Arrêter</button>' : ""}</div><div class="workspace"><section class="panel"><div class="tools"><input id="search" placeholder="Rechercher dans mes fichiers…" aria-label="Rechercher" value="${esc(query)}"><button data-command="select">${checked.size ? checked.size + " sélectionnés" : "Sélectionner la liste"}</button><button data-command="batch" ${checked.size ? "" : "disabled"}>Enchaîner la sélection</button></div>${
      rows
        .slice(0, limit)
        .map((r) => {
          const p = progress.active?.[r.path];
          return `<div class="torrent ${selected === r.path ? "selected" : ""}" data-item="${esc(r.path)}" data-stage="${r.stage}" role="button" tabindex="0"><input type="checkbox" data-check="${esc(r.path)}" aria-label="Sélectionner ${esc(r.name)}" ${checked.has(r.path) ? "checked" : ""}><div class="cover" aria-hidden="true">${esc(r.name.slice(0, 2).toUpperCase())}</div><div><div class="title">${esc(r.name)}</div><small>${bytes(r.size)} · ${esc(r.folder || "")}</small></div><div><span class="badge ${r.error ? "warn" : p ? "blue" : ""}">${esc(stateFor(r))}</span>${p ? `<div class="progress"><i style="width:${p.fraction == null ? 35 : Math.round(p.fraction * 100)}%"></i></div>` : ""}</div></div>`;
        })
        .join("") ||
      '<div class="empty">Aucun élément ici. Ajoutez un dossier ou changez le filtre.</div>'
    }<div id="more" class="foot">${Math.min(limit, rows.length)} / ${rows.length} éléments</div></section>${inspector()}</div>`;
  if (focusSearch) {
    $("search").focus({ preventScroll: true });
    $("search").setSelectionRange(cursor, cursor);
  }
  window.scrollTo(0, scroll);
  for (const node of document.querySelectorAll("[data-item]")) {
    if (
      oldStages.has(node.dataset.item) &&
      oldStages.get(node.dataset.item) !== node.dataset.stage
    )
      node.classList.add("pulse");
  }
  observer.disconnect();
  if (rows.length > limit) observer.observe($("more"));
}
const observer = new IntersectionObserver(
  (entries) => {
    if (entries.some((e) => e.isIntersecting)) {
      limit += 100;
      render();
    }
  },
  { rootMargin: "250px" },
);
function inspector() {
  const r = snapshot.rows.find((r) => r.path === selected);
  if (!r) return "";
  const steps = Object.keys(labels),
    index = steps.indexOf(r.stage),
    paused = snapshot.progress?.paused_paths?.includes(r.path);
  return `<aside class="panel inspector ${drawer ? "open" : ""}"><button class="close" data-command="close-inspector">Fermer</button><div class="eyebrow">L’ÉLÉMENT SÉLECTIONNÉ</div><div class="detail-head"><h2>${esc(r.name)}</h2><small>${bytes(r.size)} · ${esc(r.path)}</small></div><span class="badge ${r.error ? "warn" : ""}">${esc(stateFor(r))}</span><div class="callout">${r.error ? `<p class="error">${esc(r.error)}</p>` : ""}<p class="muted">${r.stage === "verify" ? "Un seul hash à la fois protège le disque. Les recherches continuent." : "Les fichiers originaux conservent leurs noms."}</p><small>La pause prend effet à la fin de l’opération en cours.</small>${snapshot.running ? `<button data-command="pause-item">${paused ? "Reprendre cet élément" : "Suspendre cet élément"}</button>` : r.stage !== "seeding" ? '<button data-command="run-item">Continuer cet élément</button>' : ""}<button data-command="review">Examiner les fichiers et les réparations</button></div>${(
    r.candidates || []
  )
    .slice(0, 20)
    .map(
      (c) =>
        `<div class="candidate"><b>${esc(c.title)}</b><p class="muted">${esc(c.match)} · ${bytes(c.size || 0)}</p>${(c.files || []).map((f) => `<pre>${esc(f.local || "Manquant")}<br>↳ ${esc(f.torrent)}</pre>`).join("")}${c.match === "full" && !c.archive ? `<button ${snapshot.progress?.active?.[r.path] ? "disabled" : ""} data-candidate="${esc(c.key)}">Choisir ce candidat</button>` : ""}</div>`,
    )
    .join(
      "",
    )}<div class="section"><b>Son parcours</b><ol class="steps">${steps.map((key, i) => `<li class="${index === i ? "current" : ""}">${i < index ? "✓" : i === index ? "●" : "○"}　${labels[key]}${key === "links" ? "<small> · Symlink</small>" : ""}</li>`).join("")}</ol></div>${r.destination ? `<div class="section"><b>Dossier de liens à conserver</b><pre>${esc(r.destination)}</pre>${(r.files || []).map((f) => `<pre>${esc(f.source)}<br>↳ ${esc(f.name)}</pre>`).join("")}</div>` : ""}<div class="section"><b>Historique de cet élément</b><div class="events">${
    (r.events || [])
      .slice()
      .reverse()
      .map(
        (e) =>
          `<p><small>${new Date(e.at * 1000).toLocaleString()}</small><br>${esc({ prepared: "Liens prêts", verified: "Contenu vérifié", checking: "Vérification du client" }[e.phase] || labels[e.phase] || e.phase)}</p>`,
      )
      .join("") || "<p>Aucune opération enregistrée.</p>"
  }</div></div></aside>`;
}
function dialog(html) {
  $("dialog-error").textContent = "";
  $("dialog-body").innerHTML = html;
  if (!$("dialog").open) $("dialog").showModal();
}
function settings() {
  dialog(
    `<div class="eyebrow">01 · RÉGLAGES</div><h2>Réglages du parcours</h2><p>Client cible : ${esc(snapshot.setup.name)}</p><button data-command="add-client">+ Ajouter un client</button><button data-command="client-settings">Paramètres du client</button><form id="root-form"><label>Dossier de liens à conserver<div class="row"><input name="path" required value="${esc(snapshot.setup.reseed_link_root)}" style="min-width:0;width:100%"><button type="button" data-command="browse-root">Parcourir…</button></div></label><p class="muted">Hors bibliothèque, accessible au client. Les fichiers originaux gardent leurs noms.</p><button class="primary">Enregistrer</button></form><button data-command="disk">Continuer vers Disque →</button>`,
  );
}
function disk() {
  dialog(
    `<div class="eyebrow">02 · DISQUE</div><h2>Parcourir mes dossiers</h2><p class="muted">${environment.mode === "desktop" ? "Dossiers sur cet ordinateur" : "Dossiers accessibles au serveur TorrentLinker"}</p>${(snapshot.setup.folders || []).map((f) => `<label><input type="checkbox" data-scan-folder="${esc(f.local)}" checked> ${esc(f.local)}</label>`).join("")}<button data-command="add-folder">+ Ajouter un dossier</button><p><button class="primary" data-command="scan" ${snapshot.scanning ? "disabled" : ""}>${snapshot.scanning ? "Analyse en cours…" : "Parcourir mon disque maintenant"}</button></p>`,
  );
}
function folderForm() {
  dialog(
    `<h2>Ajouter à la bibliothèque</h2><form id="folder-form"><label>Chemin accessible à TorrentLinker<div class="row"><input name="local" required style="min-width:0;width:100%"><button type="button" data-command="browse-folder">Parcourir…</button></div></label><small>${environment.mode === "desktop" ? "Sur cet ordinateur" : "Sur le serveur TorrentLinker"}</small><label>Contenu<select name="kind"><option value="movie">Films</option><option value="series">Séries</option><option value="music">Audio</option><option value="ebook">Livres</option><option value="game">Jeux vidéo</option><option value="3d-model">Impression 3D</option><option value="other">Autre</option></select></label><label><input type="checkbox" name="scan" checked> Analyser après l’ajout</label><button class="primary">Ajouter le dossier</button></form>`,
  );
}
function clientForm() {
  dialog(
    `<h2>Connecter un client torrent</h2><form id="client-form"><label>Nom<input name="name" required></label><label>Type<select name="client"><option value="qbittorrent">qBittorrent</option><option value="transmission">Transmission</option><option value="rtorrent">rTorrent</option><option value="deluge">Deluge</option><option value="utorrent">µTorrent</option><option value="bittorrent">BitTorrent</option></select></label><label>Adresse<input name="host" required placeholder="http://localhost:8080"></label><label>Utilisateur<input name="username" autocomplete="off"></label><label>Mot de passe<input name="password" type="password" autocomplete="new-password"></label><button class="primary">Tester et ajouter</button></form>`,
  );
}
async function browse(target, path = "") {
  browseTarget = target;
  const result = await perform("browse", { path });
  if (result.selected !== undefined) {
    if (result.selected) document.querySelector(target).value = result.selected;
    return;
  }
  if (!browseForm)
    browseForm = {
      html: $("dialog-body").innerHTML,
      values: [...$("dialog-body").querySelectorAll("input,select")].map(
        (n) => [n.name, n.value, n.checked],
      ),
    };
  dialog(
    `<h2>Dossiers du serveur TorrentLinker</h2><p>${esc(result.path || "Racines montées")}</p>${result.parent ? `<button data-browse="${esc(result.parent)}">↑ Parent</button>` : ""}${(result.folders || result.children || []).map((f) => `<button data-browse="${esc(typeof f === "string" ? f : f.path)}">${esc(typeof f === "string" ? f : f.name)}</button>`).join("")}${result.path ? '<button id="choose-folder" class="primary">Choisir ce dossier</button>' : ""}`,
  );
  const choose = $("choose-folder");
  if (choose)
    choose.onclick = () => {
      dialog(browseForm.html);
      for (const [name, value, checked] of browseForm.values) {
        const n = document.querySelector(`[name="${name}"]`);
        if (n) {
          n.value = value;
          n.checked = checked;
        }
      }
      document.querySelector(target).value = result.path;
      browseForm = null;
    };
}
async function start(paths, key = "") {
  if (!paths.length) return;
  if (snapshot.running) {
    await perform("enqueue", { paths, key });
    return;
  }
  if (!snapshot.setup.reseed_link_root) {
    settings();
    return;
  }
  if (
    !confirm(
      `${paths.length} élément(s). Créer les liens, vérifier le contenu puis partager ? Les fichiers originaux restent inchangés.`,
    )
  )
    return;
  await perform("run", { paths, key });
  await refresh();
}
document.addEventListener("click", (e) => {
  handleClick(e).catch(() => {});
});
async function handleClick(e) {
  const b = e.target.closest("button");
  if (b?.dataset.tab) {
    tab = b.dataset.tab;
    render();
    return;
  }
  if (b && "filter" in b.dataset) {
    filter = b.dataset.filter;
    limit = 100;
    render();
    return;
  }
  if (e.target.dataset.check) {
    e.target.checked
      ? checked.add(e.target.dataset.check)
      : checked.delete(e.target.dataset.check);
    render();
    return;
  }
  const row = e.target.closest("[data-item]");
  if (row) {
    selected = row.dataset.item;
    drawer = true;
    render();
    return;
  }
  if (b?.dataset.candidate) {
    await start([selected], b.dataset.candidate);
    return;
  }
  if (b && "browse" in b.dataset) {
    await browse(browseTarget, b.dataset.browse);
    return;
  }
  const cmd = b?.dataset.command;
  if (!cmd) return;
  switch (cmd) {
    case "close":
      $("dialog").close();
      break;
    case "close-inspector":
      drawer = false;
      render();
      break;
    case "settings":
      settings();
      break;
    case "manage-folders":
      await perform("manage-folders");
      break;
    case "disk":
      disk();
      break;
    case "add-folder":
      folderForm();
      break;
    case "add-client":
      clientForm();
      break;
    case "client-settings":
      $("dialog").close();
      await perform("settings");
      break;
    case "browse-folder":
      browseForm = null;
      await browse("#folder-form [name=local]");
      break;
    case "browse-root":
      browseForm = null;
      await browse("#root-form [name=path]");
      break;
    case "scan":
      {
        const folders = [
          ...document.querySelectorAll("[data-scan-folder]:checked"),
        ].map((n) => n.dataset.scanFolder);
        if (!folders.length) throw new Error("Choisissez au moins un dossier.");
        await perform("scan", { folders });
      }
      $("dialog").close();
      break;
    case "run":
      if (snapshot.running)
        await perform("pause", { paused: !snapshot.paused });
      else
        await start(
          visible()
            .filter((r) => r.stage !== "seeding")
            .map((r) => r.path),
        );
      break;
    case "run-item":
      await start([selected]);
      break;
    case "batch":
      await start([...checked]);
      break;
    case "select":
      visible().forEach((r) => checked.add(r.path));
      render();
      break;
    case "pause-item":
      await perform("pause-item", {
        paths: [selected],
        paused: !snapshot.progress?.paused_paths?.includes(selected),
      });
      break;
    case "stop":
      await perform("stop");
      break;
    case "review":
      await perform("review", { path: selected });
      break;
  }
  await refresh();
}
document.addEventListener("input", (e) => {
  if (e.target.id === "search") {
    query = e.target.value;
    limit = 100;
    const pos = e.target.selectionStart;
    e.target.blur();
    render();
    $("search").focus();
    $("search").setSelectionRange(pos, pos);
  }
});
document.addEventListener("submit", (e) => {
  e.preventDefault();
  submit(e).catch(() => {});
});
async function submit(e) {
  const body = Object.fromEntries(new FormData(e.target));
  if (e.target.id === "folder-form") {
    const scan = !!body.scan;
    delete body.scan;
    await perform("add-folder", { ...body, depth: null });
    if (scan) await perform("scan");
  } else if (e.target.id === "client-form") await perform("add-client", body);
  else if (e.target.id === "root-form") await perform("save-root", body);
  $("dialog").close();
  await refresh();
}
window.addEventListener("reseed-environment", (e) => {
  const value = e.detail;
  if (value.clear || value.client !== environment.client) {
    generation++;
    snapshot = { rows: [], setup: { folders: [] }, progress: {} };
    selected = "";
    checked.clear();
    $("dialog").close();
  }
  environment = value;
  active = !!value.active;
  render();
  if (active) refresh();
});
window.reseedTransport.then(() => window.reseedReady());
setInterval(refresh, 1200);

document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-item]")) {
    e.preventDefault();
    selected = e.target.dataset.item;
    drawer = true;
    render();
  }
});
