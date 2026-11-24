"use strict";

const $ = id => document.getElementById(id);
const days = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"];
const groupNames = {
  offtime: "Offtime", bedtime: "Bedtime", quotas: "Quote per giorno",
  web: "Protezione contenuti", services: "Servizi e applicazioni"
};
const reasons = {
  manuale: "Blocco manuale", manuale_dispositivo: "Blocco del dispositivo",
  offtime: "Offtime", bedtime: "Bedtime"
};
let state;
let reportRequest = 0;
let viewRequest = 0;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = String(text);
  if (className) element.className = className;
  return element;
}

function add(parent, ...children) {
  parent.append(...children);
  return parent;
}

function button(label, click, className = "subtle") {
  const element = node("button", label, className);
  element.type = "button";
  element.onclick = async () => {
    element.disabled = true;
    try { await click(); } finally { element.disabled = false; }
  };
  return element;
}

function submit(label, className) {
  return node("button", label, className);
}

function select(choices, value) {
  const element = node("select");
  for (const [key, label] of choices) {
    const option = node("option", label);
    option.value = key;
    element.append(option);
  }
  element.value = value ?? "";
  return element;
}

function input(type, value = "") {
  const element = node("input");
  element.type = type;
  element.value = value;
  return element;
}

function field(label, control, className) {
  return add(node("label", label, className), control);
}

function checkbox(label, checked = false) {
  const element = input("checkbox");
  element.checked = checked;
  return {element, label: add(node("label"), element, node("span", label))};
}

function panel(title) {
  return add(node("section", undefined, "panel stack"), node("h2", title));
}

function details(title, ...contents) {
  return add(node("details"), node("summary", title), ...contents);
}

function message(text, error = false) {
  $("message").textContent = text;
  $("message").className = error ? "error" : "success";
}

async function api(path, method = "GET", data) {
  const options = {method, headers: {"X-Family-Action": "1"}};
  if (data !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(data);
  }
  const response = await fetch("api/" + path, options);
  const result = await response.json().catch(() => ({detail: "Risposta non disponibile."}));
  if (!response.ok) {
    const detail = result.detail;
    throw new Error(typeof detail === "string" ? detail : "Controlla i campi del modulo e riprova.");
  }
  return result;
}

async function save(path, method, data, confirmation = "Modifica salvata.") {
  try {
    const result = await api(path, method, data);
    const failed = (result.actions || []).some(action => action.status === "errore");
    message(failed ? "Comando fallito. Controlla il registro." : confirmation, failed);
    await refresh();
    return true;
  } catch (error) {
    message(error.message, true);
    return false;
  }
}

function clock(value, options = {dateStyle: "short", timeStyle: "short"}) {
  return value && new Intl.DateTimeFormat("it-IT", {...options, timeZone: state.timezone}).format(new Date(value));
}

function showSpeed(value) {
  return value == null ? "n/d" : value + " KB/s";
}

function actionLabel(status) {
  if (["inviato_non_verificato", "accettato_da_ha_non_verificato"].includes(status))
    return "Comando accettato · da verificare";
  return status;
}

const deviceLabel = device => device.alias || device.name;
const deviceMatches = (device, term) => [device.alias, device.name, device.mac, device.ip, ...device.user_names]
  .some(value => value?.toLocaleLowerCase("it").includes(term));

function deviceLink(device) {
  const link = node("a", deviceLabel(device));
  link.href = "#statistiche";
  link.onclick = () => {
    $("report-user").value = "";
    filterReportDevices();
    $("report-device").value = device.mac;
    loadReport();
  };
  return link;
}

function deviceSettings(device) {
  const path = "devices/" + encodeURIComponent(device.mac);
  const alias = input("text", device.alias);
  alias.maxLength = 80;
  const aliasForm = add(node("form", undefined, "controls"), field("Alias", alias), submit("Salva alias"));
  aliasForm.onsubmit = event => {
    event.preventDefault();
    save(path + "/alias", "PUT", {alias: alias.value});
  };

  const people = state.users.map(user => ({id: user.id, ...checkbox(user.name, device.user_ids.includes(user.id))}));
  const settings = details(deviceLabel(device), aliasForm);
  settings.className = "device-settings";
  if (!people.length) return settings;
  const profiles = add(node("fieldset"), node("legend", "Profili associati"),
    add(node("div", undefined, "checks"), ...people.map(person => person.label)));
  const profilesForm = add(node("form", undefined, "stack"), profiles, submit("Salva profili"));
  profilesForm.onsubmit = event => {
    event.preventDefault();
    const user_ids = people.filter(person => person.element.checked).map(person => person.id);
    save(path + "/profiles", "PUT", {user_ids});
  };
  settings.append(profilesForm);
  return settings;
}

