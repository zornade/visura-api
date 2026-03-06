# Come contribuire a Visura API

Grazie per il tuo interesse nel contribuire a Visura API! Questo documento fornisce le linee guida per partecipare al progetto.

## Codice di condotta

Partecipando a questo progetto accetti di rispettare il nostro [Codice di Condotta](CODE_OF_CONDUCT.md).

## Come segnalare un problema

1. Controlla prima le [issue esistenti](https://github.com/zornade/visura-api/issues) per assicurarti che il problema non sia già stato segnalato
2. Se è un problema di sicurezza, **NON** aprire una issue pubblica — segui le istruzioni in [SECURITY.md](SECURITY.md)
3. Apri una nuova issue usando il template appropriato, fornendo:
   - Descrizione chiara del problema
   - Passi per riprodurlo
   - Comportamento atteso vs comportamento osservato
   - Versione di Python e del sistema operativo
   - Log pertinenti (rimuovi sempre le credenziali!)

## Come proporre modifiche

### Preparare l'ambiente di sviluppo

```bash
# Clona il repository
git clone https://github.com/zornade/visura-api.git
cd visura-api

# Crea un ambiente virtuale
python -m venv .venv
source .venv/bin/activate

# Installa le dipendenze (incluse quelle di sviluppo)
pip install -r requirements.txt
pip install pytest pytest-cov black ruff

# Installa Playwright
playwright install chromium

# Copia il file di esempio per le variabili d'ambiente
cp .env.example .env
# Modifica .env con le tue credenziali
```

### Flusso di lavoro

1. **Crea un fork** del repository
2. **Crea un branch** dal `main` con un nome descrittivo:
   ```bash
   git checkout -b fix/correzione-login
   git checkout -b feat/nuova-funzionalita
   ```
3. **Scrivi il codice** seguendo le convenzioni del progetto
4. **Aggiungi test** per le modifiche, se applicabile
5. **Esegui i test** per verificare che nulla sia rotto:
   ```bash
   python -m pytest test_*.py -v
   ```
6. **Fai commit** con messaggi chiari in italiano:
   ```bash
   git commit -m "fix: corretto errore nella selezione della provincia"
   git commit -m "feat: aggiunto supporto per ricerca per codice fiscale"
   ```
7. **Apri una Pull Request** verso il branch `main`

### Convenzioni per i messaggi di commit

Usiamo il formato [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` — nuova funzionalità
- `fix:` — correzione di un errore
- `docs:` — modifiche alla documentazione
- `refactor:` — ristrutturazione del codice senza cambi funzionali
- `test:` — aggiunta o modifica di test
- `ci:` — modifiche alla configurazione CI/CD

### Stile del codice

- Seguiamo le convenzioni **PEP 8**
- Usa **Black** per la formattazione automatica: `black .`
- Usa **Ruff** per il linting: `ruff check .`
- Aggiungi type hints dove possibile
- Documenta le funzioni pubbliche con docstring
- Commenti e log in **italiano**

### Cosa NON committare mai

- File `.env` con credenziali reali
- Log con dati personali (codici fiscali, nomi, ecc.)
- File temporanei di debug (`/tmp/debug_*.html`)
- Cartelle di cache (`__pycache__/`, `.venv/`)

## Aree dove servono contributi

- Miglioramento dei test automatici
- Documentazione (README, guide, esempi)
- Gestione degli errori e resilienza
- Ottimizzazione delle performance
- Supporto per nuove funzionalità del portale SISTER

## Domande?

Apri una [discussione](https://github.com/zornade/visura-api/discussions) o una issue con l'etichetta `domanda`.

Grazie per il tuo contributo!
