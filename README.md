# Visura API

[![Licenza](https://img.shields.io/badge/Licenza-AGPL%20v3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com/)

Servizio REST per l'estrazione automatizzata di dati catastali dal portale **SISTER** dell'Agenzia delle Entrate. Utilizza [Playwright](https://playwright.dev/python/) per pilotare un browser headless e [FastAPI](https://fastapi.tiangolo.com/) per esporre gli endpoint.

> **Disclaimer legale** — Questo progetto è uno strumento indipendente e **non** è affiliato, approvato o supportato dall'Agenzia delle Entrate. L'utente è l'unico responsabile del rispetto dei termini di servizio del portale SISTER e della normativa vigente. L'uso di automazione sul portale potrebbe violare i termini d'uso del servizio.

> [!WARNING]  
> Per poter attivare le API bisogna **prima** registrarsi e chiedere l'accesso ai servizi sister utilizzando l'Area Personale di Agenzia delle Entrate e poi cercando "sister" tra i servizi disponibili. L'operazione è veloce.

**iscriviti alla newsletter**:

[![Newsletter](https://img.shields.io/badge/Newsletter-Iscriviti-orange?style=for-the-badge&logo=substack)](https://newsletter.zornade.com)

---

## Indice

- [Panoramica](#panoramica)
- [Architettura](#architettura)
- [Prerequisiti](#prerequisiti)
- [Avvio rapido](#avvio-rapido)
- [Configurazione](#configurazione)
- [Endpoint API](#endpoint-api)
  - [Health check](#health-check)
  - [Visura immobili (Fase 1)](#visura-immobili-fase-1)
  - [Visura intestati (Fase 2)](#visura-intestati-fase-2)
  - [Polling risultati](#polling-risultati)
  - [Sezioni territoriali](#sezioni-territoriali)
  - [Shutdown](#shutdown)
- [Esempi d'uso](#esempi-duso)
- [Logging e debug](#logging-e-debug)
- [Dettagli tecnici](#dettagli-tecnici)
- [Sviluppo e contribuzione](#sviluppo-e-contribuzione)
- [Risoluzione dei problemi](#risoluzione-dei-problemi)
- [Autore](#autore)
- [Licenza](#licenza)

---

## Panoramica

Visura API permette di interrogare i dati catastali italiani tramite una semplice interfaccia HTTP. Il flusso operativo è diviso in due fasi:

| Fase | Endpoint | Descrizione |
|------|----------|-------------|
| **1 — Immobili** | `POST /visura` | Cerca gli immobili associati a foglio + particella |
| **2 — Intestati** | `POST /visura/intestati` | Recupera i titolari di uno specifico subalterno |

Entrambe le richieste vengono accodate ed eseguite sequenzialmente su un singolo browser autenticato al portale SISTER. I risultati si recuperano in polling con `GET /visura/{request_id}`.

### Funzionalità principali

- **Autenticazione SPID automatizzata** via provider Sielte ID (CIE Sign) con push notification
- **Coda sequenziale** — le richieste vengono processate una alla volta per non sovraccaricare il portale
- **Ri-autenticazione automatica** — alla scadenza della sessione, il servizio tenta prima un recovery diretto e, solo se necessario, un nuovo login SPID
- **Keep-alive** — la sessione viene mantenuta attiva con un light keep-alive ogni 30 secondi e un refresh profondo ogni 5 minuti
- **Graceful shutdown** — su `SIGINT`/`SIGTERM` il servizio effettua il logout dal portale prima di chiudere il browser
- **Logging HTML completo** — ogni pagina visitata dal browser viene salvata su disco per debug e audit
- **Docker-ready** — immagine pronta con tutte le dipendenze di sistema per Chromium headless

### Compatibilità SPID

Il login automatizzato funziona **esclusivamente** con il provider **Sielte ID (CIE Sign)**. Il flusso prevede l'approvazione via push notification sull'app MySielteID. Altri provider SPID non sono supportati e richiederebbero modifiche alla funzione `login()` in `utils.py`.

### Limitazioni note

- Alcune città presentano strutture catastali particolari (sezioni urbane, mappe speciali) che possono causare risultati parziali.
- Se la particella non esiste nel catasto, il portale restituisce "NESSUNA CORRISPONDENZA TROVATA" e l'API ritorna una lista vuota con il campo `error` valorizzato.
- Gli immobili con partita "Soppressa" vengono inclusi nei risultati ma senza intestati.

---

## Architettura

```
Client HTTP
     │
     ▼
┌──────────────────────────────────────────────────────┐
│  FastAPI  (main.py)                                  │
│                                                      │
│  ┌─────────────┐  ┌──────────────────────────────┐   │
│  │ Endpoints   │──│ VisuraService                │   │
│  │ REST        │  │  • asyncio.Queue             │   │
│  └─────────────┘  │  • response_store (dict)     │   │
│                   │  • worker sequenziale        │   │
│                   └──────────┬───────────────────┘   │
│                              │                       │
│  ┌───────────────────────────▼───────────────────┐   │
│  │ BrowserManager                                │   │
│  │  • Playwright browser (Chromium headless)     │   │
│  │  • Keep-alive task                            │   │
│  │  • Session recovery / re-login                │   │
│  └───────────────────────────┬───────────────────┘   │
└──────────────────────────────┼───────────────────────┘
                               │
                               ▼
                ┌──────────────────────────┐
                │ Portale SISTER           │
                │ sister3.agenziaentrate   │
                │ .gov.it                  │
                └──────────────────────────┘
```

### File del progetto

| File | Descrizione |
|------|-------------|
| `main.py` | Applicazione FastAPI: endpoint, modelli Pydantic, `BrowserManager`, `VisuraService`, lifespan |
| `utils.py` | Automazione browser: `login()`, `logout()`, `run_visura()`, `run_visura_immobile()`, `extract_all_sezioni()`, `PageLogger`, `parse_table()` |
| `Dockerfile` | Immagine basata su `python:3.11-slim` con dipendenze per Chromium |
| `docker-compose.yaml` | Orchestrazione con healthcheck, volumi per log, restart automatico |
| `requirements.txt` | Dipendenze Python |
| `pyproject.toml` | Metadati di progetto e dipendenze opzionali di sviluppo |

---

## Prerequisiti

- **Python 3.11+** (testato fino a 3.13)
- **Credenziali SPID** tramite provider Sielte ID con app MySielteID configurata
- **Convenzione SISTER attiva** — l'utente deve avere un account abilitato sul portale SISTER

Per Docker:
- Docker Engine 20+
- Docker Compose v2

---

## Avvio rapido

### Con Docker (raccomandato)

```bash
git clone https://github.com/zornade/visura-api.git
cd visura-api

cp .env.example .env
# Modifica .env con le tue credenziali SPID

docker-compose up -d

# Verifica che il servizio sia attivo
curl http://localhost:8000/health
```

### Installazione manuale

```bash
git clone https://github.com/zornade/visura-api.git
cd visura-api

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Modifica .env con le tue credenziali SPID

uvicorn main:app --host 0.0.0.0 --port 8000
```

All'avvio il servizio:

1. Lancia un browser Chromium headless
2. Esegue il login SPID — **approva la notifica push** sull'app MySielteID entro 120 secondi
3. Naviga fino alla sezione Visure catastali del portale SISTER
4. Avvia il keep-alive e il worker della coda
5. Inizia ad accettare richieste su porta 8000

---

## Configurazione

Crea un file `.env` nella root del progetto (vedi `.env.example`):

```env
# Obbligatorio — Credenziali SPID (Sielte ID)
ADE_USERNAME=RSSMRA85M01H501Z    # Codice fiscale
ADE_PASSWORD=la_tua_password

# Opzionale
LOG_LEVEL=INFO                    # DEBUG | INFO | WARNING | ERROR
```

| Variabile | Obbligatoria | Default | Descrizione |
|-----------|:------------:|---------|-------------|
| `ADE_USERNAME` | ✅ | — | Codice fiscale per il login SPID |
| `ADE_PASSWORD` | ✅ | — | Password SPID (Sielte ID) |
| `LOG_LEVEL` | | `INFO` | Livello di log su console e file |

---

## Endpoint API

### Health check

```
GET /health
```

```json
{
  "status": "healthy",
  "authenticated": true,
  "queue_size": 0
}
```

---

### Visura immobili (Fase 1)

```
POST /visura
```

Cerca tutti gli immobili su una particella catastale. Se `tipo_catasto` è omesso, vengono accodate **due** richieste (Terreni + Fabbricati).

**Request body:**

| Campo | Tipo | Obbligatorio | Default | Descrizione |
|-------|------|:------------:|---------|-------------|
| `provincia` | `string` | ✅ | — | Nome della provincia (es. `"Trieste"`) |
| `comune` | `string` | ✅ | — | Nome del comune (es. `"TRIESTE"`) |
| `foglio` | `string` | ✅ | — | Numero foglio |
| `particella` | `string` | ✅ | — | Numero particella |
| `sezione` | `string` | | `null` | Sezione censuaria (se presente) |
| `tipo_catasto` | `string` | | `null` | `"T"` = Terreni, `"F"` = Fabbricati. Se omesso: entrambi |

**Esempio:**

```bash
curl -X POST http://localhost:8000/visura \
  -H "Content-Type: application/json" \
  -d '{
    "provincia": "Trieste",
    "comune": "TRIESTE",
    "foglio": "9",
    "particella": "166",
    "tipo_catasto": "F"
  }'
```

**Risposta:**

```json
{
  "request_ids": ["req_F_1709312400000"],
  "tipos_catasto": ["F"],
  "status": "queued",
  "message": "Richieste aggiunte alla coda per TRIESTE F.9 P.166"
}
```

> **Nota:** per i Terreni (`T`) gli intestati vengono estratti automaticamente. Per i Fabbricati (`F`) vengono restituiti solo gli immobili — per ottenere gli intestati di un singolo fabbricato, usa la Fase 2.

---

### Visura intestati (Fase 2)

```
POST /visura/intestati
```

Estrae i titolari (intestati) di uno specifico immobile. Per i Fabbricati è necessario specificare il `subalterno`.

**Request body:**

| Campo | Tipo | Obbligatorio | Default | Descrizione |
|-------|------|:------------:|---------|-------------|
| `provincia` | `string` | ✅ | — | Nome della provincia |
| `comune` | `string` | ✅ | — | Nome del comune |
| `foglio` | `string` | ✅ | — | Numero foglio |
| `particella` | `string` | ✅ | — | Numero particella |
| `tipo_catasto` | `string` | ✅ | — | `"T"` o `"F"` |
| `subalterno` | `string` | Per `F` | `null` | Subalterno (obbligatorio per Fabbricati, vietato per Terreni) |
| `sezione` | `string` | | `null` | Sezione censuaria |

**Esempio:**

```bash
curl -X POST http://localhost:8000/visura/intestati \
  -H "Content-Type: application/json" \
  -d '{
    "provincia": "Trieste",
    "comune": "TRIESTE",
    "foglio": "9",
    "particella": "166",
    "tipo_catasto": "F",
    "subalterno": "3"
  }'
```

**Risposta:**

```json
{
  "request_id": "intestati_F_3_1709312500000",
  "tipo_catasto": "F",
  "subalterno": "3",
  "status": "queued",
  "message": "Richiesta intestati aggiunta alla coda per TRIESTE F.9 P.166 Sub.3",
  "queue_position": 1
}
```

---

### Polling risultati

```
GET /visura/{request_id}
```

Recupera lo stato e i dati di una richiesta precedentemente accodata.

| Status | Significato |
|--------|-------------|
| `processing` | La richiesta è in coda o in esecuzione |
| `completed` | Dati disponibili nel campo `data` |
| `error` | Errore — dettagli nel campo `error` |

**Risposta completata (Fase 1):**

```json
{
  "request_id": "req_F_1709312400000",
  "tipo_catasto": "F",
  "status": "completed",
  "data": {
    "immobili": [
      {
        "Foglio": "9",
        "Particella": "166",
        "Sub": "3",
        "Categoria": "A/2",
        "Classe": "5",
        "Consistenza": "4.5",
        "Rendita": "500,00",
        "Indirizzo": "VIA ROMA 10",
        "Partita": "12345"
      }
    ],
    "results": [
      {
        "result_index": 1,
        "immobile": { },
        "intestati": []
      }
    ],
    "total_results": 1,
    "intestati": []
  },
  "error": null,
  "timestamp": "2026-03-06T10:30:00"
}
```

**Risposta completata (Fase 2 — intestati):**

```json
{
  "request_id": "intestati_F_3_1709312500000",
  "status": "completed",
  "data": {
    "immobile": {
      "Foglio": "9",
      "Particella": "166",
      "Sub": "3"
    },
    "intestati": [
      {
        "Nominativo o denominazione": "ROSSI MARIO",
        "Codice fiscale": "RSSMRA85M01H501Z",
        "Titolarità": "Proprietà per 1/1"
      }
    ],
    "total_intestati": 1
  }
}
```

**Risposta con nessuna corrispondenza:**

```json
{
  "request_id": "req_F_1709312400000",
  "status": "completed",
  "data": {
    "immobili": [],
    "results": [],
    "total_results": 0,
    "intestati": [],
    "error": "NESSUNA CORRISPONDENZA TROVATA"
  }
}
```

---

### Sezioni territoriali

```
POST /sezioni/extract
```

Estrae le sezioni censuarie per tutte le province e comuni d'Italia. **Operazione molto lenta** — può richiedere ore.

| Campo | Tipo | Default | Descrizione |
|-------|------|---------|-------------|
| `tipo_catasto` | `string` | `"T"` | `"T"` o `"F"` |
| `max_province` | `int` | `200` | Numero massimo di province da processare (1–200) |

---

### Shutdown

```
POST /shutdown
```

Esegue un shutdown controllato: logout dal portale SISTER e chiusura del browser.

---

## Esempi d'uso

### Flusso completo con cURL

```bash
# 1. Avvia l'estrazione dei fabbricati
curl -s -X POST http://localhost:8000/visura \
  -H "Content-Type: application/json" \
  -d '{"provincia":"Roma","comune":"ROMA","foglio":"100","particella":"50","tipo_catasto":"F"}' \
  | jq .

# Salva il request_id dalla risposta, poi:

# 2. Polling risultati (ripeti fino a status != "processing")
curl -s http://localhost:8000/visura/req_F_1709312400000 | jq .

# 3. Prendi un subalterno dai risultati e chiedi gli intestati
curl -s -X POST http://localhost:8000/visura/intestati \
  -H "Content-Type: application/json" \
  -d '{"provincia":"Roma","comune":"ROMA","foglio":"100","particella":"50","tipo_catasto":"F","subalterno":"3"}' \
  | jq .

# 4. Polling intestati
curl -s http://localhost:8000/visura/intestati_F_3_1709312500000 | jq .
```

### Client Python

```python
import requests, time

BASE = "http://localhost:8000"

def visura_completa(provincia, comune, foglio, particella, tipo="F", subalterno=None):
    # Fase 1: immobili
    r = requests.post(f"{BASE}/visura", json={
        "provincia": provincia, "comune": comune,
        "foglio": foglio, "particella": particella,
        "tipo_catasto": tipo
    }).json()

    rid = r["request_ids"][0]

    # Polling
    while True:
        res = requests.get(f"{BASE}/visura/{rid}").json()
        if res["status"] != "processing":
            break
        time.sleep(5)

    if res["status"] == "error":
        raise Exception(res["error"])

    immobili = res["data"]["immobili"]
    print(f"Trovati {len(immobili)} immobili")

    if not subalterno or tipo == "T":
        return res["data"]

    # Fase 2: intestati per uno specifico subalterno
    r2 = requests.post(f"{BASE}/visura/intestati", json={
        "provincia": provincia, "comune": comune,
        "foglio": foglio, "particella": particella,
        "tipo_catasto": tipo, "subalterno": subalterno
    }).json()

    rid2 = r2["request_id"]

    while True:
        res2 = requests.get(f"{BASE}/visura/{rid2}").json()
        if res2["status"] != "processing":
            break
        time.sleep(5)

    return res2["data"]


# Esempio
dati = visura_completa("Roma", "ROMA", "100", "50", tipo="F", subalterno="3")
print(dati)
```

---

## Logging e debug

Il servizio produce due livelli di logging:

### Log testuale

Scritto su **stdout** e su **file** in `logs/visura.log`. Contiene l'intero flusso operativo: login, navigazione, estrazione dati, errori.

```bash
# Avvia con log dettagliati
LOG_LEVEL=DEBUG uvicorn main:app --host 0.0.0.0 --port 8000
```

### Log HTML delle pagine (`PageLogger`)

Ogni pagina visitata dal browser viene salvata come file HTML su disco. Questo permette di ispezionare esattamente ciò che il browser ha visto in ogni punto del flusso — utile per debug, audit e sviluppo.

**Struttura directory:**

```
logs/pages/
└── 2026-03-06_16-28-24/          ← session_id (reset ad ogni avvio del server)
    ├── login/
    │   ├── 01_goto_login.html
    │   ├── 02_entra_con_spid.html
    │   ├── 03_sielte.html
    │   ├── ...
    │   └── 15_conferma_lettura.html
    ├── visura/
    │   ├── 01_scelta_servizio.html
    │   ├── 02_provincia_applicata.html
    │   ├── 03_immobile.html
    │   ├── 04_ricerca.html
    │   ├── 05_conferma_subalterno.html
    │   ├── 06_risultati.html
    │   └── 07_intestati_r1.html
    ├── visura_002/                ← seconda visura nella stessa sessione
    │   └── ...
    ├── logout/
    │   ├── 01_before_logout.html
    │   └── 02_after_logout.html
    └── recovery/
        └── ...
```

Ogni file HTML include in testa dei commenti con metadati:

```html
<!-- URL: https://sister3.agenziaentrate.gov.it/Visure/... -->
<!-- Step: ricerca -->
<!-- Timestamp: 2026-03-06T16:30:45 -->
```

> **Privacy:** la directory `logs/pages/` è nel `.gitignore` perché i file HTML contengono dati personali (codice fiscale, intestatari, indirizzi). Non committare mai questi file.

---

## Dettagli tecnici

### Gestione della sessione

| Meccanismo | Intervallo | Descrizione |
|------------|------------|-------------|
| **Light keep-alive** | 30 secondi | Mouse move sulla pagina per evitare timeout idle |
| **Session refresh** | 5 minuti | Naviga a `SceltaServizio.do` e verifica che la sessione sia ancora attiva |
| **Recovery** | Su errore | Navigazione diretta → percorso interno → re-login SPID completo |

### Coda di elaborazione

- Unica `asyncio.Queue` con worker sequenziale
- Pausa di **2 secondi** tra una richiesta e l'altra
- Pausa di **5 secondi** dopo un errore
- I risultati restano in memoria (`response_store`) fino al riavvio del servizio
- Il client fa polling su `GET /visura/{request_id}` — restituisce `"processing"` finché il risultato non è pronto

### Graceful shutdown

Quando uvicorn riceve `SIGINT` o `SIGTERM`:

1. Il lifespan `shutdown` viene invocato da uvicorn
2. `logout()` clicca "Esci" sul portale SISTER
3. `close()` clicca "Torna al portale", chiude il browser context, chiude Chromium

Il browser viene lanciato con `handle_sigint=False, handle_sigterm=False` per impedire che Chromium intercetti i segnali prima che il logout sia completato.

### Flusso di autenticazione SPID (Sielte ID)

1. Naviga alla pagina di login dell'Agenzia delle Entrate
2. Clicca "Entra con SPID" → seleziona provider Sielte ID
3. Inserisce codice fiscale (con CapsLock attivo) e password
4. Clicca "Prosegui" → seleziona invio notifica push
5. Clicca "Autorizza" → **attende fino a 120 secondi** l'approvazione sull'app MySielteID
6. Cerca "SISTER" tra i servizi → clicca "Vai al servizio"
7. Verifica assenza di sessione bloccata ("Utente già in sessione")
8. Naviga: Conferma → Consultazioni e Certificazioni → Visure catastali → Conferma Lettura

### Flusso della visura

1. Naviga a `SceltaServizio.do` — seleziona provincia — clicca Applica
2. Clicca "Immobile" — seleziona tipo catasto (`T`/`F`), comune, compila foglio e particella
3. Clicca "Ricerca" — gestisce eventuale "conferma assenza subalterno"
4. Se "NESSUNA CORRISPONDENZA TROVATA" → ritorna risultato vuoto con `.error`
5. Estrae la tabella immobili (`table.listaIsp4`)
6. Per ogni immobile (radio button): seleziona → clicca "Intestati" → estrae tabella intestatari → torna indietro
7. Gli immobili con `Partita = "Soppressa"` vengono inclusi ma senza estrazione intestati

---

## Sviluppo e contribuzione

### Setup ambiente di sviluppo

```bash
git clone https://github.com/zornade/visura-api.git
cd visura-api

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install -e ".[dev]"            # pytest, black, ruff
playwright install chromium

cp .env.example .env
# Configura le credenziali
```

### Struttura del codice

**`main.py`** contiene:
- Modelli Pydantic di input (`VisuraInput`, `VisuraIntestatiInput`, `SezioniExtractionRequest`)
- Dataclass interne (`VisuraRequest`, `VisuraResponse`, `VisuraIntestatiRequest`)
- Eccezioni custom (`VisuraError`, `AuthenticationError`, `BrowserError`, `ValidationError`)
- `BrowserManager` — gestione browser, login, keep-alive, session recovery
- `VisuraService` — coda, worker, store risultati
- Lifespan FastAPI (startup/shutdown)
- Tutti gli endpoint REST

**`utils.py`** contiene:
- `PageLogger` — salva HTML di ogni pagina visitata, organizzato per sessione/flusso/step
- `login(page, username, password)` — flusso SPID completo (15 step, ciascuno loggato)
- `logout(page)` — cerca e clicca "Esci" con fallback su più selettori CSS
- `run_visura(page, ...)` — visura completa: selezione provincia → estrazione intestati
- `run_visura_immobile(page, ...)` — visura mirata per un singolo fabbricato con subalterno
- `extract_all_sezioni(page, ...)` — iterazione su tutte le province/comuni per estrarre sezioni
- `find_best_option_match(page, selector, text)` — fuzzy matching a 5 livelli su dropdown `<select>`
- `parse_table(html)` — parsing tabelle HTML con BeautifulSoup → lista di dizionari

### Aggiungere un provider SPID

Il login è implementato nella funzione `login()` di `utils.py`. Per supportare un altro provider:

1. Modifica il selettore del provider (attualmente `a[href*="sielte"]`)
2. Adatta il form di inserimento credenziali (ogni provider ha layout diversi)
3. Gestisci il metodo di approvazione (push notification, OTP, etc.)

### Convenzioni per il logging HTML

Quando aggiungi nuovi flussi o step, usa `PageLogger`:

```python
logger = PageLogger("nome_flusso")    # Crea logger per questo flusso
await logger.log(page, "nome_step")   # Salva HTML della pagina corrente
```

I file vengono numerati automaticamente (`01_nome_step.html`, `02_...`). Flussi ripetuti nella stessa sessione ricevono un suffisso incrementale (`visura`, `visura_002`, `visura_003`, ...).

### Formattazione e linting

```bash
black .           # formattazione automatica
ruff check .      # controllo linting
```

### Test

```bash
python -m pytest -v
```

### Docker

```bash
docker-compose up --build         # build e avvio
docker-compose logs -f             # segui i log
docker-compose down                # stop e rimozione container
```

### Linee guida

Leggi [CONTRIBUTING.md](CONTRIBUTING.md) per il dettaglio completo. In breve:

- Crea un branch dal `main` con un nome descrittivo (`fix/...`, `feat/...`)
- Ogni modifica significativa deve includere i log `PageLogger` nei punti critici
- **Mai committare** file da `logs/` — contengono dati personali
- Rimuovi le credenziali dai log prima di condividerli in una issue

---

## Risoluzione dei problemi

| Problema | Causa probabile | Soluzione |
|----------|----------------|----------|
| Il login non parte | Credenziali mancanti | Verifica `ADE_USERNAME` e `ADE_PASSWORD` nel file `.env` |
| Timeout su "Autorizza" | Push non approvata in tempo | Approva la notifica MySielteID entro 120 secondi |
| "Utente già in sessione" | Sessione precedente non chiusa | Attendi qualche minuto o chiudi manualmente dal portale |
| Sessione scaduta durante visura | Inattività prolungata | Il servizio tenta il recovery automatico; se fallisce, ri-esegue il login |
| "NESSUNA CORRISPONDENZA TROVATA" | Dati catastali inesistenti | Verifica foglio, particella, tipo catasto e comune |
| Risposte lente | Coda piena | Controlla `queue_size` con `GET /health` |
| Chromium non si avvia in Docker | Dipendenze di sistema mancanti | Usa il Dockerfile fornito che include tutte le librerie necessarie |
| Log HTML vuoti o mancanti | Errore durante il salvataggio | Controlla i permessi sulla directory `logs/pages/` |

Per debug approfondito, ispeziona i file HTML in `logs/pages/` — mostrano esattamente cosa vedeva il browser in ogni step.

---

## Autore

Sviluppato da [zornade](https://zornade.com).

---

## Licenza

Distribuito sotto licenza [GNU Affero General Public License v3.0](LICENSE).

---

*Ultimo aggiornamento: marzo 2026*