function deviceRow(device, user = null) {
  const fresh = state.last_poll && !state.last_error && device.in_latest_snapshot;
  const availability = fresh ? (device.online ? "Online" : "Offline") : "Stato non aggiornato";
  const protectedDevice = state.protected_macs.includes(device.mac);
  const types = {phone: "Telefono", tablet: "Tablet", computer: "Computer", unknown: "Tipo sconosciuto"};
  const identity = add(node("div"), deviceSettings(device),
    node("small", [device.alias ? device.name : "", types[device.client_type] || device.client_type,
      device.ip, device.mac, availability].filter(Boolean).join(" · ")));
  if (device.user_names.length) identity.append(node("small", "Profili: " + device.user_names.join(", ")));
  if (device.last_online) identity.append(node("small", "Ultima presenza online " + clock(device.last_online)));
  if (user && !protectedDevice) {
    const preview = node("small");
    preview.dataset.previewMac = device.mac;
    identity.append(preview);
  }
  if (fresh && (device.down_kbs !== null || device.up_kbs !== null)) {
    identity.append(node("small", "Ultimo campione ↓ " + showSpeed(device.down_kbs) + " · ↑ " + showSpeed(device.up_kbs)));
  }
  if (device.last_action && !state.inventory_only) identity.append(node("small", actionLabel(device.last_action)));

  const statistics = deviceLink(device);
  statistics.textContent = "Statistiche";
  statistics.setAttribute("aria-label", "Statistiche di " + deviceLabel(device));
  const controls = add(node("div", undefined, "controls"), statistics);
  if (protectedDevice) controls.append(node("span", "Protetto", "flag safe"));
  if (user && !protectedDevice) {
    const exempt = device.profile_exemptions.includes(user.id);
    const label = exempt ? "Ripristina regole del profilo" : "Escludi dalle regole del profilo";
    controls.append(button(label, () => save("users/" + user.id + "/devices/" + device.mac + "/exception",
      "PUT", {exempt: !exempt})));
  }
  if (!state.inventory_only && !protectedDevice) {
    const label = device.manually_blocked ? "Revoca blocco dispositivo" : "Blocca dispositivo";
    controls.append(button(label, () => save("manual-block", "POST", {
      mac: device.mac, blocked: !device.manually_blocked
    }), device.manually_blocked ? "subtle" : "danger"));
  }
  return add(node("div", undefined, "item"), identity, controls);
}

function renderInventory() {
  const term = $("device-filter").value.trim().toLocaleLowerCase("it");
  const matches = device => deviceMatches(device, term);
  const current = Boolean(state.last_poll && !state.last_error);
  const latest = state.devices.filter(device => current && device.in_latest_snapshot);
  const previous = state.devices.filter(device => !current || !device.in_latest_snapshot);
  const time = clock(state.last_poll);
  $("inventory-status").textContent = [
    current ? "Ultima lettura " + time + " · " + latest.length + " dispositivi · " +
      latest.filter(device => device.online).length + " online" : state.last_error || "In attesa della prima lettura.",
    !current && time ? "Ultima lettura riuscita " + time : "",
    state.inventory_setup_required ? "Completa la configurazione dell’app in Home Assistant e riavvia." : "",
    state.ha_ingress && state.mode === "mock" ? "Attiva l’inventario nelle opzioni dell’app per leggere il Deco." : ""
  ].filter(Boolean).join(" · ");

  const assigned = latest.filter(device => device.user_ids.length > 0 && matches(device));
  const unassigned = latest.filter(device => !device.user_ids.length && matches(device));
  $("devices").replaceChildren(...assigned.map(device => deviceRow(device)));
  $("unassigned").replaceChildren(...unassigned.map(device => deviceRow(device)));
  if (!assigned.length) $("devices").append(node("p", "Nessun dispositivo assegnato nell’ultima lettura.", "notice"));
  if (!unassigned.length) $("unassigned").append(node("p", "Nessun dispositivo senza profilo nell’ultima lettura.", "notice"));
  const found = previous.filter(matches);
  $("previous-devices").hidden = !found.length;
  if (term && found.length) $("previous-devices").open = true;
  $("previous-device-count").textContent = "Rilevati in precedenza · " + found.length;
  $("device-history").replaceChildren(...found.map(device => deviceRow(device)));
}

function profileActions(user, path) {
  const actions = node("div", undefined, "controls");
  const action = (label, name, style) => button(label, () => save(path + "/action", "POST", {action: name}), style);
  actions.append(action("Blocca ora", "block", "danger"), action("Sblocca ora", "unblock"));
  if (user.override_until && new Date(user.override_until) > new Date()) {
    actions.append(action("Ripristina programmazione", "resume"));
  }
  return actions;
}

