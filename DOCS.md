## Inventario Deco X50 da Home Assistant

1. Installa o aggiorna **Deco Family Control** dallo store di Home Assistant.
2. Nella scheda **Configurazione** dell'app, imposta `deco_ip`, `deco_username` (di solito `admin`) e `deco_password` con i dati che hanno funzionato nella diagnostica. Se la password non compare, mostra le opzioni facoltative. Attiva `inventory_enabled`, salva e riavvia l'app.
3. Tocca **Apri interfaccia web** dalla sessione amministratore di Home Assistant. In **Persone**, crea i profili. In **Dispositivi**, cerca un nome, un IP o un MAC e scegli la persona accanto al dispositivo. L'assegnazione viene salvata subito. Scegli **Nessuna persona** per rimuoverla.
4. L'elenco viene aggiornato ogni minuto. **Aggiorna elenco** avvia una lettura manuale. I dispositivi rilevati in precedenza restano nell'elenco. Il riepilogo mostra campioni di presenza Wi-Fi e velocità istantanee in KB/s. La presenza giornaliera per persona usa le assegnazioni attuali e conta una sola volta ciascun minuto in cui almeno un suo dispositivo è stato osservato online.

La configurazione dell'inventario viene letta all'avvio dalle opzioni amministratore di Home Assistant. La password non viene copiata in SQLite. Il database reale, con profili, assegnazioni e storico, risiede nel volume dell'app in `/data/inventory` e sopravvive a riavvii e aggiornamenti. I profili creati nella simulazione sono temporanei. L'inventario reale mantiene il Deco in sola lettura.

**Diagnostica Deco X50** permette di riprovare le letture e copiare **Risultato JSON**. **Usa configurazione del server** riutilizza la configurazione dell'app. Il modulo è disponibile per un tentativo separato. Condividi soltanto il report redatto. Conserva al sicuro i backup di Home Assistant che includono i dati e le opzioni dell'app.
