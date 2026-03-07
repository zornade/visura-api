# Registro delle modifiche

Tutte le modifiche rilevanti a questo progetto saranno documentate in questo file.

Il formato è basato su [Keep a Changelog](https://keepachangelog.com/it/1.1.0/),
e questo progetto aderisce al [Versionamento Semantico](https://semver.org/lang/it/).

## [Non rilasciato]

### Aggiunto
- **Refactoring Autenticazione (`auth/`)**:
  - Introdotto supporto provider SPID multipli.
  - Implementazione modulare per i provider `SIELTE` e `CIE`.
  - Configurazione provider tramite variabile `AUTH_PROVIDER`.
  - Nuova variabile d'ambiente `2FA_TIMEOUT_SECONDS` per la gestione del timeout notifiche 2FA.
- Licenza GPL v3
- File CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md
- Configurazione CI con GitHub Actions
- File pyproject.toml con metadati del progetto
- File .env.example completo con tutte le variabili
- Endpoint API per estrazione immobili (`POST /visura`) — accetta parametri catastali diretti
- Endpoint API per estrazione intestati (`POST /visura/intestati`) — accetta parametri catastali diretti
- Endpoint API per consultazione risultati (`GET /visura/{request_id}`)
- Endpoint API per estrazione sezioni territoriali (`POST /sezioni/extract`)
- Gestione automatica della sessione SISTER con keep-alive
- Ri-autenticazione automatica alla scadenza della sessione
- Shutdown graceful con logout dal portale
- Supporto Docker con docker-compose
- Filtro automatico immobili con partita "Soppressa"
- Gestione risultati multipli con iterazione radio button
- Nota sulla compatibilità SPID (solo CIE Sign / Sielte ID)

### Rimosso
- Rimossa dipendenza da PostgreSQL / SQLAlchemy — il servizio ora è completamente stateless
- Rimosso modulo `database.py`
- Rimossi endpoint che richiedevano il database (`GET /parcel/{parcel_fid}`, `GET /sezioni`, `GET /sezioni/stats`, `GET /sezioni/province`, `GET /sezioni/comuni/{provincia}`)
- Rimosse dipendenze `sqlalchemy` e `psycopg[binary]`

### Corretto
- Miglioramenti al logging ed error handling. 
- Corretto bug di session recovery aggiornando l'URL standardizzato `sister3.agenziaentrate.gov.it`.
- Ripristinata l'integrità del blocco `lifespan` in `main.py` per l'avvio della coda e del `BrowserManager`.
- Rimosso `sys.exit(0)` duplicato nel gestore dei segnali