function renderPeople() {
  const container = $("users");
  container.replaceChildren();
  for (const user of state.users) {
    const target = "users/" + user.id;
    const devices = state.devices.filter(device => device.user_ids.includes(user.id));
    const online = state.last_poll && !state.last_error
      ? " · " + devices.filter(device => device.online && device.in_latest_snapshot).length + " online" : "";
    const heading = add(node("div", undefined, "row"),
      node("span", user.avatar || user.name[0].toUpperCase(), "avatar"), node("h3", user.name));
    const description = user.description ? node("p", user.description, "notice") : null;
    const usage = state.usage_today.users[user.id];
    const next = user.policy_reason ? user.next_release : user.next_block;
    const transition = next ? (user.policy_reason ? "Ripristino previsto " : "Prossimo blocco ") + clock(next) : null;
    const link = node("a", "Apri profilo");
    link.href = "#profilo-" + user.id;
    link.setAttribute("aria-label", "Apri profilo " + user.name);
    const card = add(node("article", undefined, "card"), heading,
      node("p", devices.length + " dispositivi" + online, "notice"));
    if (description) card.append(description);
    card.append(node("p", (user.policy_reason ? reasons[user.policy_reason] : "Accesso previsto") +
      " · " + (state.writes_enabled ? "Stato della regola" : "Anteprima"), "flag " + (user.policy_reason ? "warning" : "safe")));
    if (transition) card.append(node("p", transition, "notice"));
    if (state.last_poll && !state.last_error)
      card.append(node("p", "Ultima lettura " + clock(state.last_poll), "notice"));
    card.append(node("p", usage === undefined ? "Nessun campione oggi" : usage + " min di presenza osservata oggi", "notice"),
      profileActions(user, target));
    link.className = "profile-link";
    card.append(link);
    container.append(card);
  }
  if (!state.users.length) container.append(node("p", "Crea un profilo per assegnare i dispositivi e impostare le fasce orarie.", "notice"));
}

function selectedDays(mask) {
  return days.map((_, day) => day).filter(day => mask & (1 << day));
}

function scheduleDays(rule) {
  return selectedDays(rule.days_mask ?? 1 << rule.weekday);
}

function scheduleSummary(rule) {
  return [rule.label || groupNames[rule.kind], scheduleDays(rule).map(day => days[day].slice(0, 3)).join(", "),
    rule.start_time + "–" + rule.end_time, rule.enabled ? null : "disattivata"].filter(Boolean).join(" · ");
}

function weeklyChecks(selected) {
  const boxes = days.map((day, index) => checkbox(day, selected.includes(index)));
  const controls = add(node("div", undefined, "checks"), ...boxes.map(box => box.label));
  const group = add(node("fieldset", undefined, "full"), node("legend", "Giorni di inizio"), controls);
  return {group, boxes};
}

function scheduleEditor(path, rule, kind, done) {
  const editing = Boolean(rule);
  const form = node("form", undefined, "form-grid");
  const label = input("text", rule?.label || "");
  label.maxLength = 60;
  const start = input("time", rule?.start_time || (kind === "bedtime" ? "20:30" : "16:00"));
  const end = input("time", rule?.end_time || (kind === "bedtime" ? "07:00" : "17:30"));
  start.required = end.required = true;
  const {group, boxes} = weeklyChecks(rule ? scheduleDays(rule) : [0, 1, 2, 3, 4]);
  const templates = state.templates.filter(template => template.kind === kind);
  if (!editing) {
    const template = select([["", "Personalizzato"], ...templates.map((item, index) => [index, item.label])], "");
    template.onchange = () => {
      if (template.value === "") return;
      const selected = templates[Number(template.value)];
      label.value = selected.label;
      start.value = selected.start_time;
      end.value = selected.end_time;
      boxes.forEach((box, day) => { box.element.checked = selected.weekdays.includes(day); });
    };
    form.append(field("Modello", template));
  }
  add(form, field("Nome della fascia", label), field("Dalle", start), field("Alle", end), group);
  const saveButton = submit(editing ? "Salva fascia" : "Aggiungi fascia");
  form.append(add(node("div", undefined, "controls full"), saveButton));
  form.onsubmit = async event => {
    event.preventDefault();
    const weekdays = boxes.flatMap((box, index) => box.element.checked ? [index] : []);
    if (!weekdays.length || start.value === end.value) {
      message("Scegli almeno un giorno e due orari diversi.", true);
      return;
    }
    saveButton.disabled = true;
    try {
      if (await save(path + (editing ? "/" + rule.id : ""), editing ? "PUT" : "POST", {
        kind, label: label.value.trim(), start_time: start.value, end_time: end.value, weekdays,
        enabled: editing ? Boolean(rule.enabled) : true
      })) done?.();
    } finally {
      saveButton.disabled = false;
    }
  };
  return form;
}

function scheduleSection(user, path, kind) {
  const section = panel(kind === "bedtime" ? "Bedtime · Orario notturno" : "Offtime · Fasce senza Internet");
  const schedules = state.windows.filter(rule => rule.user_id === user.id && rule.kind === kind);
  const endpoint = path + "/schedules";
  for (const rule of schedules) {
    const row = node("div", undefined, "item");
    const status = node("small");
    status.dataset.schedule = rule.id;
    const paused = rule.paused_until && new Date(rule.paused_until) > new Date();
    const controls = add(node("div", undefined, "controls"),
      button(rule.enabled ? "Disattiva" : "Attiva", () => save(endpoint + "/" + rule.id, "PUT", {
        kind, label: rule.label, start_time: rule.start_time, end_time: rule.end_time,
        weekdays: scheduleDays(rule), enabled: !rule.enabled
      })),
      button(paused ? "Riprendi fascia" : "Sospendi 30 min",
        () => save(endpoint + "/" + rule.id + "/pause", "POST", {minutes: paused ? null : 30})),
      button("Elimina fascia", () => {
        if (confirm("Eliminare questa fascia oraria?")) return save(endpoint + "/" + rule.id, "DELETE");
      }, "danger"));
    const editor = details("Modifica fascia", scheduleEditor(endpoint, rule, kind));
    add(row, add(node("div"), node("strong", scheduleSummary(rule)), status), controls);
    section.append(row, editor);
  }
  if (!schedules.length) section.append(node("p", "Nessuna fascia configurata.", "notice"));
  section.append(details("Aggiungi fascia", scheduleEditor(endpoint, null, kind)));
  return section;
}

