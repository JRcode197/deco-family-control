"use strict";

const copyCategories = {
  adult: "Contenuti per adulti", gambling: "Gioco d’azzardo", threats: "Malware e phishing",
  social: "Social network", streaming: "Video e streaming", games: "Giochi online"
};
const serviceNames = {youtube: "YouTube", tiktok: "TikTok", roblox: "Roblox", instagram: "Instagram", twitch: "Twitch"};
const restrictedModes = {off: "Disattivato", moderate: "Moderato", strict: "Restrittivo"};

function dnsWebSummary(web) {
  return [
    "Domini bloccati · " + (web.blocked_domains.join(", ") || "nessuno"),
    "Eccezioni · " + (web.allowed_domains.join(", ") || "nessuna"),
    "Categorie · " + (web.categories.map(id => copyCategories[id]).join(", ") || "nessuna"),
    "SafeSearch " + (web.safe_search ? "attivo" : "disattivo"),
    "YouTube " + restrictedModes[web.youtube_mode],
  ];
}

function dnsServicesSummary(services) {
  return [
    "Bloccati · " + (services.blocked_services.map(id => serviceNames[id]).join(", ") || "nessuno"),
    "Consentiti · " + (services.allowed_services.map(id => serviceNames[id]).join(", ") || "nessuno"),
    ...services.windows.map(window => serviceNames[window.service] + " · " +
      window.weekdays.map(day => days[day].slice(0, 3)).join(", ") + " " + window.start_time + "–" + window.end_time),
  ];
}

function domainInput(domains = [], rows = 3) {
  const element = node("textarea");
  element.rows = rows;
  element.spellcheck = false;
  element.autocapitalize = "none";
  element.placeholder = "Un dominio per riga";
  element.value = domains.join("\n");
  return element;
}

function domainList(element) {
  return element.value.split(/\r?\n/).map(line => line.trim()).filter(line => line && !line.startsWith("#"));
}

function webSettings(path, data) {
  const form = node("form", undefined, "form-grid");
  const blocked = domainInput(data.web.blocked_domains);
  const allowed = domainInput(data.web.allowed_domains);
  const sourceNotes = node("ul");
  const categories = Object.entries(data.categories).map(([id, name]) => {
    const source = data.sources[id];
    if (source || data.web.categories.includes(id)) {
      sourceNotes.append(node("li", name + " · " + (source
        ? source.source + " · " + source.count + " domini · aggiornato " + clock(source.updated_at)
        : "Lista non impostata")));
    }
    return {id, ...checkbox(name, data.web.categories.includes(id))};
  });
  const safe = checkbox("SafeSearch · Google e Bing", data.web.safe_search);
  const youtube = select(Object.entries(restrictedModes), data.web.youtube_mode);
  add(form, field("Domini bloccati", blocked), field("Eccezioni per dominio", allowed),
    add(node("fieldset", undefined, "full"), node("legend", "Categorie web"),
      add(node("div", undefined, "checks"), ...categories.map(category => category.label)), sourceNotes),
    add(node("div", undefined, "checks"), safe.label), field("Modalità con restrizioni di YouTube", youtube),
    submit("Salva protezione contenuti"));
  form.onsubmit = event => {
    event.preventDefault();
    save(path + "/web", "PUT", {
      blocked_domains: domainList(blocked), allowed_domains: domainList(allowed),
      categories: categories.filter(category => category.element.checked).map(category => category.id),
      safe_search: safe.element.checked, youtube_mode: youtube.value
    });
  };
  return form;
}

