# Registro delle modifiche

Tutte le modifiche rilevanti a questo progetto saranno documentate in questo file.

Il formato è basato su [Keep a Changelog](https://keepachangelog.com/it/1.1.0/),
e questo progetto aderisce al [Versionamento Semantico](https://semver.org/lang/it/).

## [1.4.0] - 2026-11-02

Prima release con note pubblicate dopo la 0.1.0. Raccoglie 41 commit dalla
1.3.1: login diretto SISTER oltre a SPID, riduzione della latenza media del
34% (circa 3,4 secondi per richiesta), retry sulla push SPID non approvata,
gestione delle sessioni orfane, alias delle province e fail-fast su Trento e
Bolzano, catena di sicurezza con Dependabot, SBOM CycloneDX e audit delle
dipendenze in CI.

### Aggiunto
- Licenza AGPL v3 (passaggio da GPL v3 per chiudere la SaaS loophole)
- File CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md
- Dependabot settimanale sulle dipendenze Python e mensile sulle GitHub Actions,
  con patch e minor raggruppate in un'unica pull request
- Workflow SBOM (CycloneDX) su push in `main` e su tag `v*`, con l'inventario
  delle dipendenze conservato come artefatto
- Nel README, il rimando alle API dati catastali Zornade per chi non vuole
  pilotare SISTER e non ha un contratto con l'Agenzia
- Configurazione CI con GitHub Actions
- File pyproject.toml con metadati del progetto
- File .env.example completo con tutte le variabili
- Endpoint API per estrazione immobili (`POST /visura`) - accetta parametri catastali diretti
- Endpoint API per estrazione intestati (`POST /visura/intestati`) - accetta parametri catastali diretti
- Endpoint API per consultazione risultati (`GET /visura/{request_id}`)
- Endpoint API per estrazione sezioni territoriali (`POST /sezioni/extract`)
- Gestione automatica della sessione SISTER con keep-alive
- Ri-autenticazione automatica alla scadenza della sessione
- Shutdown graceful con logout dal portale
- Supporto Docker con docker-compose
- Filtro automatico immobili con partita "Soppressa"
- Gestione risultati multipli con iterazione radio button
- Nota sulla compatibilità SPID (solo CIE Sign / Sielte ID)
- **Variabile d'ambiente `PAGES_LOG_DIR`** per configurare la directory di salvataggio degli HTML loggati dal browser. Se non impostata e la directory di default (`./logs/pages`) non è scrivibile, viene usato automaticamente `/tmp/visura-api/logs/pages`; se anche quella fallisce, il logging delle pagine viene disabilitato silenziosamente senza interrompere il servizio.

### Rimosso
- Rimossa dipendenza da PostgreSQL / SQLAlchemy - il servizio ora è completamente stateless
- Rimosso modulo `database.py`
- Rimossi endpoint che richiedevano il database (`GET /parcel/{parcel_fid}`, `GET /sezioni`, `GET /sezioni/stats`, `GET /sezioni/province`, `GET /sezioni/comuni/{provincia}`)
- Rimosse dipendenze `sqlalchemy` e `psycopg[binary]`

### Corretto
- Rimosso `sys.exit(0)` duplicato nel gestore dei segnali
- Indirizzo dedicato `sicurezza@zornade.com` in SECURITY.md, al posto del
  profilo GitHub, e periodo di supporto di cinque anni allineato al Cyber
  Resilience Act
- **Stabilità Docker**: rimosso flag Chromium `--single-process` (incompatibile con Docker, causava crash sporadici al re-init) e aggiunta chiusura esplicita dell'istanza Playwright precedente in `BrowserManager.initialize()` e `BrowserManager.close()` per evitare processi Chromium orfani durante session recovery e shutdown
- **Robustezza `PageLogger`**: la creazione della directory di logging ora è tollerante ai filesystem read-only o privi di permessi di scrittura. La risoluzione avviene in cascata (`PAGES_LOG_DIR` env → variabile di modulo `PAGES_LOG_DIR` → fallback `/tmp/visura-api/logs/pages` → disabilitazione silenziosa), evitando crash all'avvio in container con application directory in sola lettura o senza volume montato.