function quotaSection(user, path) {
  const section = panel("Quote Internet");
  section.append(node("p", "Obiettivi configurabili per giorno. Applicazione automatica non disponibile: la presenza in rete e le velocità campionate non misurano il tempo di utilizzo di Internet.", "notice"));
  const form = node("form", undefined, "form-grid");
  const quotas = state.weekly_quotas.filter(item => item.user_id === user.id);
  const fields = days.map((day, index) => {
    const fieldInput = input("number", quotas.find(quota => quota.weekday === index)?.minutes || "");
    fieldInput.min = 1;
    fieldInput.max = 1440;
    fieldInput.placeholder = "Nessuna quota";
    form.append(field(day + " (min)", fieldInput));
    return fieldInput;
  });
  form.append(submit("Salva obiettivi"));
  form.onsubmit = event => {
    event.preventDefault();
    const daily = {};
    fields.forEach((control, day) => { if (control.value) daily[day] = Number(control.value); });
    save(path + "/weekly-quotas", "PUT", {days: daily});
  };
  section.append(form);
  return section;
}

function accessSection(user, path) {
  const section = panel("Gestione dell’accesso");
  const stateLabel = state.writes_enabled ? "Stato della regola" : "Anteprima delle regole";
  section.append(node("p", stateLabel + " · " + state.timezone, "notice"));
  if (!state.writes_enabled) section.append(node("p", "Le modifiche vengono salvate localmente. Il Deco resta in sola lettura.", "notice"));
  const status = node("p", "Calcolo delle fasce…");
  status.id = "profile-status";
  const action = (label, duration) => button(label,
    () => save(path + "/action", "POST", {action: "pause", minutes: duration}));
  const end = input("datetime-local");
  end.required = true;
  const form = add(node("form", undefined, "controls"), field("Sospendi fino a (ora di questo dispositivo)", end), submit("Sospendi"));
  form.onsubmit = event => {
    event.preventDefault();
    const until = new Date(end.value);
    if (!Number.isFinite(until.getTime()) || until <= new Date()) {
      message("Indica un orario futuro.", true);
      return;
    }
    save(path + "/action", "POST", {action: "pause", until: until.toISOString()});
  };
  const normal = button("Ripristina programmazione", () => save(path + "/action", "POST", {action: "resume"}));
  return add(section, status, profileActions({...user, override_until: null}, path),
    add(node("div", undefined, "controls"), action("Consenti 15 min", 15), action("Consenti 30 min", 30),
      action("Consenti 60 min", 60), normal), form,
    node("p", "Lo sblocco sospende le fasce già attive fino al loro termine, per un massimo di 24 ore. I dispositivi con blocco individuale seguono la propria regola.", "notice"));
}

function profileDevices(user) {
  const associated = state.devices.filter(device => device.user_ids.includes(user.id));
  const available = state.devices.filter(device => !device.user_ids.includes(user.id));
  const section = panel("Dispositivi associati");
  section.append(node("p", "Un dispositivo condiviso segue le regole applicabili di tutti i suoi profili.", "notice"));
  if (!associated.length) section.append(node("p", "Nessun dispositivo assegnato.", "notice"));
  section.append(...associated.map(device => deviceRow(device, user)));
  if (available.length) {
    const picker = select([["", "Scegli un dispositivo"], ...available.map(device => [
      device.mac, [deviceLabel(device), ...device.user_names].join(" · ")
    ])], "");
    picker.required = true;
    const form = add(node("form", undefined, "controls"), field("Aggiungi un dispositivo", picker), submit("Assegna"));
    form.onsubmit = event => {
      event.preventDefault();
      save("users/" + user.id + "/devices/" + encodeURIComponent(picker.value), "PUT");
    };
    section.append(form);
  }
  return section;
}

function profileSettings(user, path) {
  const section = panel("Configurazione profilo");
  const name = input("text", user.name);
  name.maxLength = 60;
  name.required = true;
  const avatar = input("text", user.avatar);
  avatar.maxLength = 12;
  const age = input("text", user.age_band);
  age.maxLength = 40;
  const description = node("textarea");
  description.maxLength = 300;
  description.value = user.description;
  const form = add(node("form", undefined, "form-grid"), field("Nome", name), field("Avatar (emoji o iniziali)", avatar),
    field("Fascia di età", age), field("Descrizione", description, "full"), submit("Salva profilo"));
  form.onsubmit = event => {
    event.preventDefault();
    save(path, "PUT", {name: name.value, avatar: avatar.value, age_band: age.value, description: description.value});
  };
  section.append(details("Modifica profilo", form), button("Elimina profilo", async () => {
    if (!confirm("Eliminare " + user.name + " e le sue regole? I dispositivi torneranno senza profilo.")) return;
    if (await save(path, "DELETE")) location.hash = "persone";
  }, "danger"));
  return section;
}

