"use strict";
const reseedUI = {rows:[], root:"", selected:new Set(), client:null, job:null, paused:false, activePath:"", generation:0, limits:{}};
const reseedStages = () => [
  ["find",say("To find","À retrouver"),say("Search for compatible torrents","Chercher des torrents compatibles")],
  ["candidates",say("Candidates found","Candidats trouvés"),say("Compare files before proceeding","Comparer les fichiers avant de continuer")],
  ["links",say("Links to prepare","Liens à préparer"),say("Expected names, originals preserved","Les bons noms, originaux conservés")],
  ["verify",say("Verification","Vérification"),say("Check the bytes before seeding","Vérifier le contenu avant de seeder")],
  ["incomplete",say("Needs attention","À compléter"),say("Review missing files and archives","Examiner les fichiers manquants et archives")],
  ["seeding",say("Seeding","En partage"),say("Keep the links while seeding","Conserver les liens pendant le seed")],
];
const reseedNext = row => ({find:"find",candidates:"compare",links:"prepare",verify:row.status==="verified"?"seed":"verify"}[row.stage]);
const reseedAction = step => ({all:say("Run all steps","Tout enchaîner"),find:say("Find candidates","Chercher les candidats"),compare:say("Compare","Comparer"),prepare:say("Prepare links","Préparer les liens"),verify:say("Verify","Vérifier"),seed:say("Start seeding","Partager")}[step]);
const reseedVisible = () => reseedUI.rows.filter(r=>r.name.toLocaleLowerCase().includes($("reseed-query").value.toLocaleLowerCase()));

