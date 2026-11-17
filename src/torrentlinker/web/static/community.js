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
  toolbar.append(button("Import seeds & channels","Importer seeds et canaux",()=>change("refresh",{page:1})),
    button("Next seeds page","Page de seeds suivante",()=>change("refresh",{page:(data?.page||1)+1})), pause);
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
    enabled:enabled.checked, limits:Object.fromEntries(kinds.map(k=>[k,Number(limits[k].value)])),
    intervals:Object.fromEntries(kinds.map(k=>[k,Number(intervals[k].value)])),
    templates:Object.fromEntries(Object.entries(templates).map(([k,v])=>[k,v.value])),
  })));
  root.append(settings);
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
      await change("save",payload);reset();
    }));
  editor.append(editButtons,el("p",text("Saving creates a draft. Queue it below to schedule publication. Editing returns a queued item to draft.",
    "L'enregistrement crée un brouillon. Ajoutez-le à la file ci-dessous pour programmer sa publication. Une modification remet l'élément en brouillon.")));
  root.append(editor,el("h3",text("Waitlist & history","File d'attente et historique")));
  const table=el("table");table.id="community-table";
  const head=el("thead"), heading=el("tr");
  for(const [en,fr] of [["Action / destination","Action / destination"],["Text","Texte"],["Status / timer","État / délai"],["Controls","Commandes"]]) heading.append(el("th",text(en,fr)));
  head.append(heading);const body=el("tbody");table.append(head,body);
  const wrap=el("div",undefined,"table-scroll");wrap.append(table);root.append(wrap);
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
    if(account!==data.account_id){account=data.account_id;reset();enabled.checked=data.settings.enabled;
      for(const k of kinds){limits[k].value=data.settings.limits[k];intervals[k].value=data.settings.intervals[k];}
      for(const k of Object.keys(templates))templates[k].value=data.settings.templates[k];}
    notice.textContent=data.refresh_pending?text("Waiting for the companion to import seeds and channels…","En attente de l'import par le compagnon…"):
      data.paired?text("Connected. Queued actions run when due.","Connecté. Les actions en file seront exécutées à leur échéance."):
      text("Connect the updated companion in Settings → Companion.","Connectez le compagnon mis à jour dans Réglages → Compagnon.");
    pause.textContent=data.settings.enabled?text("Pause queue","Suspendre la file"):text("Resume queue","Reprendre la file");
    quotas.textContent=kinds.map(k=>`${names()[k]}: ${data.quotas[k].local_used}/${data.quotas[k].limit} ${text("local","local")} · ${text("site today","site aujourd'hui")}: ${data.quotas[k].site_used ?? "?"}`).join(" | ");
    const next=JSON.stringify([data.items,data.candidates,data.channels]);
    if(next===signature){timers();return;}signature=next;
    options(candidate,data.candidates,"hash","title");options(channel,data.channels,"id","name");
    body.replaceChildren();
    const labels={draft:["Draft","Brouillon"],queued:["Queued","En attente"],paused:["Paused","Suspendu"],sending:["Sending","Envoi"],posted:["Posted","Publié"],uncertain:["Uncertain: check c411","Incertain : vérifiez c411"],refused:["Refused","Refusé"],cancelled:["Cancelled","Annulé"]};
    for(const item of data.items){const tr=el("tr");tr.dataset.id=item.id;
      tr.append(el("td",`${names()[item.kind]} · ${item.title}`),el("td",item.content));
      const status=el("td",text(...labels[item.state]));
      const timer=el("div");timer.dataset.communityTimer=item.id;status.append(timer);
      if(item.note)status.append(el("small",item.note));
      if(item.state==="posted")status.append(el("div",item.reward==="pending_24h" && Date.now()/1000<item.reward_due?
        text("XP pending, 24 h","XP en attente, 24 h"):text("XP unconfirmed","XP non confirmée")));
      const controls=el("td");
      if(["draft","queued","paused","refused","cancelled"].includes(item.state)){
        controls.append(button("Edit","Modifier",async()=>{await change("control",{id:item.id,revision:item.revision,action:"pause"});editing={...data.items.find(i=>i.id===item.id)};kind.value=item.kind;kindChanged();subject.value=item.subject;channel.value=item.subject;vote.value=item.vote;content.value=item.content;due.value=localDate(item.due*1000);editor.scrollIntoView({block:"nearest"});}));
        for(const [action,en,fr] of [["queue","Queue","Mettre en file"],["pause","Pause","Suspendre"],["first","Move first","En premier"],["cancel","Cancel","Annuler"]])
          controls.append(button(en,fr,()=>change("control",{id:item.id,revision:item.revision,action})));
      }
      tr.append(status,controls);body.append(tr);
    }
    timers();
  }
  function timers(){for(const e of root.querySelectorAll("[data-community-timer]")){
    const item=data?.items.find(x=>x.id===e.dataset.communityTimer);if(!item)continue;
    const seconds=Math.max(0,Math.ceil((item.next_at||item.due)-Date.now()/1000));
    e.textContent=item.state==="queued"?(item.wait_reason|| (seconds?`${text("In","Dans")} ${Math.floor(seconds/3600)}h ${Math.floor(seconds%3600/60)}m ${seconds%60}s`:text("Due; waiting for companion","À échéance ; en attente du compagnon"))):new Date(item.due*1000).toLocaleString();
  }}
  async function refresh(){if(loading||root.hidden||$("workspace").hidden)return;loading=true;
    try{data=await api("community");render();if(!library.length){library=(await api("community/messages")).entries;options(suggestions,library,"id","text");}}
    catch(e){notice.textContent=e.message;
      if(e.message==="Refresh the c411 profile first." || $("workspace").hidden){
        data=null;account="";signature="";editing=null;body.replaceChildren();candidate.replaceChildren();channel.replaceChildren();reset();
        for(const e of Object.values(templates))e.value="";
      }
    }finally{loading=false;}}
  document.addEventListener("tl-language-changed",()=>{signature="";render();});
  reset();kindChanged();setInterval(refresh,3000);setInterval(timers,1000);
})();