function copySummary(value, group) {
  if (group === "quotas") return Object.entries(value).map(([day, minutes]) => days[day] + " " + minutes + " min");
  if (group === "web" || group === "services") {
    if (!Object.keys(value).length) return [];
    if (group === "web") return dnsWebSummary(value);
    return dnsServicesSummary(value);
  }
  return value.map(scheduleSummary);
}

function comparison(before, after) {
  const summary = node("div", undefined, "stack");
  for (const group of Object.keys(before)) {
    const current = copySummary(before[group], group);
    const incoming = copySummary(after[group], group);
    summary.append(add(node("div"), node("h3", groupNames[group]),
      node("p", "Attuali · " + (current.join(" / ") || "Nessuna regola")),
      node("p", "Dopo la copia · " + (incoming.join(" / ") || "Nessuna regola"))));
  }
  return summary;
}

function copySection(user, path) {
  const section = panel("Copia da un altro profilo");
  const sources = state.users.filter(person => person.id !== user.id);
  const result = node("div");
  if (sources.length) {
    const source = select(sources.map(person => [person.id, person.name]), sources[0].id);
    const groups = Object.entries(groupNames).map(([key, name]) => ({key, ...checkbox(name, key === "offtime" || key === "bedtime")}));
    const form = add(node("form", undefined, "stack"), field("Profilo sorgente", source),
      add(node("fieldset"), node("legend", "Impostazioni da copiare"),
        add(node("div", undefined, "checks"), ...groups.map(group => group.label))),
      submit("Confronta regole"));
    form.onchange = () => result.replaceChildren();
    form.onsubmit = async event => {
      event.preventDefault();
      const selected = groups.filter(group => group.element.checked).map(group => group.key);
      if (!selected.length) return message("Scegli almeno un gruppo di regole.", true);
      const payload = {source_id: Number(source.value), groups: selected};
      try {
        const preview = await api(path + "/copy/preview", "POST", payload);
        result.replaceChildren(comparison(preview.before, preview.after),
          button("Conferma sovrascrittura", () => save(path + "/copy", "POST", {...payload, revision: preview.revision}), "danger"));
      } catch (error) {
        message(error.message, true);
      }
    };
    section.append(form, result);
  } else {
    section.append(node("p", "Crea un secondo profilo per copiare le regole.", "notice"));
  }
  const copies = node("div");
  const history = details("Storico delle copie", copies);
  history.ontoggle = async () => {
    if (!history.open) return;
    try {
      const rows = await api(path + "/copy");
      copies.replaceChildren();
      if (!rows.length) copies.append(node("p", "Nessuna copia salvata.", "notice"));
      for (const copy of rows) {
        const row = add(node("div", undefined, "item"),
          node("span", clock(copy.time) + " · " + copy.groups.map(group => groupNames[group]).join(", ")));
        row.append(copy.restored_at ? node("small", "Ripristinata") : button("Ripristina configurazione precedente", () => {
          if (confirm("Ripristinare le regole precedenti della configurazione selezionata?"))
            return save(path + "/copy/" + copy.id + "/restore", "POST", {});
        }));
        copies.append(row);
      }
    } catch (error) {
      copies.textContent = error.message;
    }
  };
  section.append(history);
  return section;
}

async function refreshPreview(path, version) {
  try {
    const preview = await api(path + "/preview");
    if (version !== viewRequest) return;
    const labels = [
      preview.reason ? reasons[preview.reason] + " · blocco previsto" : "Accesso previsto",
      preview.override_until ? "Sospensione fino a " + clock(preview.override_until) : null,
      preview.next_block ? "Prossimo blocco " + clock(preview.next_block) : null,
      preview.next_release ? "Prossima riattivazione " + clock(preview.next_release) : null,
    ];
    $("profile-status").textContent = labels.filter(Boolean).join(" · ");
    const devices = new Map(preview.devices.map(device => [device.mac, device]));
    for (const label of $("profile").querySelectorAll("[data-preview-mac]")) {
      const device = devices.get(label.dataset.previewMac);
      label.textContent = device?.reason
        ? "Anteprima · " + reasons[device.reason]
        : device ? "Anteprima · Accesso previsto" : "";
    }
    for (const label of $("profile").querySelectorAll("[data-schedule]")) {
      const rule = preview.schedules[label.dataset.schedule];
      label.textContent = rule?.paused ? "Sospesa temporaneamente" : rule?.active ? "In fascia adesso" : "Fuori fascia";
    }
  } catch (error) {
    if (version === viewRequest) $("profile-status").textContent = error.message;
  }
}