function serviceSettings(path, data) {
  const form = node("form", undefined, "stack");
  const catalog = Object.entries(data.catalog);
  const services = catalog.map(([id, service]) => ({
    id, blocked: checkbox("Blocca " + service.label, data.services.blocked_services.includes(id)),
    allowed: checkbox("Consenti " + service.label, data.services.allowed_services.includes(id))
  }));
  const rows = node("div");
  for (const {id, blocked, allowed} of services) {
    const service = data.catalog[id];
    const annotations = [service.domains.join(", ")];
    if (data.active_service_windows.includes(id)) annotations.push("Fascia di blocco prevista adesso");
    rows.append(add(node("div", undefined, "item"), add(node("div"), node("strong", service.label),
      node("small", annotations.join(" · "))), add(node("div", undefined, "checks"), blocked.label, allowed.label)));
  }
  const windows = data.services.windows.map(window => ({...window}));
  const scheduleList = node("div");
  function renderWindows() {
    scheduleList.replaceChildren();
    windows.forEach((window, index) => {
      const label = data.catalog[window.service].label + " · " +
        window.weekdays.map(day => days[day].slice(0, 3)).join(", ") + " " + window.start_time + "–" + window.end_time;
      scheduleList.append(add(node("div", undefined, "item"), node("span", label),
        button("Rimuovi", () => { windows.splice(index, 1); renderWindows(); })));
    });
  }
  renderWindows();
  const service = select(catalog.map(([id, entry]) => [id, entry.label]), catalog[0][0]);
  const {group, boxes} = weeklyChecks([0, 1, 2, 3, 4]);
  const start = input("time", "16:00");
  const end = input("time", "17:30");
  const addWindow = button("Aggiungi fascia al modulo", () => {
    const weekdays = boxes.flatMap((box, index) => box.element.checked ? [index] : []);
    if (!weekdays.length || !start.value || !end.value || start.value === end.value)
      return message("Indica giorni e orari diversi per la fascia del servizio.", true);
    windows.push({service: service.value, weekdays, start_time: start.value, end_time: end.value});
    renderWindows();
  });
  form.onsubmit = event => {
    event.preventDefault();
    save(path + "/services", "PUT", {
      blocked_services: services.filter(item => item.blocked.element.checked).map(item => item.id),
      allowed_services: services.filter(item => item.allowed.element.checked).map(item => item.id),
      windows,
    });
  };
  add(form, rows, node("p", "Il catalogo copre i domini elencati. Un servizio consentito ha precedenza nei filtri DNS. Le fasce dei servizi sono in anteprima e richiedono un provider con applicazione programmata.", "notice"),
    scheduleList, details("Programma un servizio", add(node("div", undefined, "form-grid"), field("Servizio", service),
      field("Dalle", start), field("Alle", end), group, addWindow)), submit("Salva servizi"));
  return form;
}

function bindingSettings(user, data, path) {
  const bindable = state.devices.filter(device => device.user_ids.includes(user.id) && !state.protected_macs.includes(device.mac));
  const section = node("div", undefined, "stack");
  if (!bindable.length) section.append(node("p", "Assegna un dispositivo non protetto a questo profilo per esportarne le regole.", "notice"));
  for (const device of bindable) {
    const binding = data.clients[device.mac];
    const name = input("text", binding?.client_name || "");
    name.required = true;
    name.pattern = "[A-Za-z0-9][A-Za-z0-9_\\-]{0,47}";
    name.maxLength = 48;
    const blocked = domainInput(binding?.blocked_domains);
    const allowed = domainInput(binding?.allowed_domains);
    const endpoint = path + "/devices/" + device.mac;
    const form = add(node("form", undefined, "form-grid"), field("Nome esatto del client persistente AdGuard Home", name),
      field("Domini da bloccare sul dispositivo", blocked), field("Eccezioni del dispositivo", allowed),
      submit("Salva collegamento"));
    form.onsubmit = event => {
      event.preventDefault();
      save(endpoint, "PUT", {client_name: name.value, blocked_domains: domainList(blocked), allowed_domains: domainList(allowed)});
    };
    if (binding) form.append(button("Rimuovi collegamento", () => save(endpoint, "DELETE")));
    section.append(details(deviceLabel(device) + " · " + (binding ? binding.client_name : "Da collegare"), form));
  }
  return section;
}

