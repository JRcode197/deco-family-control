# Deco Family Control — X50

Deco Family Control è un prototipo di controllo parentale della rete locale per TP-Link Deco, senza abbonamento. Offre profili, assegnazione dei dispositivi, regole di blocco manuali, fasce orarie settimanali, rilevamento della presenza in rete e un registro locale delle operazioni. L’interfaccia si adatta anche al browser di iPhone. I dati restano nel database SQLite locale e le credenziali del router rimangono nella configurazione protetta.

> **Stato del progetto.** La simulazione è disponibile. L’efficacia dei comandi di blocco e ripristino va verificata sul proprio Deco X50 prima di usare l’app per proteggere la rete. Le funzioni a pagamento di HomeShield, il tempo di utilizzo su iOS, le metriche per singola app e la cronologia dettagliata dei siti sono fuori dall’ambito del progetto.

## Requisiti

- TP-Link Deco X50 con accesso all’interfaccia web locale.
- Home Assistant con supporto per le app oppure un host locale con Docker. Tieni acceso l’host per eseguire la programmazione.

## Funzionalità nella versione 0.5.0

- Profili familiari, dispositivi condivisi tra più persone e alias ricercabili. Assegnazioni e alias sono persistenti per MAC. L’inventario reale è disponibile in Home Assistant Ingress con le credenziali nelle opzioni protette dell’app.
- Offtime, Bedtime, blocchi manuali, tempo aggiuntivo e deroghe. Il motore calcola l’anteprima, le prossime transizioni e le scadenze persistenti. **Home Assistant mantiene il Deco in sola lettura.**
- Modelli settimanali e copia selettiva tra profili con anteprima e ripristino. Le quote Internet giornaliere sono obiettivi configurabili.
- Domini, categorie, SafeSearch, YouTube e catalogo servizi configurabili localmente. Il passaggio opzionale ad AdGuard Home avviene tramite regole DNS per client da applicare manualmente.
- Presenza campionata e velocità osservate con riepiloghi per dispositivo, aggregati per persona, filtri e ordinamenti. L’attribuzione ai profili usa le assegnazioni registrate dal primo avvio della 0.5.0. La copertura distingue letture riuscite, errori e intervalli senza esito. Campioni gestiti in SQLite con conservazione configurabile.
- Dashboard responsive in italiano, accesso amministratore e conferma per operazioni come cancellazione e sovrascrittura.