function showView() {
  if (!state) return;
  const userId = /^#profilo-(\d+)$/.exec(location.hash)?.[1];
  const user = state.users.find(person => person.id === Number(userId));
  const version = ++viewRequest;
  const container = $("profile");
  container.hidden = !user;
  $("overview").hidden = Boolean(user);
  if (!user) return;
  const path = "users/" + user.id;
  const title = panel((user.avatar ? user.avatar + " " : "") + user.name);
  const back = node("a", "Torna alle persone");
  back.href = "#persone";
  const report = node("a", "Apri statistiche del profilo");
  report.href = "#statistiche";
  report.onclick = () => {
    $("report-user").value = user.id;
    $("report-device").value = "";
    filterReportDevices();
    loadReport();
  };
  add(title, add(node("div", undefined, "row"), back, report));
  if (user.description || user.age_band) title.append(node("p", [user.description, user.age_band].filter(Boolean).join(" · "), "notice"));
  const openDns = container.dataset.userId === String(user.id)
    ? new Set([...container.querySelectorAll("details[data-dns][open]")].map(item => item.dataset.dns))
    : new Set();
  container.dataset.userId = user.id;
  container.replaceChildren(title, accessSection(user, path), profileDevices(user),
    scheduleSection(user, path, "offtime"), scheduleSection(user, path, "bedtime"),
    quotaSection(user, path), dnsSection(user, version, openDns), copySection(user, path), profileSettings(user, path));
  refreshPreview(path, version);
}

function filterReportDevices() {
  const current = $("report-device");
  const user = Number($("report-user").value);
  const options = state.devices.filter(device => !user || device.user_ids.includes(user));
  const chosen = options.some(device => device.mac === current.value) ? current.value : "";
  const next = select([["", "Tutti i dispositivi"], ...options.map(device => [device.mac, deviceLabel(device)])], chosen);
  current.replaceChildren(...next.children);
  current.value = chosen;
}

function table(headings, rows) {
  const head = add(node("thead"), add(node("tr"), ...headings.map(heading => {
    const cell = node("th", heading);
    cell.scope = "col";
    return cell;
  })));
  const body = add(node("tbody"), ...rows.map(row => add(node("tr"), ...row.map((value, index) => {
    const cell = value instanceof Node ? add(node("td"), value) : node("td", value);
    cell.dataset.label = headings[index];
    return cell;
  }))));
  return add(node("div", undefined, "tablewrap"), add(node("table"), head, body));
}

function sortReport(rows) {
  const key = $("report-sort").value;
  const direction = Number($("report-direction").value);
  return [...rows].sort((a, b) => {
    const first = a[key], second = b[key];
    if (first == null || second == null) return Number(first == null) - Number(second == null);
    const order = key === "name" ? first.localeCompare(second, "it") : first - second;
    return direction * order || a.name.localeCompare(b.name, "it");
  });
}

function speedCells(row) {
  return [showSpeed(row.average_down_kbs) + " / " + showSpeed(row.max_down_kbs),
    showSpeed(row.average_up_kbs) + " / " + showSpeed(row.max_up_kbs),
    (row.down_samples ?? 0) + " / " + (row.up_samples ?? 0)];
}

function renderPresenceChart(rows, title) {
  const chart = node("ul", undefined, "chart");
  const max = rows.reduce((largest, row) => Math.max(largest, row.minutes), 1);
  for (const row of [...rows].reverse().slice(-30)) {
    const date = row.day.split("-").reverse().join("/");
    const meter = node("meter");
    meter.min = 0;
    meter.max = max;
    meter.value = row.minutes;
    meter.setAttribute("aria-label", date + " · " + title);
    chart.append(add(node("li"), node("span", date), meter, node("span", row.minutes + " min")));
  }
  return add(node("section"), node("h3", "Presenza giornaliera · " + title),
    node("p", "Fino a 30 giorni con campioni disponibili.", "notice"), chart);
}