async function loadReseed() {
  const generation=++reseedUI.generation, client=state.clientId, previousJob=reseedUI.job;
  const [response,jobs]=await Promise.all([api("reseed-workspace"),api("jobs")]);
  if(generation!==reseedUI.generation || client!==state.clientId) return;
  if(reseedUI.client!==client) {
    reseedUI.selected.clear(); reseedUI.client=client;
  }
  reseedUI.root=response.link_root || localStorage.getItem(`reseed-root:${client}`) || "";
  reseedUI.rows=response.rows;
  const pending=jobs.filter(j=>j.kind==="reseed-workspace" && j.client_id===client);
  const running=pending.find(j=>j.status==="running") || pending.find(j=>j.status==="queued");
  reseedUI.job=running?.id||null;
  if(running) reseedProgress(running.progress||{});
  else if(previousJob && previousJob!=="submitting") {
    const finished=await api(`jobs/${previousJob}`);
    if(generation!==reseedUI.generation || client!==state.clientId) return;
    reseedFinished(finished.result,finished.status);
  }
  renderReseed();
}
function reseedFinished(result,status="completed") {
  reseedUI.job=null;reseedUI.activePath="";
  $("reseed-run-buttons").hidden=true;
  $("reseed-progress-text").textContent=status==="completed" && result
    ?say(`Finished · ${result.seeded} seeded · ${result.errors.length} error(s)`,`Terminé · ${result.seeded} en partage · ${result.errors.length} erreur(s)`)
    :say("Run stopped. Completed steps are kept.","Parcours arrêté. Les étapes terminées sont conservées.");
  if(result){$("reseed-meter").max=Math.max(1,result.total);$("reseed-meter").value=result.done;}
  $("reseed-current").textContent="";
}
function renderReseed() {
  const board=$("reseed-board");
  const before=new Map([...board.querySelectorAll("[data-path]")].map(n=>[n.dataset.path,n.getBoundingClientRect()]));
  const focused=document.activeElement?.dataset;
  board.replaceChildren();
  const rows=reseedVisible();
  for(const [stage,title,hint] of reseedStages()) {
    const lane=element("section",undefined,"reseed-lane"); lane.dataset.stage=stage;lane.setAttribute("aria-label",title);
    const entries=rows.filter(r=>r.stage===stage), heading=element("h3",title);
    heading.append(element("span",String(entries.length)));lane.append(heading,element("p",hint));
    const limit=reseedUI.limits[stage]||50;
    for(const row of entries.slice(0,limit)) {
      const card=element("article",undefined,`reseed-card${reseedUI.job && reseedUI.activePath===row.path?" is-active":""}`);card.dataset.path=row.path;
      const label=element("label"), check=document.createElement("input");check.type="checkbox";check.checked=reseedUI.selected.has(row.path);check.disabled=!!reseedUI.job || stage==="seeding";check.dataset.selectPath=row.path;
      check.onchange=()=>{check.checked?reseedUI.selected.add(row.path):reseedUI.selected.delete(row.path);reseedSelection();};
      label.append(check,document.createTextNode(row.name));card.append(label,element("p",`${bytes(row.size)}${row.status==="verified"?say(" · Verified"," · Vérifié"):""}`));
      if(row.error)card.append(element("p",row.error,"reseed-error"));
      const inspect=button(say("Inspect","Examiner"),()=>inspectReseed(row));inspect.dataset.inspectPath=row.path;card.append(inspect);lane.append(card);
    }
    if(entries.length>limit)lane.append(button(say("Show more","Afficher la suite"),()=>{reseedUI.limits[stage]=limit+50;renderReseed();}));
    if(!entries.length)lane.append(element("p",say("Nothing here yet","Rien ici pour l’instant"),"muted"));
    board.append(lane);
  }
  if(!matchMedia("(prefers-reduced-motion: reduce)").matches) for(const card of board.querySelectorAll("[data-path]")) {
    const old=before.get(card.dataset.path), now=card.getBoundingClientRect();
    if(old && (old.x!==now.x || old.y!==now.y))card.animate([{transform:`translate(${old.x-now.x}px,${old.y-now.y}px)`},{transform:"none"}],{duration:450,easing:"ease-out"});
  }
  if(focused?.selectPath || focused?.inspectPath) {
    const attr=focused.selectPath?"selectPath":"inspectPath", value=focused[attr];
    [...board.querySelectorAll("input,button")].find(n=>n.dataset[attr]===value)?.focus({preventScroll:true});
  }
  $("reseed-empty").hidden=!!reseedUI.rows.length;
  $("reseed-run-all").disabled=!!reseedUI.job || !rows.some(r=>!["seeding","incomplete"].includes(r.stage));
  $("reseed-refresh").disabled=!!reseedUI.job;$("reseed-scan").disabled=!!reseedUI.job;
  $("reseed-settings").disabled=!!reseedUI.job;
  $("reseed-root-status").textContent=reseedUI.root?say("Link folder configured","Dossier de liens configuré"):say("Choose a link folder in Client settings","Choisissez un dossier de liens dans les paramètres du client");
  $("reseed-root-status").title=reseedUI.root;reseedSelection();
}
function reseedSelection() {
  $("reseed-selection").textContent=say(`${reseedUI.selected.size} selected`,`${reseedUI.selected.size} sélectionné(s)`);
  $("reseed-run-selected").disabled=!!reseedUI.job || !reseedUI.selected.size;
}
function inspectReseed(row) {
  const dialog=$("reseed-details"), content=$("reseed-detail-content");content.replaceChildren(element("h2",row.name),element("p",row.path));
  if(row.destination)content.append(element("h3",say("Persistent link folder","Dossier de liens à conserver")),element("pre",row.destination));
  if(row.files)for(const file of row.files)content.append(element("pre",`${file.source}\n↳ ${file.name}\n→ ${file.target}`));
  if(row.error)content.append(element("p",row.error,"error"));
  for(const candidate of row.candidates||[]) {
    content.append(element("h3",candidate.title),element("p",libraryMatch(candidate.match)));
    for(const f of candidate.files||[])content.append(element("pre",`${f.local||say("Missing","Manquant")}\n↳ ${f.torrent}`));
    if(row.stage==="candidates" && candidate.match==="full" && !candidate.archive) {
      const choose=button(say("Use this Candidate","Utiliser ce candidat"),()=>{dialog.close();return startReseed([row.path],"compare",candidate.key);});choose.disabled=!!reseedUI.job;content.append(choose);
    }
  }
  const actions=element("div",undefined,"actions"), next=reseedNext(row);
  if(next) {const go=button(reseedAction(next),()=>{dialog.close();return confirmReseed([row.path],next);});go.disabled=!!reseedUI.job;actions.append(go);}
  if(row.error && row.stage!=="seeding") {const retry=button(say("Compare again","Comparer à nouveau"),()=>{dialog.close();return startReseed([row.path],"compare");});retry.disabled=!!reseedUI.job;actions.append(retry);}
  if(row.stage==="incomplete")actions.append(button(say("Review Local Item","Examiner l’élément local"),async()=>{dialog.close();await navigate("library","items");await reviewLibraryItem(row);}));
  actions.append(button(say("Close","Fermer"),()=>dialog.close()));content.append(actions);dialog.showModal();
}
function confirmReseed(paths,action) {
  const chosen=paths.filter(p=>reseedUI.rows.some(r=>r.path===p && !["seeding","incomplete"].includes(r.stage)));
  if(!chosen.length)return;
  if(["prepare","all"].includes(action) && !reseedUI.root) {guard(openReseedSettings);toast(say("Choose a link folder in Client settings","Choisissez un dossier de liens dans les paramètres du client"));return;}
  const dialog=$("reseed-details"), content=$("reseed-detail-content");
  content.replaceChildren(element("h2",reseedAction(action)),element("p",say(`${chosen.length} Local Item(s). Originals keep their names. Keep the link folder while seeding.`,`${chosen.length} élément(s). Les originaux gardent leur nom. Conservez le dossier de liens pendant le seed.`)),element("pre",reseedUI.root));
  if(action==="all")content.append(element("p",say("Find → Compare → Prepare links → Verify → Seed. Incomplete or ambiguous matches stop for review. Pause takes effect between operations; a client hash check may finish while paused.","Chercher → Comparer → Préparer les liens → Vérifier → Partager. Les correspondances incomplètes ou ambiguës attendent votre examen. La pause prend effet entre les opérations ; une vérification du client peut se terminer pendant la pause.")));
  const actions=element("div",undefined,"actions");actions.append(button(say("Cancel","Annuler"),()=>dialog.close()),button(reseedAction(action),()=>{dialog.close();return startReseed(chosen,action);}));content.append(actions);dialog.showModal();
}
async function startReseed(paths,action,key="") {
  const root=reseedUI.root;
  reseedUI.job="submitting";reseedUI.paused=false;renderReseed();
  try {
    reseedUI.job=await job({kind:"reseed-workspace",paths,reseed_action:action,link_root:root,candidate_key:key},async result=>{
      reseedFinished(result);reseedUI.rows=result.rows;reseedUI.selected.clear();renderReseed();
    },async()=>{reseedUI.job=null;$("reseed-progress-text").textContent=say("Run stopped. Completed steps are kept.","Parcours arrêté. Les étapes terminées sont conservées.");$("reseed-run-buttons").hidden=true;await loadReseed();},reseedProgress);
  } catch(error) {reseedUI.job=null;renderReseed();throw error;}
}
function reseedProgress(p) {
  reseedUI.activePath=p.path||"";reseedUI.paused=!!p.paused;
  $("reseed-progress").hidden=false;$("reseed-run-buttons").hidden=false;
  const phases={"local-hash":say("Checking original files","Vérification des originaux"),"client-hash":say("Client verification","Vérification du client"),"item-complete":say("Item processed","Élément traité")};
  $("reseed-progress-text").textContent=`${reseedUI.paused?say("Paused · ","En pause · "):""}${phases[p.phase]||reseedAction(p.phase)||say("Queued","En attente")} · ${p.done||0}/${p.total||0}${p.fraction!=null?` · ${Math.round(p.fraction*100)} %`:""}`;
  $("reseed-current").textContent=p.path||"";
  $("reseed-meter").max=Math.max(1,p.total||1);$("reseed-meter").value=p.done||0;
  $("reseed-pause").textContent=reseedUI.paused?say("Resume","Reprendre"):say("Pause","Pause");
  if(p.rows)reseedUI.rows=p.rows;renderReseed();
}
$("reseed-query").oninput=renderReseed;
$("reseed-run-all").onclick=()=>confirmReseed(reseedVisible().map(r=>r.path),"all");
$("reseed-run-selected").onclick=()=>confirmReseed([...reseedUI.selected],"all");
$("reseed-select-visible").onclick=()=>{for(const r of reseedVisible())if(!["seeding","incomplete"].includes(r.stage))reseedUI.selected.add(r.path);renderReseed();};
$("reseed-pause").onclick=()=>guard(async()=>{await api(`jobs/${reseedUI.job}/pause-reseed?paused=${!reseedUI.paused}`,{});await loadReseed();});
$("reseed-stop").onclick=()=>guard(()=>api(`jobs/${reseedUI.job}/cancel`,{}));
$("reseed-refresh").onclick=()=>guard(()=>job({kind:"reseed-workspace",reseed_action:"refresh"},loadReseed));
$("reseed-scan").onclick=()=>guard(()=>job({kind:"scan"},loadReseed));

$("reseed-browse").onclick=()=>guard(()=>browseLibraryFolder("","reseed-root"));

async function openReseedSettings() {
  await navigate("setup", "connections");
  $("reseed-folder-settings").open=true;
  $("reseed-root").focus();
}
$("reseed-settings").onclick=()=>guard(openReseedSettings);
$("reseed-folder-form").onsubmit=event=>{
  event.preventDefault();
  guard(async()=>{
    const client=state.clientId;
    const result=await api("setup/reseed-folder",{path:$("reseed-root").value.trim()});
    localStorage.removeItem(`reseed-root:${client}`);
    if(client!==state.clientId)return;
    state.setup.reseed_link_root=result.reseed_link_root;
    reseedUI.root=result.reseed_link_root;
    $("reseed-root").value=result.reseed_link_root;
    toast(say("Link folder saved for this client.","Dossier de liens enregistré pour ce client."));
  });
};