Consulta le [istruzioni di installazione e aggiornamento](DOCS.md#installare-o-aggiornare-lapp-home-assistant), la [matrice delle funzionalità](DOCS.md#matrice-delle-funzionalità) e il [collaudo locale X50](DOCS.md#collaudo-fisico-deco-x50).

## Limiti e compatibilità da verificare

1. `tplink-deco-api` 2.2.0 usa le API locali del Deco. La diagnostica in sola lettura consente di verificare il supporto dell’inventario sul proprio firmware. `get_client_list()` può restituire dati incompleti o includere dispositivi offline. Usa le credenziali amministrative che funzionano nell’interfaccia web del router, spesso quelle dell’account TP-Link. Il nome utente API può variare in base al modello.
2. Su alcuni firmware, `clients.block()` modifica la lista dei blocchi e l’accesso a Internet può rimanere disponibile. Deco Guard segnala che su BE85 serve il protocollo SSH/TMP usato dall’app. Verifica la raggiungibilità di Internet sul tuo X50 dopo il blocco e dopo lo sblocco. [Deco Guard](https://github.com/leewsimpson/ha-deco-guard)
3. Il flag `managed_block` registra i comandi accettati. La verifica dell’effettivo blocco richiede una prova di connettività. Usa l’attuatore `ha_guard` solo dopo aver collaudato l’integrazione esterna Deco Guard su X50. Deco Guard è distribuito separatamente con licenza GPL-3.0.
4. Gli endpoint `patrol_insights` e DPI di HomeShield possono richiedere un abbonamento. L’eventuale raccolta delle richieste DNS tramite un componente esterno come AdGuard Home ha limiti di copertura e di privacy.
5. Su iOS puoi impostare l’indirizzo Wi-Fi privato su **Fisso** per la rete domestica. VPN, Relay privato, DNS cifrato e altre reti Wi-Fi possono aggirare il filtro DNS. I blocchi del router riguardano il traffico che passa dal Deco.
6. La programmazione richiede che l’host locale sia acceso. I blocchi già applicati possono rimanere sul router anche quando l’host è spento.
7. Le quote Internet automatiche sono indisponibili sull’hardware reale. La quota basata sulla presenza campionata è riservata alla simulazione autonoma.

<a id="quickstart-safe-simulation"></a>

## Avvio rapido in simulazione

```sh
cp .env.example .env
# Imposta ADMIN_PASSWORD con una password lunga e univoca. Escludi .env dai commit.
docker compose up --build -d
# Apri l’indirizzo dalla macchina che esegue il servizio.
open http://127.0.0.1:8088
```

Accedi con `ADMIN_USER` e `ADMIN_PASSWORD`. Crea un profilo, assegna i dispositivi simulati e aggiungi una fascia oraria. Con `DECO_MODE=mock` e `DECO_WRITE_ENABLED=false` l’app opera in simulazione e il router rimane in sola lettura. La porta 8088 ascolta solo su localhost per impostazione predefinita. Usa HTTP Basic su localhost oppure tramite HTTPS o VPN. Per accedere da iPhone puoi usare un proxy inverso locale con HTTPS e autenticazione o una VPN privata come Tailscale.

## Verifica del Deco X50 sulla rete locale

### Home Assistant

Nelle installazioni con lo store delle app, aggiungi questo repository, installa **Deco Family Control** e seleziona **Avvia → Apri interfaccia web**. Home Assistant Ingress limita l’accesso agli amministratori. Attiva l’inventario opzionale in sola lettura per assegnare i dispositivi rilevati e conservare lo storico dopo il riavvio. [Istruzioni per Home Assistant](DOCS.md)

### Diagnostica in sola lettura

**Lista dei blocchi.** Il firmware del Deco X50 può rappresentare una lista vuota come `{"client_list": {}}` oppure `{"client_list": []}`. La diagnostica accetta anche l’oggetto vuoto e restituisce `blocklist.status=read_only_detected` con `count=0`. Una risposta incompleta o un oggetto non vuoto di formato inatteso produce `read_error`. L’efficacia del blocco va verificata con una prova di connettività.

**Autenticazione locale.** L’SDK usa una procedura di autenticazione firmata basata su `md5(username + password)`. Prova `admin` come nome utente API anche quando l’interfaccia web del Deco mostra solo il campo password. Usa la password dell’account TP-Link proprietario e verifica prima che funzioni sulla pagina web locale del router. Lo stato `authentication_failed` può dipendere dalle credenziali, da un protocollo incompatibile, da una sessione rifiutata o da un blocco temporaneo. Evita tentativi ripetuti. Se l’accesso web funziona e la diagnostica fallisce, condividi solo il JSON redatto, la revisione hardware e il firmware. Conserva password, token, cookie e log grezzi fuori da GitHub.

1. Dalla rete domestica, avvia l’app seguendo la procedura di simulazione. Per questa verifica mantieni `DECO_MODE=mock` e `DECO_WRITE_ENABLED=false`. Nel file `.env` bastano le impostazioni di accesso alla dashboard e i valori predefiniti.
2. Accedi alla dashboard. In **Diagnostica Deco X50** inserisci l’indirizzo IPv4 privato e le credenziali del Deco. Seleziona **Esegui verifica in sola lettura**. La verifica usa una sessione per leggere l’inventario, il firmware del gateway e la lista dei blocchi. Il campo della password si svuota dopo l’invio e l’app conserva in memoria solo il rapporto redatto.
3. Apri **Risultato JSON**. Se segnali un problema su GitHub, condividi il rapporto redatto e la revisione hardware, se nota. Il rapporto contiene stati, conteggi e metadati riconosciuti del firmware. `/api/state`, le schermate e le copie del database possono contenere identificativi dei dispositivi.

La richiesta autenticata `GET /api/diagnostics` restituisce l’ultimo rapporto conservato in memoria. La verifica manuale usa `POST /api/diagnostics/deco` con i campi JSON `router_ip`, `username` e `password` e richiede l’intestazione `X-Family-Action: 1`. Dopo un errore puoi correggere i dati e riprovare dopo un minuto. La diagnostica si collega direttamente all’indirizzo IPv4 indicato e rifiuta i reindirizzamenti. L’SDK usa HTTPS con la verifica del certificato disattivata per il router. Esegui la prova su una rete locale di fiducia.

Per interrogare periodicamente il Deco in modalità autonoma, imposta `DECO_MODE=deco`, `DECO_ROUTER_IP`, `DECO_USERNAME` e `DECO_PASSWORD` nel file `.env` escluso da Git e ricrea il servizio. **Usa configurazione del server** esegue la verifica con quei valori. Se l’accesso viene rifiutato, correggi la configurazione e ricrea il servizio.

### Collaudo fisico di blocco e sblocco

La procedura usa un dispositivo di prova non essenziale, un dispositivo amministrativo indipendente e una verifica di ripristino. Registra l’accettazione dei comandi e le modifiche alla lista dei blocchi separatamente dall’esito delle prove di connettività. [Procedura di collaudo locale](DOCS.md#collaudo-fisico-deco-x50)

### Matrice di compatibilità

| Funzionalità | Evidenza disponibile | Compatibilità Deco X50 |
| --- | --- | --- |
| Inventario dei client e firmware del gateway | L’SDK 2.2.0 documenta le letture locali dell’inventario e dell’elenco dei dispositivi mesh. La diagnostica le verifica separatamente. | Esegui la diagnostica in sola lettura per verificare il rilevamento. HTTP 404 o 501 indica che l’endpoint di lettura è indisponibile. |
| Lettura della lista dei blocchi | `clients.get_blocked()` legge la lista dei blocchi della rete mesh. La diagnostica controlla il formato e restituisce solo il conteggio. | Da verificare in sola lettura. |
| Blocco e ripristino di Internet | `clients.block()` invia il comando di blocco. `clients.unblock()` invia il comando inverso. | Da verificare con il collaudo fisico. |
| Presenza e velocità campionate | La presenza conta i minuti in cui un dispositivo è stato osservato online. I campioni di velocità sono istantanei, in KB/s. `get_client_totals()` somma le velocità. | La disponibilità dei campioni dipende dal firmware. Byte trasferiti, tempo di utilizzo su iOS e cronologia di app e siti sono indisponibili. |
| Integrazione opzionale Deco Guard per Home Assistant | Componente separato con licenza GPL-3.0 o successiva. Il progetto documenta prove su BE85 con firmware 1.2.4 e affianca il protocollo dell’app alle API web. | Compatibilità X50 ed efficacia del blocco da verificare. |

## Sviluppo

L’SDK reale richiede Python 3.14. I test in simulazione usano le dipendenze di base con Python 3.12 o successivo.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
ADMIN_PASSWORD=local-test-pass-not-for-production DATABASE_PATH=./var/family.sqlite3 uvicorn app.main:app --reload --no-access-log --host 127.0.0.1 --port 8088
pytest -q
```

Per installare l’SDK, esegui `pip install -r requirements-deco.txt` con Python 3.14.

### Analisi e copertura dei test dell’issue #1

- La dashboard e le rotte protette usano HTTP Basic. Le operazioni di modifica richiedono `X-Family-Action`. La lettura del rapporto salvato in memoria non interroga il router. I rapporti e i log di errore omettono risposte grezze dell’SDK, messaggi delle eccezioni e URL di sessione.
- SQLite usa una connessione protetta da un lock. All’arresto, l’app attende la fine della lettura prima di chiudere la connessione. L’inventario e le modifiche ai profili vengono salvati in transazioni. I test coprono le migrazioni additive, la copia e il ripristino dei profili e la scadenza degli override dopo il riavvio.
- Le sessioni SDK simulate coprono endpoint assenti, risultati vuoti o malformati, accessi rifiutati, attesa tra i tentativi, operazioni in sola lettura e redazione dei dati. I test API coprono autenticazione, intestazione per le modifiche e conservazione del risultato in memoria. Le risposte del firmware reale, la raggiungibilità dei client e le letture prolungate durante richieste concorrenti richiedono verifiche separate.

## Prossime attività di sviluppo

Esegui il collaudo separato di blocco e sblocco e verifica l’identificazione dei client e la risoluzione DNS di AdGuard Home sulla rete locale. Per applicare le quote servono misure attendibili dell’attività. Il piano è descritto nei documenti di progetto. [PRD](PRD.md) · [Avvio del lavoro con Codex](CODEX_START.md)

## Progetti di terze parti valutati

- SDK Python con tipi, documentazione e test, distribuito con licenza MIT e usato come dipendenza esterna. [tplink-deco-api](https://github.com/roquerodrigo/tplink-deco-api)
- Integrazione esterna opzionale per Home Assistant, distribuita separatamente con licenza GPL-3.0 o successiva. Il progetto documenta il comportamento dei comandi di blocco. [ha-deco-guard](https://github.com/leewsimpson/ha-deco-guard)
- Integrazione Home Assistant per leggere client e stato del router. Il manutentore ha annunciato la migrazione a `dkwolf1/ha-deco`. Il progetto è usato come riferimento tecnico. [ha-tplink-deco](https://github.com/amosyuen/ha-tplink-deco)

Deco Family Control è sviluppato in modo indipendente e distribuito con licenza MIT. I componenti esterni seguono le rispettive licenze.