function renderReport(result, userId, mac) {
  const retention = $("retention-days");
  retention.value = result.retention_override ?? "";
  retention.options[0].textContent = "Usa le impostazioni dell’app (" +
    (result.retention_default ? result.retention_default + " giorni" : "senza scadenza") + ")";
  $("sample-retention").querySelector("button").disabled = false;
  const history = $("history");
  history.replaceChildren();
  $("report-source").textContent = [
    result.source + " · letture ogni " + result.poll_seconds + " s",
    result.start + " — " + result.end + " · " + result.timezone,
    result.retention_days ? "Conservazione " + result.retention_days + " giorni" : "Conservazione senza scadenza",
  ].join(" · ");

  const speedHeadings = ["Download medio / max", "Upload medio / max", "Campioni ↓ / ↑"];
  const devicesByMac = new Map(state.devices.map(device => [device.mac, device]));
  const deviceTotals = new Map(result.device_totals.map(row => [row.mac, row]));
  const profileTotals = new Map(result.profile_totals.map(row => [row.user_id, row]));
  const selectedDevices = state.devices.filter(device => (!userId || device.user_ids.includes(userId)) &&
    (!mac || device.mac === mac));
  const selectedProfiles = state.users.filter(user => (!userId || user.id === userId) &&
    (!mac || devicesByMac.get(mac)?.user_ids.includes(user.id)));
  const deviceRows = sortReport(selectedDevices.map(device => ({
    ...deviceTotals.get(device.mac), device, name: deviceLabel(device)
  })));
  const profileRows = sortReport(selectedProfiles.map(user => ({
    ...profileTotals.get(user.id), user, name: user.name
  })));

  if (mac && selectedDevices.length) {
    const [device] = selectedDevices;
    const current = state.last_poll && !state.last_error && device.in_latest_snapshot;
    const status = current ? (device.online ? "Online" : "Offline") : "Stato non aggiornato";
    history.append(add(node("article", undefined, "card"), node("h3", deviceLabel(device)),
      node("p", [device.alias && device.name, device.client_type, device.ip, device.mac, status].filter(Boolean).join(" · ")),
      node("p", "Ultimo campione disponibile nella lettura corrente · ↓ " + showSpeed(current ? device.down_kbs : null) +
        " · ↑ " + showSpeed(current ? device.up_kbs : null), "notice")));
  }
  const summary = add(node("section", undefined, "stats-section"), node("h3", "Dispositivi · riepilogo del periodo"));
  if (deviceRows.length) {
    summary.append(table(["Dispositivo", "Ultima presenza online", "Giorni osservati", "Presenza", ...speedHeadings],
      deviceRows.map(row => [deviceLink(row.device), clock(row.device.last_online) || "n/d",
        row.observed_days ?? 0, row.minutes == null ? "n/d" : row.minutes + " min", ...speedCells(row)])));
  } else summary.append(node("p", "Nessun dispositivo per questi filtri.", "notice"));
  history.append(summary);

  const people = add(node("section", undefined, "stats-section"), node("h3", "Persone · statistiche aggregate"),
    node("p", "Ogni profilo conta una sola volta i minuti sovrapposti. Un dispositivo condiviso contribuisce a ogni profilo associato. Le medie usano tutti i campioni di velocità disponibili dei dispositivi inclusi.", "notice"));
  if (profileRows.length) {
    people.append(table(["Profilo", "Dispositivi osservati", "Giorni osservati", "Presenza", ...speedHeadings],
      profileRows.map(row => [row.name, row.observed_devices ?? 0, row.observed_days ?? 0,
        row.minutes == null ? "n/d" : row.minutes + " min", ...speedCells(row)])));
  } else people.append(node("p", "Nessun profilo per questi filtri.", "notice"));
  history.append(people);

  if (!result.data.length) {
    history.append(node("p", "Nessun campione disponibile per questo periodo e questi filtri.", "notice"));
    return;
  }
  if (mac && deviceRows.length) history.append(renderPresenceChart(result.data, deviceRows[0].name));
  else if (userId && profileRows.length) history.append(renderPresenceChart(result.profiles, profileRows[0].name));
  else history.append(renderPresenceChart(result.network_days, "Almeno un dispositivo online"));

  const daily = details("Dettaglio giornaliero");
  const dailyRows = result.data.map(row => ({
    ...row, name: deviceLabel(devicesByMac.get(row.mac) || {name: row.mac})
  }));
  const ordered = sortReport(dailyRows);
  let displayed = 0;
  const list = node("div");
  const more = button("Mostra altre righe", () => showMore());
  function showMore() {
    const next = ordered.slice(displayed, displayed + 50);
    list.append(table(["Giorno", "Dispositivo", "Presenza", ...speedHeadings],
      next.map(row => [row.day.split("-").reverse().join("/"), row.name, row.minutes + " min", ...speedCells(row)])));
    displayed += next.length;
    more.hidden = displayed >= ordered.length;
  }
  showMore();
  daily.open = Boolean(mac);
  daily.append(list, more);
  history.append(daily);
}

async function loadReport() {
  const request = ++reportRequest;
  const params = new URLSearchParams();
  const period = $("report-period").value;
  if (period === "custom") {
    const start = $("report-start").value;
    const end = $("report-end").value;
    if (!start || !end) return;
    params.set("start", start);
    params.set("end", end);
  } else params.set("days", period);
  const user = $("report-user").value;
  const mac = $("report-device").value;
  if (user) params.set("user_id", user);
  if (mac) params.set("mac", mac);
  try {
    const result = await api("usage?" + params);
    if (request === reportRequest) renderReport(result, Number(user), mac);
  } catch (error) {
    if (request === reportRequest) message(error.message, true);
  }
}

function renderOverview() {
  $("release").textContent = "Versione " + state.release + " · TP-Link Deco X50";
  const setup = state.inventory_setup_required;
  $("refresh").textContent = state.mode === "mock" ? "Aggiorna simulazione" : "Leggi dal Deco";
  $("refresh").disabled = $("probe").disabled = setup;
  const mode = setup ? "Configurazione richiesta" : state.mode === "mock" ? "Simulazione" : "Inventario X50";
  const access = state.writes_enabled
    ? (state.mode === "mock" ? "Comandi simulati" : "Comandi abilitati · esito da verificare")
    : "Deco in sola lettura";
  $("badges").replaceChildren(node("span", mode, "flag " + (setup ? "warning" : "safe")),
    node("span", access, "flag " + (state.writes_enabled ? "warning" : "safe")));
  renderPeople();
  renderInventory();
  const reportUser = $("report-user");
  const selected = reportUser.value;
  reportUser.replaceChildren(...select([["", "Tutti i profili"], ...state.users.map(user => [user.id, user.name])], selected).children);
  reportUser.value = state.users.some(user => String(user.id) === selected) ? selected : "";
  filterReportDevices();
  $("audit").replaceChildren(table(["Orario", "Dispositivo", "Operazione", "Stato", "Dettagli"],
    state.audit.map(row => [clock(row.time), row.mac || "—", row.action.replaceAll("_", " "),
      actionLabel(row.status), row.detail])));
}

