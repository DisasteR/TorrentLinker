"use strict";
// Same account journal, validation and scheduler as the native Community tab.
(() => {
  const root = $("zen-community-panel");
  $("zen-community-tab").textContent=say("Community", "Communauté");
  $("zen-community-tab").dataset.en="Community";
  $("zen-community-tab").dataset.fr="Communauté";
  let data = null, editing = null, library = [], signature = "", account = "", loading = false, lastLanguage = language;
  const kinds = ["comment", "vote", "chat"];
  const translations=new Map();
  const text = (en, fr) => {const value=say(en,fr);translations.set(value,[en,fr]);return value;};
  const names = () => ({comment: text("Comments", "Commentaires"), vote: text("Votes", "Votes"), chat: text("Chat messages", "Messages du chat")});
  const el = (tag, value, cls) => {const e=element(tag,value,cls);const pair=translations.get(value);
    if(pair){
      // Only translate the text node, never a container with timers or controls.
      const label=tag==="option"?e:element("span",value);
      label.dataset.en=pair[0];label.dataset.fr=pair[1];
      if(label!==e)e.replaceChildren(label);
    }return e;};
  const field = (label, input) => {const l = el("label"); l.append(el("span",label),input); return l;};
  const input = (type, id) => {const e=el("input"); e.type=type; e.id=id; return e;};
  const button = (en, fr, fn) => {const e=el("button", text(en,fr)); e.type="button"; e.onclick=()=>guard(fn); return e;};
  const notice = el("p"); notice.setAttribute("role", "status"); root.append(notice);
  const toolbar=el("div",undefined,"toolbar");
  const pause=button("Resume queue","Reprendre la file",async()=>{await change("settings",{...data.settings,enabled:!data.settings.enabled}); enabled.checked=data.settings.enabled;});
  toolbar.append(button("Refresh library","Actualiser la bibliothèque",()=>change("refresh",{page:1})), pause);
  root.append(toolbar);
  const quotas=el("p");root.append(quotas);
  const settings=el("details",undefined,"card");settings.id="community-settings";
  settings.append(el("summary",text("Settings & templates","Réglages et modèles")));
  const enabled=input("checkbox","community-enabled");
  settings.append(field(text("Run queued actions automatically","Exécuter automatiquement les actions en attente"),enabled));
  const limits={}, intervals={}, templates={};
  for(const kind of kinds){
    const group=el("fieldset"); group.append(el("legend",names()[kind]));
    const limit=limits[kind]=input("number",`community-${kind}-limit`); limit.min=0;limit.max=5;limit.step=1;
    const interval=intervals[kind]=input("number",`community-${kind}-interval`);interval.min=30;interval.max=86400;interval.step=1;
    for(const spin of [limit,interval]) spin.addEventListener("wheel",e=>e.preventDefault(),{passive:false});
    group.append(field(text("Maximum per day (0 disables)","Maximum par jour (0 désactive)"),limit),
      field(text("Minimum interval (seconds)","Intervalle minimum (secondes)"),interval));
    if(kind!=="vote") {const template=templates[kind]=el("textarea");template.maxLength=500;template.rows=2;
      group.append(field(text("Editable template","Modèle modifiable"),template));}
    settings.append(group);
  }
  settings.append(el("p",text("General and class chat share one limit. Site limits and slow mode also apply. Local sends are capped over a rolling 24 hours. Keep the app/server running and a signed-in c411 tab open.",
    "Les canaux général et de classe partagent une limite. Les limites du site et le mode lent s'appliquent aussi. Les envois locaux sont plafonnés sur 24 heures glissantes. Gardez l'application/serveur actif et un onglet c411 connecté ouvert.")));
  settings.append(button("Save settings","Enregistrer les réglages",()=>change("settings",{
    automation:automaticValues(), enabled:enabled.checked, limits:Object.fromEntries(kinds.map(k=>[k,Number(limits[k].value)])),
    intervals:Object.fromEntries(kinds.map(k=>[k,Number(intervals[k].value)])),
    templates:Object.fromEntries(Object.entries(templates).map(([k,v])=>[k,v.value])),
  })));
  root.append(settings);
  const autoFields = buildAutomaticSettings();
  const editor=el("section",undefined,"card");editor.setAttribute("aria-label",text("Draft editor","Éditeur de brouillon"));
  editor.append(el("h3",text("Prepare an action","Préparer une action")));
  const kind=el("select");kind.id="community-kind";
  for(const k of kinds){const o=el("option",names()[k]);o.value=k;kind.append(o);}
  const candidate=el("select");candidate.id="community-candidate";
  const subject=input("text","community-subject");subject.maxLength=40;
  const channel=el("select");channel.id="community-channel";
  const vote=el("select");vote.id="community-vote";
  for(const [value,en,fr] of [["","Choose your vote","Choisissez votre vote"],["approve","Approve","Approuver"],["disapprove","Disapprove","Désapprouver"]]){
    const o=el("option",text(en,fr));o.value=value;vote.append(o);}
  const content=el("textarea");content.id="community-content";content.maxLength=500;content.rows=3;
  const suggestions=el("select");suggestions.id="community-suggestion";
  const due=input("datetime-local","community-due");
  const grid=el("div",undefined,"community-form");
  grid.append(field(text("Action","Action"),kind),field(text("Seeded torrent","Torrent en seed"),candidate),
    field(text("Torrent hash","Hash du torrent"),subject),field(text("Chat channel","Canal du chat"),channel),
    field(text("Vote","Vote"),vote),field(text("Send after (local time)","Envoyer après (heure locale)"),due));
  editor.append(grid,field(text("Text / reason (500 characters)","Texte / raison (500 caractères)"),content),
    field(text("French suggestions","Suggestions en français"),suggestions));
  const editButtons=el("div",undefined,"toolbar");
  editButtons.append(button("New draft","Nouveau brouillon",()=>reset()),
    button("Use saved template","Utiliser le modèle",()=>{content.value=data?.settings.templates[kind.value]||"";}),
    button("Save draft","Enregistrer le brouillon",async()=>{
      const target=kind.value==="chat"?channel.value:subject.value.trim();
      const title=kind.value==="chat"?channel.selectedOptions[0]?.textContent:
        data?.candidates.find(c=>c.hash===target)?.title || target;
      const payload={kind:kind.value,subject:target,title,content:content.value,vote:vote.value,due:new Date(due.value).getTime()/1000};
      if(editing) Object.assign(payload,{id:editing.id,revision:editing.revision});
      await change("save",payload);reset();showPage("queue");
    }));
  editor.append(editButtons,el("p",text("Saving creates a draft. Queue it to schedule publication. When automatic votes are enabled, saved votes are scheduled using your choice and date.",
    "L'enregistrement crée un brouillon. Mettez-le en file pour programmer sa publication. Si les votes automatiques sont activés, vos votes enregistrés seront programmés selon votre choix et votre date.")));
  root.append(editor);
  const table=el("table");table.id="community-table";
  const head=el("thead"), heading=el("tr");
  for(const [en,fr] of [["Action / destination","Action / destination"],["Text","Texte"],["Status / timer","État / délai"],["Controls","Commandes"]]) heading.append(el("th",text(en,fr)));
  head.append(heading);const body=el("tbody");table.append(head,body);
  const wrap=el("div",undefined,"table-scroll");wrap.append(table);root.append(wrap);
  const pages = buildWorkspace();
  let activePage = "torrents", selectedHash = "", libraryLimit = 60;
  let calendarAnchor = new Date(), calendarScale = "week", selectedDay = dayKey(new Date());
  let historyMonth = new Date();
  showPage("torrents");
  const localDate=(stamp)=>{const d=new Date(stamp);d.setMinutes(d.getMinutes()-d.getTimezoneOffset());return d.toISOString().slice(0,16);};
  function reset(){editing=null;subject.value="";content.value="";vote.value="";due.value=localDate(Date.now());}
  function kindChanged(){const chat=kind.value==="chat";channel.disabled=!chat;suggestions.disabled=!chat;subject.disabled=chat;candidate.disabled=chat;vote.disabled=kind.value!=="vote";
    for(const e of [channel,suggestions]) e.parentElement.hidden=!chat;
    for(const e of [candidate,subject]) e.parentElement.hidden=chat;
    vote.parentElement.hidden=kind.value!=="vote";}
  kind.onchange=kindChanged; candidate.onchange=()=>{subject.value=candidate.value;};
  suggestions.onchange=()=>{const entry=library.find(x=>x.id===suggestions.value);if(entry)content.value=entry.text;};
  function options(select, rows, value, label){const previous=select.value;select.replaceChildren();
    const empty=el("option",text("Choose…","Choisir…"));empty.value="";select.append(empty);
    for(const r of rows){const o=el("option",r[label]);o.value=r[value];select.append(o);}select.value=previous;}
  async function change(operation,payload){data=await api(`community/${operation}`,payload);render();}
  function render(){
    if(!data)return;
    if(lastLanguage!==language){signature="";lastLanguage=language;}
    if(account!==data.account_id){clearAccountViews();account=data.account_id;reset();enabled.checked=data.settings.enabled;
      for(const k of kinds){limits[k].value=data.settings.limits[k];intervals[k].value=data.settings.intervals[k];}
      for(const k of Object.keys(templates))templates[k].value=data.settings.templates[k];
      loadAutomatic(data.settings.automation);
    }
    notice.textContent=data.refresh_pending?text("Waiting for the companion to import seeds and channels…","En attente de l'import par le compagnon…"):
      data.paired?text("Connected. Queued actions run when due.","Connecté. Les actions en file seront exécutées à leur échéance."):
      text("Connect the updated companion in Settings → Companion.","Connectez le compagnon mis à jour dans Réglages → Compagnon.");
    pause.textContent=data.settings.enabled?text("Pause queue","Suspendre la file"):text("Resume queue","Reprendre la file");
    quotas.replaceChildren(...kinds.map(k=>{const card=el("span",undefined,"community-quota");card.append(el("small",names()[k]),el("strong",`${data.quotas[k].site_used ?? data.quotas[k].local_used} / ${data.quotas[k].limit}`));card.title=`${text("Local attempts in 24 h","Tentatives locales sur 24 h")}: ${data.quotas[k].local_used}`;return card;}));
    const next=JSON.stringify([data.items.map(({next_at,...item})=>item),data.candidates,data.channels,data.settings]);
    if(next===signature){timers();return;}signature=next;
    options(candidate,data.candidates,"hash","title");options(channel,data.channels,"id","name");
    body.replaceChildren();
    const labels={draft:["Draft","Brouillon"],queued:["Queued","En attente"],paused:["Paused","Suspendu"],sending:["Sending","Envoi"],posted:["Posted","Publié"],uncertain:["Uncertain: check c411","Incertain : vérifiez c411"],refused:["Refused","Refusé"],cancelled:["Cancelled","Annulé"]};
    for(const item of data.items.filter(i=>!["posted","cancelled"].includes(i.state))){const tr=el("tr");tr.dataset.id=item.id;
      tr.append(el("td",`${names()[item.kind]} · ${item.title}`),el("td",item.content));
      const status=el("td",text(...labels[item.state]));
      const timer=el("div");timer.dataset.communityTimer=item.id;status.append(timer);
      if(item.note)status.append(el("small",item.note));
      if(item.state==="posted")status.append(el("div",item.reward==="pending_24h" && Date.now()/1000<item.reward_due?
        text("XP pending, 24 h","XP en attente, 24 h"):text("XP unconfirmed","XP non confirmée")));
      const controls=el("td");
      if(["draft","queued","paused","refused","cancelled"].includes(item.state)){
        controls.append(button("Edit","Modifier",async()=>{await change("control",{id:item.id,revision:item.revision,action:"pause"});editing={...data.items.find(i=>i.id===item.id)};kind.value=item.kind;kindChanged();subject.value=item.subject;channel.value=item.subject;vote.value=item.vote;content.value=item.content;due.value=localDate(item.due*1000);showPage("editor");}));
        for(const [action,en,fr] of [["queue","Queue","Mettre en file"],["pause","Pause","Suspendre"],["first","Move first","En premier"],["cancel","Cancel","Annuler"]])
          controls.append(button(en,fr,()=>change("control",{id:item.id,revision:item.revision,action})));
      }
      tr.append(status,controls);body.append(tr);
    }
    renderWorkspace();
    timers();
  }
  function timers(){for(const e of root.querySelectorAll("[data-community-timer]")){
    const item=data?.items.find(x=>x.id===e.dataset.communityTimer);if(!item)continue;
    const seconds=Math.max(0,Math.ceil((item.next_at||item.due)-Date.now()/1000));
    e.textContent=item.state==="queued"?(item.wait_reason|| (seconds?`${text("In","Dans")} ${Math.floor(seconds/3600)}h ${Math.floor(seconds%3600/60)}m ${seconds%60}s`:text("Due; waiting for companion","À échéance ; en attente du compagnon"))):new Date(item.due*1000).toLocaleString();
  }}
  function buildAutomaticSettings(){
    const box=el("section",undefined,"community-automation");
    box.append(el("h3",text("Automatic mode","Mode automatique")));
    const master=input("checkbox","community-auto-enabled");master.setAttribute("role","switch");
    box.append(field(text("Plan automatically","Planifier automatiquement"),master));
    const switches={};
    for(const [key,en,fr] of [["comment","Comments from my template","Commentaires depuis mon modèle"],["vote","My prepared votes","Mes votes préparés"],["chat","My selected chat messages","Mes messages sélectionnés"]]){
      const toggle=switches[key]=input("checkbox",`community-auto-${key}`);toggle.setAttribute("role","switch");
      box.append(field(text(en,fr),toggle));
    }
    const start=input("time","community-auto-start"),end=input("time","community-auto-end"),zone=input("text","community-auto-timezone");
    start.value="09:00";end.value="19:00";zone.value=Intl.DateTimeFormat().resolvedOptions().timeZone;
    const hours=el("div",undefined,"community-form");
    hours.append(field(text("Publication starts","Début des publications"),start),field(text("Publication ends","Fin des publications"),end),field(text("Timezone","Fuseau horaire"),zone));box.append(hours);
    const channels={};for(const [key,en,fr] of [["general","General channel","Canal général"],["class","Class channel","Canal de classe"]]){
      const toggle=channels[key]=input("checkbox",`community-auto-channel-${key}`);box.append(field(text(en,fr),toggle));
    }
    const messages=el("textarea");messages.id="community-auto-messages";messages.rows=5;
    box.append(field(text("Selected messages, one per line","Messages sélectionnés, un par ligne"),messages));
    box.append(button("Add current chat suggestion","Ajouter la suggestion du chat",()=>{
      const entry=library.find(x=>x.id===suggestions.value);if(entry)messages.value+=(messages.value?"\n":"")+entry.text.replace(/\s+/g," ");
    }));
    box.append(el("p",text("Plans seven days ahead using the limits above. Comments support {titre}. Only your prepared votes are scheduled. Disabling an action cancels its automatic slots and keeps manual operations.","Planifie sept jours à l'avance avec les limites ci-dessus. Les commentaires acceptent {titre}. Seuls vos votes préparés sont programmés. Désactiver une action annule ses créneaux automatiques et conserve les opérations manuelles.")));
    // Keep the save control after all settings.
    settings.insertBefore(box,settings.lastElementChild);
    return {master,switches,start,end,zone,channels,messages};
  }
  function automaticValues(){return {enabled:autoFields.master.checked,kinds:Object.fromEntries(kinds.map(k=>[k,autoFields.switches[k].checked])),start:autoFields.start.value,end:autoFields.end.value,timezone:autoFields.zone.value.trim(),channels:Object.keys(autoFields.channels).filter(k=>autoFields.channels[k].checked),messages:autoFields.messages.value.split("\n").map(s=>s.trim()).filter(Boolean)};}
  function loadAutomatic(config){
    if(!config)return;autoFields.master.checked=config.enabled;
    kinds.forEach(k=>autoFields.switches[k].checked=config.kinds[k]);
    autoFields.start.value=config.start;autoFields.end.value=config.end;autoFields.zone.value=config.timezone;
    Object.keys(autoFields.channels).forEach(k=>autoFields.channels[k].checked=config.channels.includes(k));
    autoFields.messages.value=config.messages.join("\n");
  }
  function buildWorkspace(){
    const heading=el("div",undefined,"community-heading");
    heading.append(el("h2",text("Every contribution counts.","Chaque contribution compte.")),el("p",text("Find your torrents, take part and keep track.","Retrouvez vos torrents, participez et gardez le fil.")));
    root.prepend(heading);
    const navigation=el("nav",undefined,"community-tabs");navigation.setAttribute("aria-label",text("Community views","Vues communauté"));
    const panes={};
    for(const [key,en,fr] of [["torrents","Torrents","Torrents"],["editor","Chat / draft","Chat / brouillon"],["queue","Waitlist","File d'attente"],["calendar","Calendar","Calendrier"],["history","History","Historique"],["settings","Settings / auto","Réglages / auto"]]){
      const b=button(en,fr,()=>showPage(key));b.dataset.communityPage=key;navigation.append(b);
      panes[key]=el("section",undefined,"community-page");panes[key].dataset.page=key;root.append(panes[key]);
    }
    root.insertBefore(navigation,notice);
    const statusbar=el("div",undefined,"community-statusbar");root.insertBefore(statusbar,notice);statusbar.append(notice,toolbar);heading.append(quotas);quotas.className="community-quotas";
    panes.editor.append(editor);panes.settings.append(settings);settings.open=true;
    panes.queue.append(wrap);
    const search=input("search","community-library-search");search.placeholder=text("Search torrents…","Rechercher un torrent…");search.oninput=()=>{libraryLimit=60;renderLibrary();};
    const filter=el("select");filter.id="community-library-filter";
    for(const [v,en,fr] of [["all","All torrents","Tous les torrents"],["available","No local action","Sans action locale"],["queued","In the waitlist","Dans la file"],["posted","Already contributed","Déjà contribué"]]){const o=el("option",text(en,fr));o.value=v;filter.append(o);}filter.onchange=()=>{libraryLimit=60;renderLibrary();};
    const filters=el("div",undefined,"toolbar");filters.append(search,filter);panes.torrents.append(filters);
    const split=el("div",undefined,"community-split"),list=el("div",undefined,"community-library"),detail=el("aside",undefined,"community-detail card");
    list.id="community-library";detail.id="community-detail";split.append(list,detail);panes.torrents.append(split);
    const more=el("div",text("Loading more…","Chargement…"));more.id="community-library-more";panes.torrents.append(more);
    new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)&&data&&libraryLimit<data.candidates.length){libraryLimit+=60;renderLibrary();}},{rootMargin:"200px"}).observe(more);
    const calendarTools=el("div",undefined,"toolbar");
    calendarTools.append(button("Previous period","Période précédente",()=>moveCalendar(-1)),button("Today","Aujourd'hui",()=>{calendarAnchor=new Date();selectedDay=dayKey(calendarAnchor);renderCalendar();}),button("Next period","Période suivante",()=>moveCalendar(1)));
    for(const [scale,en,fr] of [["week","Week","Semaine"],["month","Month","Mois"]])calendarTools.append(button(en,fr,()=>{calendarScale=scale;renderCalendar();}));
    const calendarFilter=el("select");calendarFilter.id="community-calendar-filter";fillKindFilter(calendarFilter);calendarFilter.onchange=renderCalendar;calendarTools.append(calendarFilter);
    panes.calendar.append(calendarTools);const calendar=el("div");calendar.id="community-calendar";panes.calendar.append(calendar);
    panes.calendar.append(el("p",text("Estimated local times. Site limits and companion availability may delay sending.","Heures locales estimées. Les limites du site et la disponibilité du compagnon peuvent retarder l'envoi.")));
    const historyTools=el("div",undefined,"toolbar");
    const historySearch=input("search","community-history-search");historySearch.placeholder=text("Search history…","Rechercher dans l'historique…");historySearch.oninput=renderHistory;
    const historyKind=el("select");historyKind.id="community-history-kind";fillKindFilter(historyKind);historyKind.onchange=renderHistory;
    const period=el("select");period.id="community-history-period";
    for(const [value,en,fr] of [["all","All time","Tout l'historique"],["1","Today","Aujourd'hui"],["7","7 days","7 jours"],["30","30 days","30 jours"],["custom","Custom dates","Dates personnalisées"]]){const o=el("option",text(en,fr));o.value=value;period.append(o);}period.onchange=renderHistory;
    const from=input("date","community-history-from"),to=input("date","community-history-to");from.setAttribute("aria-label",text("From date","Depuis le"));to.setAttribute("aria-label",text("Through date","Jusqu'au"));from.onchange=to.onchange=renderHistory;
    historyTools.append(historySearch,historyKind,period,from,to);panes.history.append(historyTools);
    const monthNav=el("div",undefined,"toolbar");monthNav.append(button("Previous month","Mois précédent",()=>{historyMonth.setMonth(historyMonth.getMonth()-1);renderHistory();}),button("Next month","Mois suivant",()=>{historyMonth.setMonth(historyMonth.getMonth()+1);renderHistory();}));
    panes.history.append(monthNav);const timeline=el("div");timeline.id="community-history-timeline";panes.history.append(timeline);
    const results=el("div");results.id="community-history-results";panes.history.append(results);
    return panes;
  }
  function fillKindFilter(select){const all=el("option",text("All actions","Toutes les actions"));all.value="";select.append(all);for(const k of kinds){const o=el("option",names()[k]);o.value=k;select.append(o);}}
  function showPage(key){activePage=key;for(const [k,pane] of Object.entries(pages))pane.hidden=k!==key;for(const b of root.querySelectorAll("[data-community-page]")){b.classList.toggle("active",b.dataset.communityPage===key);b.setAttribute("aria-current",b.dataset.communityPage===key?"page":"false");}if(key==="settings")settings.open=true;if(data)renderWorkspace();}
  function renderWorkspace(){renderLibrary();renderCalendar();renderHistory();}
  function renderLibrary(){
    if(!data)return;const list=$("community-library"),detail=$("community-detail"),q=$("community-library-search").value.toLocaleLowerCase(),filter=$("community-library-filter").value;
    const activity=new Map();for(const item of data.items){if(!activity.has(item.subject))activity.set(item.subject,[]);activity.get(item.subject).push(item);}
    const rows=data.candidates.filter(c=>{if(!(c.title+" "+c.hash).toLocaleLowerCase().includes(q))return false;const items=(activity.get(c.hash)||[]);return filter==="all"||filter==="available"&&!items.length||filter==="queued"&&items.some(i=>["draft","queued","paused","sending"].includes(i.state))||filter==="posted"&&items.some(i=>i.state==="posted");});
    list.replaceChildren();if(!rows.length)list.append(el("p",data.refresh_pending?text("Discovering your seeds…","Recherche de vos torrents en seed…"):text("No matching torrents.","Aucun torrent correspondant.")));
    for(const c of rows.slice(0,libraryLimit)){const b=el("button",undefined,"community-torrent");b.type="button";b.append(el("strong",c.title));const actions=(activity.get(c.hash)||[]);b.append(el("small",actions.length?actions.map(i=>`${names()[i.kind]} · ${stateName(i.state)}`).join(" / "):text("No local action yet","Aucune action locale")));b.onclick=()=>{selectedHash=c.hash;renderLibrary();};b.classList.toggle("active",c.hash===selectedHash);list.append(b);}
    $("community-library-more").hidden=rows.length<=libraryLimit;
    const c=rows.find(c=>c.hash===selectedHash)||rows[0];detail.replaceChildren();if(!c)return;selectedHash=c.hash;
    detail.append(el("h3",c.title),el("small",c.hash));
    for(const [k,en,fr] of [["comment","Write a comment","Écrire un commentaire"],["vote","Prepare a vote","Préparer un vote"]])detail.append(button(en,fr,()=>{reset();kind.value=k;kindChanged();subject.value=c.hash;candidate.value=c.hash;showPage("editor");}));
    const items=(activity.get(c.hash)||[]);detail.append(el("h4",text("Your activity","Votre activité")));
    if(!items.length)detail.append(el("p",text("No local action recorded. Eligibility is checked by c411 before sending.","Aucune action locale enregistrée. L'éligibilité est vérifiée par c411 avant l'envoi.")));
    for(const item of items)detail.append(operationCard(item));
  }
  function dayKey(date){return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}`;}
  function stateName(state){const values={draft:["Draft","Brouillon"],queued:["Queued","En attente"],paused:["Paused","Suspendu"],sending:["Sending","Envoi"],posted:["Posted","Publié"],uncertain:["Uncertain: check c411","Incertain : vérifiez c411"],refused:["Refused","Refusé"],cancelled:["Cancelled","Annulé"]};return text(...(values[state]||[state,state]));}
  function operationCard(item){const b=el("button",undefined,`community-operation ${item.kind}`);b.type="button";b.append(el("strong",item.title),el("span",`${names()[item.kind]} · ${stateName(item.state)}`),el("small",item.kind==="vote"?`${item.vote==="approve"?text("Approve","Approuver"):text("Disapprove","Désapprouver")} · ${item.content}`:item.content));b.onclick=()=>inspect(item);return b;}
  function moveCalendar(step){if(calendarScale==="month")calendarAnchor=new Date(calendarAnchor.getFullYear(),calendarAnchor.getMonth()+step,1);else calendarAnchor.setDate(calendarAnchor.getDate()+step*7);renderCalendar();}
  function renderCalendar(){
    if(!data)return;const target=$("community-calendar");target.replaceChildren();
    const start=new Date(calendarAnchor.getFullYear(),calendarAnchor.getMonth(),calendarScale==="month"?1:calendarAnchor.getDate());start.setDate(start.getDate()-(start.getDay()+6)%7);
    const count=calendarScale==="month"?42:7;target.append(el("h3",calendarAnchor.toLocaleDateString(language,{month:"long",year:"numeric"})));
    const grid=el("div",undefined,`community-calendar-grid ${calendarScale}`),filter=$("community-calendar-filter").value;
    const items=data.items.filter(i=>["queued","paused","sending"].includes(i.state)&&(!filter||i.kind===filter)).sort((a,b)=>(a.next_at||a.due)-(b.next_at||b.due));
    for(let offset=0;offset<count;offset++){const date=new Date(start);date.setDate(date.getDate()+offset);const key=dayKey(date);const cell=el("div",undefined,"community-calendar-day");cell.classList.toggle("today",key===dayKey(new Date()));cell.classList.toggle("selected",key===selectedDay);
      const label=el("button",date.toLocaleDateString(language,{weekday:"short",day:"numeric"}));label.type="button";label.onclick=()=>{selectedDay=key;renderCalendar();};cell.append(label);
      for(const item of items.filter(i=>dayKey(new Date((i.next_at||i.due)*1000))===key)){const b=el("button",undefined,`community-calendar-event ${item.kind}`);b.type="button";b.append(el("small",new Date((item.next_at||item.due)*1000).toLocaleTimeString(language,{hour:"2-digit",minute:"2-digit"})+" · "+(item.source==="automatic"?"AUTO":text("MANUAL","MANUEL"))),el("strong",item.title));b.onclick=()=>inspect(item);cell.append(b);}grid.append(cell);
    }target.append(grid,el("h3",new Date(selectedDay+"T12:00:00").toLocaleDateString(language,{dateStyle:"long"})));
    const selected=items.filter(i=>dayKey(new Date((i.next_at||i.due)*1000))===selectedDay);if(!selected.length)target.append(el("p",text("No scheduled operations.","Aucune opération programmée.")));for(const item of selected)target.append(operationCard(item));
  }
  function renderHistory(){
    if(!data)return;const search=$("community-history-search").value.toLocaleLowerCase(),filter=$("community-history-kind").value,period=$("community-history-period").value,from=$("community-history-from"),to=$("community-history-to");from.hidden=to.hidden=period!=="custom";
    const matched=data.items.filter(i=>(!filter||i.kind===filter)&&(i.title+" "+i.content+" "+i.note).toLocaleLowerCase().includes(search));
    const timeline=$("community-history-timeline");timeline.replaceChildren(el("h3",historyMonth.toLocaleDateString(language,{month:"long",year:"numeric"})));
    const bars=el("div",undefined,"community-timeline");const days=new Date(historyMonth.getFullYear(),historyMonth.getMonth()+1,0).getDate();
    for(let day=1;day<=days;day++){const key=dayKey(new Date(historyMonth.getFullYear(),historyMonth.getMonth(),day));const count=matched.filter(i=>dayKey(new Date((i.updated||i.created)*1000))===key).length;const b=el("button");b.type="button";b.title=`${key} · ${count}`;b.setAttribute("aria-label",`${key}: ${count}`);const bar=el("span",String(count||""));bar.style.height=`${Math.max(3,Math.min(70,count*12))}px`;b.append(bar,el("small",String(day)));b.onclick=()=>{from.value=to.value=key;$("community-history-period").value="custom";renderHistory();};bars.append(b);}timeline.append(bars);
    const today=new Date();today.setHours(0,0,0,0);const cutoff=new Date(today);if(Number(period))cutoff.setDate(cutoff.getDate()-Number(period)+1);
    const rows=matched.filter(i=>{const date=new Date((i.updated||i.created)*1000),key=dayKey(date);return period==="all"||period==="custom"&&(!from.value||key>=from.value)&&(!to.value||key<=to.value)||Number(period)>0&&date>=cutoff;}).sort((a,b)=>(b.updated||b.created)-(a.updated||a.created));
    const result=$("community-history-results");result.replaceChildren(el("p",`${rows.length} ${text("operations","opérations")}`));let last="";for(const item of rows){const day=dayKey(new Date((item.updated||item.created)*1000));if(day!==last){result.append(el("h3",day));last=day;}result.append(operationCard(item));}
  }
  function inspect(item){
    const dialog=el("dialog",undefined,"community-dialog");dialog.append(el("h2",item.title),el("p",`${names()[item.kind]} · ${stateName(item.state)}`),el("p",item.content),el("small",item.subject));
    if(item.kind==="vote")dialog.append(el("p",item.vote==="approve"?text("Approve","Approuver"):text("Disapprove","Désapprouver")));
    if(item.note)dialog.append(el("p",item.note));
    if(item.state==="posted")dialog.append(el("p",item.reward==="pending_24h"&&Date.now()/1000<item.reward_due?text("XP pending, 24 h","XP en attente, 24 h"):text("XP unconfirmed","XP non confirmée")));
    dialog.append(el("h3",text("Operation timeline","Chronologie de l'opération")));
    for(const event of item.events||[]){const block=el("section",undefined,"community-event");block.append(el("strong",new Date(event.time*1000).toLocaleString()+" · "+stateName(event.state)),el("p",event.vote?`${event.vote==="approve"?text("Approve","Approuver"):text("Disapprove","Désapprouver")} · ${event.content}`:event.content),el("small",event.note));dialog.append(block);}
    if(["draft","queued","paused","refused","cancelled"].includes(item.state)){
      dialog.append(button("Edit / reschedule","Modifier / reprogrammer",async()=>{await change("control",{id:item.id,revision:item.revision,action:"pause"});editing={...data.items.find(i=>i.id===item.id)};kind.value=item.kind;kindChanged();subject.value=item.subject;channel.value=item.subject;vote.value=item.vote;content.value=item.content;due.value=localDate(item.due*1000);dialog.close();showPage("editor");}));
      for(const [action,en,fr] of [["queue","Queue","Mettre en file"],["pause","Pause","Suspendre"],["cancel","Cancel","Annuler"]])dialog.append(button(en,fr,async()=>{await change("control",{id:item.id,revision:item.revision,action});dialog.close();}));
    }
    dialog.append(button("Close","Fermer",()=>dialog.close()));dialog.addEventListener("close",()=>dialog.remove());root.append(dialog);dialog.showModal();
  }
  function clearAccountViews(){
    for(const dialog of root.querySelectorAll("dialog"))dialog.close();
    for(const id of ["community-library","community-detail","community-calendar","community-history-timeline","community-history-results"]){$(id).replaceChildren();}
    selectedHash="";libraryLimit=60;quotas.textContent="";
    autoFields.master.checked=false;enabled.checked=false;autoFields.messages.value="";
    Object.values(autoFields.switches).forEach(e=>e.checked=false);
  }
  async function refresh(){if(loading||root.hidden||$("workspace").hidden)return;loading=true;
    try{data=await api("community");render();if(!library.length){library=(await api("community/messages")).entries;options(suggestions,library,"id","text");}}
    catch(e){notice.textContent=e.message;
      if(e.message==="Refresh the c411 profile first." || $("workspace").hidden){
        data=null;account="";signature="";editing=null;clearAccountViews();body.replaceChildren();candidate.replaceChildren();channel.replaceChildren();reset();
        for(const e of Object.values(templates))e.value="";
      }
    }finally{loading=false;}}
  document.addEventListener("tl-language-changed",()=>{signature="";render();});
  new MutationObserver(()=>{if($("workspace").hidden){data=null;account="";signature="";editing=null;clearAccountViews();body.replaceChildren();candidate.replaceChildren();channel.replaceChildren();reset();}}).observe($("workspace"),{attributes:true,attributeFilter:["hidden"]});
  new MutationObserver(()=>refresh()).observe(root,{attributes:true,attributeFilter:["hidden"]});
  reset();kindChanged();setInterval(refresh,3000);setInterval(timers,1000);
})();