function listManagement(data) {
  const category = select(Object.entries(data.categories), Object.keys(data.categories)[0]);
  const source = input("text");
  source.required = true;
  source.maxLength = 250;
  const domains = domainInput([], 5);
  domains.required = true;
  const categoryForm = add(node("form", undefined, "form-grid"), field("Categoria", category),
    field("Fonte o versione della lista", source), field("Lista da sostituire", domains, "full"),
    submit("Aggiorna lista"), button("Rimuovi lista", () => {
      if (confirm("Rimuovere la lista di questa categoria per tutti i profili?")) return save("dns/categories/" + category.value, "DELETE");
    }, "danger"));
  categoryForm.onsubmit = event => {
    event.preventDefault();
    if (data.sources[category.value] && !confirm("Sostituire la lista di questa categoria per tutti i profili?")) return;
    save("dns/categories/" + category.value, "PUT", {source: source.value, domains: domainList(domains)});
  };

  const destinations = state.users.map(user => ({id: user.id, ...checkbox(user.name)}));
  const deny = domainInput(), allow = domainInput();
  const bulk = add(node("form", undefined, "form-grid"), field("Domini da bloccare", deny), field("Eccezioni", allow),
    add(node("fieldset", undefined, "full"), node("legend", "Profili destinatari"),
      add(node("div", undefined, "checks"), ...destinations.map(person => person.label))), submit("Aggiungi ai profili"));
  bulk.onsubmit = event => {
    event.preventDefault();
    const userIds = destinations.filter(person => person.element.checked).map(person => person.id);
    if (!userIds.length) return message("Scegli almeno un profilo destinatario.", true);
    save("dns/import", "POST", {user_ids: userIds, blocked_domains: domainList(deny), allowed_domains: domainList(allow)});
  };
  return add(node("div"), details("Aggiorna liste per categoria", categoryForm), details("Importa domini in più profili", bulk));
}

function exportSettings() {
  const output = node("div");
  return add(node("div", undefined, "stack"),
    node("p", "L’esportazione include le regole permanenti di tutti i client collegati. Incolla il blocco nei filtri personalizzati di AdGuard Home e sostituisci l’eventuale esportazione precedente. Verifica le risposte DNS dal dispositivo di prova.", "notice"),
    button("Genera regole AdGuard Home", async () => {
      try {
        const result = await api("dns/export");
        const statuses = {protetto: "protetto", client_non_collegato: "client non collegato", esportabile: "esportabile"};
        const text = node("textarea");
        text.rows = 8;
        text.readOnly = true;
        text.value = result.rules;
        output.replaceChildren(add(node("ul"), ...result.devices.map(device =>
          node("li", (state.devices.find(item => item.mac === device.mac)?.name || device.mac) +
            " · " + statuses[device.status]))), field("Regole da copiare", text));
      } catch (error) {
        output.textContent = error.message;
      }
    }), output);
}

function dnsSection(user, version, openSections) {
  const section = panel("Protezione web e applicazioni");
  const target = node("div", "Apri per configurare.", "stack");
  function sectionDetails(title, contents) {
    const item = details(title, contents);
    item.dataset.dns = title;
    item.open = openSections.has(title);
    return item;
  }
  const settings = sectionDetails("Filtri DNS e servizi", target);
  settings.ontoggle = async () => {
    if (!settings.open || target.dataset.loaded) return;
    target.textContent = "Caricamento…";
    const path = "dns/users/" + user.id;
    try {
      const data = await api(path);
      if (viewRequest !== version) return;
      target.dataset.loaded = "true";
      target.replaceChildren(
        node("p", "Configurazione locale · applicazione manuale con AdGuard Home · protezione da verificare sui dispositivi.", "notice"),
        sectionDetails("Siti, categorie, SafeSearch e YouTube", webSettings(path, data)),
        sectionDetails("Servizi bloccati e sempre consentiti", serviceSettings(path, data)),
        sectionDetails("Collegamenti ed eccezioni per dispositivo", bindingSettings(user, data, path)),
        listManagement(data), sectionDetails("Esporta regole DNS", exportSettings()));
    } catch (error) {
      target.textContent = error.message;
    }
  };
  return add(section, settings);
}
