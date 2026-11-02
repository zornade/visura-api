# Licenza commerciale - Visura API

> **TL;DR** - `visura-api` è distribuito sotto licenza **AGPL-3.0-only**.
> Se vuoi usarlo in un servizio commerciale proprietario senza dover pubblicare
> il codice sorgente del tuo sistema combinato (auth, frontend, orchestratori,
> domini), è disponibile una **licenza commerciale separata** acquistabile da
> [zornade](https://zornade.com).

---

## Quando ti serve una licenza commerciale

La licenza AGPL-3.0-only impone obblighi forti **sia in caso di redistribuzione
del software**, sia - punto critico - **in caso di uso in rete** (clausola §13,
"Remote Network Interaction"). In sintesi, se metti online un servizio basato
su `visura-api` modificato, devi offrire pubblicamente il *Corresponding Source*
completo della tua opera combinata.

### Casi tipici in cui ti serve una licenza commerciale

- **SaaS o piattaforma B2B/B2C** che espone API costruite sopra `visura-api` a
  clienti paganti, e non vuoi pubblicare il codice di:
  - autenticazione (SPID, CIE, magic-link, SSO aziendale)
  - frontend, dashboard, tema UI, gestione cart/billing
  - orchestratori di workflow proprietari
  - logica di business (scoring rischio, NPL, due diligence automatica, ecc.)
- **Piattaforma interna aziendale** che integra `visura-api` con sistemi
  proprietari (CRM, ERP, data warehouse) e non vuoi essere obbligata a
  pubblicare quegli integration layer ai dipendenti come "utenti della rete".
- **Software on-premise distribuito a clienti** dove non vuoi che la
  documentazione del prodotto contenga link al sorgente completo combinato.
- **Bundling in un prodotto commerciale closed-source** (desktop app, plugin
  proprietario, dispositivo embedded) che non può rispettare i requisiti
  di "Installation Information" di AGPL §6.

### Quando NON ti serve una licenza commerciale

Puoi usare `visura-api` sotto AGPL-3.0 senza problemi se:

- Lo usi **personalmente, in locale, per i tuoi dati**.
- Lo usi in un **progetto open source compatibile** (AGPL-3.0 o licenze
  più permissive che accettano di passare ad AGPL-3.0).
- Lo usi in **uno script CLI/batch** che non espone interfacce di rete a terzi.
- Sei disposto a pubblicare l'intero *Corresponding Source* del tuo sistema
  combinato sotto AGPL-3.0, comprese tutte le componenti private linkate.

---

## Cosa include una licenza commerciale

La licenza commerciale standard ti permette di:

1. **Integrare** `visura-api` in un prodotto/servizio proprietario senza
   l'obbligo di pubblicare il sorgente delle componenti combinate.
2. **Modificare** il codice e mantenere le modifiche private.
3. **Distribuire** il prodotto combinato a clienti finali (interni o esterni)
   senza obbligo di fornire il sorgente.
4. **Esporre il servizio in rete** senza dover offrire pubblicamente il
   *Corresponding Source* (AGPL §13 viene esplicitamente derogato dalla
   licenza commerciale).
5. **Ricevere updates** dal ramo upstream per la durata della licenza.

Termini negoziabili includono:

- Supporto tecnico prioritario via email / Slack / call.
- SLA dedicato.
- Roadmap di feature personalizzata.
- Audit & compliance review.
- Indennità su rivendicazioni di terzi sul codice upstream.

---

## Modello di pricing (indicativo)

| Tipologia                                          | Indicativo annuale (EUR) |
|----------------------------------------------------|--------------------------|
| Startup / piccola realtà (< 5 dipendenti, < 250k€ fatturato) | a partire da 990 €/anno  |
| PMI (< 50 dipendenti)                              | a partire da 4.900 €/anno |
| Enterprise / volumi non specificati                | personalizzato            |
| Licenza one-shot perpetua + 1 anno aggiornamenti   | personalizzato            |
| Sub-licensing a clienti finali (white-label)       | personalizzato            |

Il pricing dipende da: numero di deploy, traffico stimato, supporto richiesto,
indennità, durata contrattuale.

---

## Come acquistare

1. Scrivi a **`hello@zornade.com`** descrivendo brevemente:
   - L'uso previsto (SaaS / interno / on-premise / bundling)
   - Numero di deploy o utenti finali stimati
   - Componenti combinate principali (auth, frontend, orchestratori, ecc.)
   - Volume di richieste / traffico stimato
   - Necessità di supporto o SLA
2. Riceverai una proposta entro **5 giorni lavorativi**.
3. Firma di NDA reciproca (opzionale, se richiesto).
4. Contratto firmato + bonifico → licenza emessa entro **48h dal pagamento**.

---

## Domande frequenti

**D: Ho già forkato il repository pubblico. Posso comprare la licenza dopo?**
R: Sì. La licenza commerciale è retroattiva al momento dell'acquisto: copre
tutti gli usi futuri del tuo fork, ma non sana eventuali violazioni AGPL già
commesse. È sempre meglio acquistarla *prima* del deploy.

**D: Esistono già fork pubblici sotto AGPL che hanno aggiunto features. Posso
prenderli e integrarli sotto licenza commerciale?**
R: No, non puoi: le modifiche apportate dai forker terzi sono di loro
copyright e tu non hai automaticamente diritto di relicensarle. Solo il
codice upstream di `zornade/visura-api` è coperto dalla nostra licenza
commerciale. Le modifiche di terzi puoi reimplementarle in autonomia, o
negoziare con i rispettivi autori.

**D: Cosa succede ai contributor del repo open source?**
R: Tutti i contributor accettano (firmando i commit con `--signoff` o tramite
DCO) di concedere al maintainer un grant di relicensing su contribuzioni
sufficiente a permettere la dual-licensing. Vedi `CONTRIBUTING.md`.

**D: Cosa devo fare se sono già in violazione di AGPL §13?**
R: Contattaci a `hello@zornade.com`. Nella maggior parte dei casi la
violazione si risolve con: (a) acquisto retroattivo di licenza commerciale a
copertura del periodo di uso, (b) impegno scritto a rispettare i termini
futuri. Le azioni legali sono l'ultima ratio.

---

## Contatti

- **Email**: `hello@zornade.com`
- **Sito**: https://zornade.com/licensing
- **PEC**: disponibile su richiesta
- **GitHub**: aprire una issue privata su https://github.com/zornade/visura-api
  con tag `licensing` (solo per inquiry non-confidenziali)

---

*Ultimo aggiornamento: 11 maggio 2026*
