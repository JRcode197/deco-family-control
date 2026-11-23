## Diagnostica Deco X50 da Home Assistant

1. Installa **Deco Family Control** nello store di Home Assistant.
2. Premi **Avvia**, poi **Apri interfaccia web**. La pagina usa la sessione di Home Assistant.
3. Inserisci l'IPv4 privato del Deco e le credenziali della sua interfaccia web. Premi **Esegui verifica in sola lettura**. Se accedi a Home Assistant tramite HTTPS, lo stesso collegamento protegge l'invio del modulo dall'iPhone.
4. Apri **Risultato JSON** per copiare il report. Contiene solo esiti, conteggi e metadati firmware riconosciuti.

Le credenziali servono per la singola verifica. Il report e i dati della simulazione sono in memoria e si azzerano quando l'app si ferma. L'accesso passa per Home Assistant Ingress e l'app non pubblica una propria porta sulla rete. Il rilevamento reale è in sola lettura.