async function refresh() {
  try {
    state = await api("state");
    renderOverview();
    showView();
    await loadReport();
  } catch (error) {
    message(error.message, true);
  }
}
const diagnosticLabels = {
 not_checked: "Da verificare", simulated: "Simulato", read_only_detected: "Rilevato in sola lettura",
 not_supported: "Non supportato", not_reported: "Dato non restituito", sdk_unavailable: "SDK non disponibile",
 temporary_error: "Errore temporaneo", read_error: "Risposta non utilizzabile",
 authentication_failed: "Accesso API locale rifiutato: verifica admin + password del TP-Link ID proprietario. Se la pagina web funziona, potrebbe esserci un problema di protocollo o sessione. Evita tentativi ripetuti.",
 not_verified: "Da verificare fisicamente"
};
async function loadDiagnostics(method="GET", credentials){
 const buttons=[$("probe"),$("probe-deco")], summary=$("diagnostic-summary");
 for(const button of buttons)button.disabled=true;
 if(method==="POST"){
  summary.textContent="Verifica in corso…";
  $("diagnostic-json").textContent="";
 }
 try{
  const report=await api(credentials?"diagnostics/deco":"diagnostics",method,credentials);
  const rows=[
   ["Sessione Deco", diagnosticLabels[report.connection]],
   ["Inventario", diagnosticLabels[report.inventory.status]+(report.inventory.count===null?"":" · "+report.inventory.count+" dispositivi")],
   ["Firmware", [report.model,report.firmware_version,diagnosticLabels[report.firmware.status]].filter(Boolean).join(" · ")],
   ["Lista blocchi", diagnosticLabels[report.blocklist.status]+(report.blocklist.count===null?"":" · "+report.blocklist.count+" voci")],
   ["Velocità campionate (KB/s)", diagnosticLabels[report.speed_samples]],
   ["Blocco Internet", diagnosticLabels[report.enforcement]]
  ];
  const list=node("ul");
  for(const [label,value] of rows)list.append(node("li",label+": "+value));
  summary.replaceChildren(list);
  if(report.checked_at)summary.append(node("p","Ultima verifica: "+new Date(report.checked_at).toLocaleString()+" · Al massimo una verifica al minuto.","notice"));
  $("diagnostic-json").textContent=JSON.stringify(report,null,2);
 }catch(e){summary.textContent=e.message}
 finally{for(const button of buttons)button.disabled=button.id==="probe"&&!!state?.inventory_setup_required}
}

$("add-user").onsubmit = async event => {
  event.preventDefault();
  const name = $("user-name");
  if (name.value.trim() && await save("users", "POST", {name: name.value.trim()}, "Profilo creato."))
    name.value = "";
};

$("refresh").onclick = async () => {
  $("refresh").disabled = true;
  try {
    if (!await save("refresh", "POST", {}, "Lettura completata.")) await refresh();
  } finally {
    $("refresh").disabled = Boolean(state?.inventory_setup_required);
  }
};

$("device-filter").oninput = () => { if (state) renderInventory(); };
$("report-user").onchange = () => { filterReportDevices(); loadReport(); };
$("report-device").onchange = loadReport;
$("report-sort").onchange = loadReport;
$("report-direction").onchange = loadReport;
$("report-period").onchange = () => {
  const custom = $("report-period").value === "custom";
  for (const label of document.querySelectorAll(".report-date")) {
    label.hidden = !custom;
    label.querySelector("input").required = custom;
  }
  if (!custom) loadReport();
};
$("report-form").onsubmit = event => { event.preventDefault(); loadReport(); };
$("sample-retention").onsubmit = async event => {
  event.preventDefault();
  const control = $("retention-days");
  const days = control.value === "" ? null : Number(control.value);
  if (days !== 0 && !confirm("Salvare la conservazione? I campioni fuori dal periodo impostato saranno eliminati.")) return;
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  try {
    await save("usage/retention", "PUT", {days}, "Conservazione salvata.");
  } finally {
    button.disabled = false;
  }
};
$("clear-samples").onsubmit = event => {
  event.preventDefault();
  const through = $("clear-through").value;
  const prompt = through ? "Eliminare i campioni fino al " + through + " incluso?" : "Eliminare tutti i campioni storici?";
  if (confirm(prompt)) save("usage" + (through ? "?through=" + through : ""), "DELETE", undefined, "Campioni eliminati.");
};

$("probe").onclick = () => loadDiagnostics("POST");
$("deco-probe-form").onsubmit = event => {
  event.preventDefault();
  const password = $("deco-password");
  const credentials = {router_ip: $("deco-ip").value.trim(), username: $("deco-username").value, password: password.value};
  password.value="";
  loadDiagnostics("POST", credentials);
};

window.addEventListener("hashchange", () => {
  showView();
  if (location.hash.startsWith("#profilo-")) window.scrollTo(0, 0);
  else if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
});
window.setInterval(() => {
  const id = /^#profilo-(\d+)$/.exec(location.hash)?.[1];
  if (id && $("profile-status")) refreshPreview("users/" + id, viewRequest);
}, 30_000);
refresh();
loadDiagnostics();
