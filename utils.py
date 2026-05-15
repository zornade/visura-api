# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zornade (https://zornade.com)
# See LICENSE, NOTICE, and COMMERCIAL-LICENSE.md at the repository root.

import asyncio
import logging
import os
import re
import time
import unicodedata
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

DEFAULT_PAGES_LOG_DIR = "./logs/pages"
FALLBACK_PAGES_LOG_DIR = "/tmp/visura-api/logs/pages"


def _ensure_writable_dir(path: str) -> bool:
    """Crea ``path`` (se serve) e verifica che sia scrivibile.

    Esegue una scrittura di prova (``.write_probe``) e la rimuove. Restituisce
    ``True`` solo se entrambe le operazioni hanno successo.
    """
    try:
        os.makedirs(path, exist_ok=True)
        probe_path = os.path.join(path, ".write_probe")
        with open(probe_path, "w", encoding="utf-8") as probe:
            probe.write("ok")
        os.remove(probe_path)
        return True
    except (PermissionError, OSError):
        return False


def _resolve_pages_log_dir() -> Optional[str]:
    """Risolve una directory scrivibile per il logging delle pagine HTML.

    Ordine di priorità:
      1. variabile d'ambiente ``PAGES_LOG_DIR`` se valorizzata;
      2. variabile di modulo ``PAGES_LOG_DIR`` (default ``./logs/pages``,
         sovrascrivibile per test e configurazioni custom);
      3. fallback ``/tmp/visura-api/logs/pages`` (utile in container con
         filesystem applicativo read-only o senza volume montato);
      4. ``None`` se nessuna directory è scrivibile (il logging viene disabilitato).
    """
    preferred_dir = os.getenv("PAGES_LOG_DIR") or PAGES_LOG_DIR
    if _ensure_writable_dir(preferred_dir):
        return preferred_dir

    if _ensure_writable_dir(FALLBACK_PAGES_LOG_DIR):
        print(f"[PAGE_LOG] Directory '{preferred_dir}' non scrivibile, " f"uso fallback '{FALLBACK_PAGES_LOG_DIR}'")
        return FALLBACK_PAGES_LOG_DIR

    print(
        f"[PAGE_LOG] Nessuna directory scrivibile (provate: '{preferred_dir}', "
        f"'{FALLBACK_PAGES_LOG_DIR}'). Logging delle pagine disabilitato."
    )
    return None


# Compatibilità retroattiva: alcuni consumatori esterni potrebbero importare
# o monkey-patchare PAGES_LOG_DIR. È usata da ``_resolve_pages_log_dir`` come
# default se la variabile d'ambiente omonima non è impostata.
PAGES_LOG_DIR = DEFAULT_PAGES_LOG_DIR

# Logger di modulo (riusato da consumatori esterni e da _resolve_pages_log_dir).
logger = logging.getLogger(__name__)


def parse_table(html):
    soup = BeautifulSoup(html, "html.parser")
    headers = [th.get_text(strip=True) for th in soup.find_all("th")]
    rows = []
    for tr in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cells:
            # Se ci sono meno celle che header, aggiungi celle vuote
            while len(cells) < len(headers):
                cells.append("")
            # strict=False: cells is pre-padded to len(headers); extra cells
            # (rare: malformed HTML with more <td> than <th>) are intentionally dropped.
            rows.append(dict(zip(headers, cells, strict=False)))
    return rows


class PageLogger:
    """Salva l'HTML di ogni pagina visitata, organizzato per sessione e flusso.

    Struttura directory:
        logs/pages/{session_id}/{flow_name}/01_step.html
        logs/pages/{session_id}/{flow_name}/02_step.html
        ...

    Se lo stesso flusso viene eseguito più volte nella stessa sessione,
    le cartelle successive vengono numerate: visura, visura_002, visura_003, ecc.
    """

    _session_id: str = None
    _flow_counters: dict = {}
    _pages_log_dir: Optional[str] = None

    @classmethod
    def reset_session(cls):
        """Resetta la sessione (da chiamare ad ogni avvio del server)."""
        cls._session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        cls._flow_counters = {}
        cls._pages_log_dir = None

    def __init__(self, flow_name: str):
        if PageLogger._session_id is None:
            PageLogger._session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        # Risolvi la directory di log la prima volta che serve.
        if PageLogger._pages_log_dir is None:
            PageLogger._pages_log_dir = _resolve_pages_log_dir()

        # Contatore per differenziare flussi ripetuti (visura_002, visura_003…)
        count = PageLogger._flow_counters.get(flow_name, 0) + 1
        PageLogger._flow_counters[flow_name] = count

        self.flow_name = flow_name
        self.step = 0

        dir_name = flow_name if count == 1 else f"{flow_name}_{count:03d}"

        # Se nessuna directory è scrivibile, disabilita il logging silenziosamente:
        # base_dir resta ``None`` e ``log()`` farà no-op.
        if PageLogger._pages_log_dir is None:
            self.base_dir: Optional[str] = None
            return

        self.base_dir = os.path.join(PageLogger._pages_log_dir, PageLogger._session_id, dir_name)
        try:
            os.makedirs(self.base_dir, exist_ok=True)
        except (PermissionError, OSError) as e:
            print(
                f"[PAGE_LOG] Errore creando '{self.base_dir}': {e}. "
                "Logging delle pagine disabilitato per questo flusso."
            )
            self.base_dir = None

    async def log(self, page: Page, step_name: str) -> None:
        """Salva l'HTML corrente della pagina su disco (no-op se disabilitato).

        Disabilitabile via env ``LOG_PAGES=0`` (default: ``1``). Quando off,
        ritorna immediatamente senza chiamare ``page.content()`` né scrivere
        su disco — riduce ~0.5-2s/richiesta su success path.
        """
        self.step += 1
        if os.getenv("LOG_PAGES", "1") != "1":
            return
        if self.base_dir is None:
            return
        try:
            if not page or page.is_closed():
                print(f"[PAGE_LOG] {self.flow_name}/{step_name}: pagina chiusa, skip")
                return
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            url = page.url
            html = await page.content()
            safe_name = re.sub(r"[^\w\-]", "_", step_name)
            filename = f"{self.step:02d}_{safe_name}.html"
            filepath = os.path.join(self.base_dir, filename)
            # Write off the event loop: open()/write() are blocking syscalls
            # and were previously freezing the asyncio loop for the duration
            # of the disk write (ASYNC230). We use asyncio.to_thread so any
            # I/O slowness (e.g. EBS, full disk) cannot starve the loop.
            payload = (
                f"<!-- URL: {url} -->\n"
                f"<!-- Step: {step_name} -->\n"
                f"<!-- Timestamp: {datetime.now().isoformat()} -->\n\n"
                f"{html}"
            )

            def _write_file() -> None:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(payload)

            await asyncio.to_thread(_write_file)
            print(f"[PAGE_LOG] {self.flow_name}/{filename}")
        except Exception as e:
            print(f"[PAGE_LOG] Errore salvataggio {step_name}: {e}")


async def _login_sielte(page: Page, logger: PageLogger, username: str, password: str) -> None:
    """Esegue l'autenticazione SPID tramite provider Sielte ID (MySielteID).

    Credenziali richieste:
        ADE_USERNAME — codice fiscale o partita IVA dell'account Sielte
        ADE_PASSWORD — password dell'account Sielte

    Il secondo fattore è gestito tramite notifica push sull'app MySielteID:
    l'utente approva sull'app e clicca 'Autorizza' (timeout 120s).
    """
    step = "sielte_id"
    try:
        print("[LOGIN] Clicco 'Sielte ID'...")
        await page.locator('a[href*="sielte"]').click()
        await logger.log(page, "sielte_id")

        step = "username"
        print("[LOGIN] Inserisco username...")
        await page.get_by_role("textbox", name="Codice Fiscale / Partita IVA").press("CapsLock")
        await page.get_by_role("textbox", name="Codice Fiscale / Partita IVA").fill(username)
        await logger.log(page, "username")

        step = "password"
        print("[LOGIN] Inserisco password...")
        await page.get_by_role("textbox", name="Password").click()
        await page.get_by_role("textbox", name="Password").fill(password)

        step = "prosegui"
        print("[LOGIN] Clicco 'Prosegui'...")
        await page.get_by_role("button", name="Prosegui").click()
        await logger.log(page, "prosegui")

        step = "notifica_push"
        print("[LOGIN] Cerco link notifica (può non esserci)...")
        try:
            await page.get_by_role(
                "link", name="Utilizza il le notifiche Ricevi una notifica sull'app MySielteID"
            ).click(timeout=4000)
            print("[LOGIN] Cliccato link notifica (testo completo).")
        except PlaywrightTimeoutError:
            print("[LOGIN] Link notifica con testo completo non trovato, provo fallback...")
            try:
                await page.locator(
                    'a.link-sso:has(img[alt="Utilizza il le notifiche"]):has(p:text("Ricevi una notifica sull\'app MySielteID"))'
                ).click(timeout=4000)
                print("[LOGIN] Cliccato link notifica (fallback DOM selector).")
            except PlaywrightTimeoutError:
                print("[LOGIN] Nessun link notifica trovato, continuo...")
        await logger.log(page, "notifica_push")

        step = "autorizza"
        # Fail-fast: il pulsante 'Autorizza' appare solo se username/password
        # sono corretti e l'autenticazione e' arrivata allo step push. Se le
        # credenziali sono sbagliate la pagina rimane sulla form di login con
        # un messaggio d'errore: in tal caso vogliamo fallire entro pochi
        # secondi invece di aspettare 120s. Soglia configurabile via env
        # ``LOGIN_AUTORIZZA_APPEAR_TIMEOUT_S`` (default 15).
        appear_timeout_s = int(os.getenv("LOGIN_AUTORIZZA_APPEAR_TIMEOUT_S", "15"))
        print(
            f"[LOGIN] Attendo pulsante 'Autorizza' (max {appear_timeout_s}s) — "
            f"se non appare = credenziali probabilmente errate"
        )
        try:
            await page.get_by_role("button", name="Autorizza").wait_for(
                state="visible", timeout=appear_timeout_s * 1000
            )
        except PlaywrightTimeoutError as e:
            current_url = page.url
            await logger.log(page, f"ERRORE_sielte_{step}_autorizza_non_apparso")
            raise RuntimeError(
                f"Login Sielte fallito: pulsante 'Autorizza' non apparso entro "
                f"{appear_timeout_s}s. Credenziali probabilmente errate o flusso "
                f"Sielte modificato. URL corrente: {current_url}"
            ) from e

        print("[LOGIN] Clicco 'Autorizza'... (attendo conferma notifica push, timeout 120s)")
        await page.get_by_role("button", name="Autorizza").click(timeout=120000)
        await logger.log(page, "autorizza")
    except Exception:
        await logger.log(page, f"ERRORE_sielte_{step}")
        raise


async def _login_poste(page: Page, logger: PageLogger, username: str, password: str) -> None:
    """Esegue l'autenticazione SPID tramite provider Poste Italiane (PosteID).

    Credenziali richieste:
        POSTE_USERNAME — indirizzo email dell'account PosteID
        POSTE_PASSWORD — password dell'account PosteID

    Il secondo fattore è gestito tramite notifica push sull'app PosteID:
    l'utente approva sull'app e la pagina si reindirizza automaticamente
    sul dominio agenziaentrate.gov.it (timeout 120s).
    """
    step = "poste_id"
    try:
        print("[LOGIN] Clicco 'Poste Italiane'...")
        await page.locator('a[href*="poste"]').click()
        await logger.log(page, "poste_id")

        step = "username"
        print("[LOGIN] Inserisco email PosteID...")
        await page.get_by_role("textbox", name="Indirizzo e-mail").fill(username)
        await logger.log(page, "username")

        step = "password"
        print("[LOGIN] Inserisco password PosteID...")
        await page.get_by_role("textbox", name="Password").fill(password)

        step = "avanti"
        print("[LOGIN] Clicco 'Avanti'...")
        await page.get_by_role("button", name="Avanti").click()
        await logger.log(page, "avanti")

        step = "attesa_app"
        # Fail-fast: dopo 'Avanti' PosteID transita a uno step di approvazione
        # push e poi reindirizza al dominio agenziaentrate. Se username/password
        # sono sbagliate la pagina mostra subito un errore senza mai partire la
        # push; aspettare 120s prima di accorgersene non e' utile. Diamo prima
        # ``LOGIN_POSTE_PUSH_APPEAR_TIMEOUT_S`` per il cambio di URL/stato e
        # poi il timeout lungo per l'approvazione vera e propria.
        appear_timeout_s = int(os.getenv("LOGIN_POSTE_PUSH_APPEAR_TIMEOUT_S", "15"))
        print(
            f"[LOGIN] Attendo transizione push PosteID (max {appear_timeout_s}s) — "
            f"se rimane sulla form = credenziali probabilmente errate"
        )
        try:
            # Attendiamo che l'URL CAMBI dalla pagina di login. wait_for_url
            # con un pattern wildcard fallisce se restiamo sullo stesso URL.
            await page.wait_for_function(
                "url => !window.location.href.includes('login') && !window.location.href.includes('Login')",
                timeout=appear_timeout_s * 1000,
            )
        except PlaywrightTimeoutError as e:
            current_url = page.url
            await logger.log(page, f"ERRORE_poste_{step}_push_non_partita")
            raise RuntimeError(
                f"Login PosteID fallito: la pagina non e' uscita dal form di login "
                f"entro {appear_timeout_s}s. Credenziali probabilmente errate o "
                f"flusso PosteID modificato. URL corrente: {current_url}"
            ) from e

        print("[LOGIN] Attendo approvazione sull'app PosteID (timeout 120s)...")
        # PosteID reindirizza automaticamente dopo l'approvazione sull'app:
        # aspettiamo che il browser torni sul dominio agenziaentrate.
        await page.wait_for_url("**/agenziaentrate.gov.it/**", timeout=120000)
        await logger.log(page, "redirect_post_auth")
    except Exception:
        await logger.log(page, f"ERRORE_poste_{step}")
        raise


async def login(page: Page):
    """Esegue il login completo (SPID + navigazione fino a 'Visure catastali').

    Il provider di autenticazione è selezionato dalla variabile d'ambiente
    ``SPID_PROVIDER`` (case-insensitive):

    * ``sielte`` (default) — SPID Sielte ID, credenziali ``ADE_USERNAME`` /
      ``ADE_PASSWORD``.
    * ``poste`` — SPID PosteID, credenziali ``POSTE_USERNAME`` /
      ``POSTE_PASSWORD``.
    * ``sister`` — login diretto SISTER tramite il tab dedicato della pagina
      ADE, credenziali ``SISTER_USERNAME`` / ``SISTER_PASSWORD``. Pensato per
      l'**intestatario** di una convenzione SISTER che vuole automatizzare le
      proprie consultazioni con le proprie credenziali nominali (vedi README,
      sezione "Provider di autenticazione supportati").

    Con i provider SPID il flusso prosegue identico: ricerca del servizio
    SISTER, conferma, navigazione fino a "Visure catastali → Conferma Lettura".
    Con il provider ``sister`` il login diretto atterra già nell'area del
    servizio e quei passi vengono saltati.
    """
    spid_provider = os.getenv("SPID_PROVIDER", "sielte").lower()

    if spid_provider == "sielte":
        username = os.getenv("ADE_USERNAME")
        password = os.getenv("ADE_PASSWORD")
        if not username or not password:
            raise ValueError("ADE_USERNAME and ADE_PASSWORD environment variables must be set")
    elif spid_provider == "poste":
        username = os.getenv("POSTE_USERNAME")
        password = os.getenv("POSTE_PASSWORD")
        if not username or not password:
            raise ValueError("POSTE_USERNAME and POSTE_PASSWORD environment variables must be set")
    elif spid_provider == "sister":
        username = os.getenv("SISTER_USERNAME")
        password = os.getenv("SISTER_PASSWORD")
        if not username or not password:
            raise ValueError("SISTER_USERNAME and SISTER_PASSWORD environment variables must be set")
    else:
        raise ValueError(
            f"SPID_PROVIDER non supportato: '{spid_provider}'. " "Valori validi: 'sielte', 'poste', 'sister'"
        )

    logger = PageLogger("login")
    step = "init"

    try:
        step = "goto_login"
        print("[LOGIN] Navigo alla pagina di login...")
        await page.goto("https://iampe.agenziaentrate.gov.it/sam/UI/Login?realm=/agenziaentrate")
        await logger.log(page, "goto_login")

        if spid_provider == "sister":
            # Login diretto SISTER: bypass completo della navigazione ADE portal
            # (cerca SISTER → Vai al servizio → Conferma → Consultazioni →
            # Visure catastali → Conferma Lettura) perché l'area servizio si
            # apre già dopo l'autenticazione SISTER nominale.
            step = "provider_sister"
            await _login_sister_direct(page, logger, username, password)
            return

        step = "entra_con_spid"
        print("[LOGIN] Clicco 'Entra con SPID'...")
        await page.get_by_role("button", name="Entra con SPID").click()
        await logger.log(page, "entra_con_spid")

        step = f"provider_{spid_provider}"
        print(f"[LOGIN] Autenticazione tramite provider: {spid_provider}...")
        if spid_provider == "sielte":
            await _login_sielte(page, logger, username, password)
        else:  # poste (gli altri valori sono già stati respinti sopra)
            await _login_poste(page, logger, username, password)

        step = "cerca_sister"
        print("[LOGIN] Cerco servizio SISTER...")
        await page.get_by_role("textbox", name="Cerca il servizio").click()
        await page.get_by_role("textbox", name="Cerca il servizio").fill("SISTER")
        await page.get_by_role("textbox", name="Cerca il servizio").press("Enter")
        await logger.log(page, "cerca_sister")

        step = "vai_al_servizio"
        print("[LOGIN] Clicco 'Vai al servizio'...")
        await page.get_by_role("link", name="Vai al servizio").first.click()

        step = "controllo_sessione"
        print("[LOGIN] Attendo caricamento pagina...")
        await page.wait_for_load_state("domcontentloaded")
        await logger.log(page, "vai_al_servizio")
        print("[LOGIN] Controllo blocco sessione...")
        content = await page.content()
        url = page.url
        if "Utente gia' in sessione" in content or "error_locked.jsp" in url:
            print("[LOGIN][ERRORE] Utente già in sessione su un'altra postazione!")
            raise Exception("Utente già in sessione su un'altra postazione")

        step = "conferma"
        print("[LOGIN] Clicco 'Conferma'...")
        await page.get_by_role("button", name="Conferma").click()
        await logger.log(page, "conferma")

        step = "consultazioni"
        print("[LOGIN] Clicco 'Consultazioni e Certificazioni'...")
        await page.get_by_role("link", name="Consultazioni e Certificazioni").click()
        await logger.log(page, "consultazioni")

        step = "visure_catastali"
        print("[LOGIN] Clicco 'Visure catastali'...")
        await page.get_by_role("link", name="Visure catastali").click()
        await logger.log(page, "visure_catastali")

        step = "conferma_lettura"
        print("[LOGIN] Clicco 'Conferma Lettura'...")
        await page.get_by_role("link", name="Conferma Lettura").click()
        await logger.log(page, "conferma_lettura")

    except Exception:
        await logger.log(page, f"ERRORE_{step}")
        raise


async def _login_sister_direct(page: Page, logger: PageLogger, username: str, password: str) -> None:
    """Esegue il login diretto SISTER tramite il tab dedicato sulla pagina ADE.

    Credenziali richieste:
        SISTER_USERNAME — username dell'account SISTER nominale dell'utente
        SISTER_PASSWORD — password dell'account SISTER nominale dell'utente

    Scope d'uso ammesso: questo flusso è destinato all'**intestatario della
    convenzione SISTER** che desidera automatizzare le proprie consultazioni
    con le proprie credenziali nominali. Non è destinato a chi vuole rivendere
    o esporre l'accesso al portale SISTER a terzi (la convenzione SISTER
    richiede che l'utenza sia personale, non cedibile, e che le consultazioni
    siano riconducibili all'intestatario).

    Dopo il login si atterra direttamente nella pagina SceltaServizio di
    SISTER, saltando la navigazione tramite portale ADE (Cerca servizio →
    Vai al servizio → Conferma → Consultazioni → Visure catastali → Conferma
    Lettura) che non è necessaria con il login diretto.
    """
    step = "sister_tab"
    try:
        print("[LOGIN] Clicco tab 'Sister'...")
        await page.get_by_role("tab", name="Sister").click()
        await logger.log(page, "sister_tab")

        step = "username"
        print("[LOGIN] Inserisco username SISTER...")
        await page.get_by_role("textbox", name="Utente:").fill(username)
        await logger.log(page, "username")

        step = "password"
        print("[LOGIN] Inserisco password SISTER...")
        await page.get_by_role("textbox", name="Password:").fill(password)

        step = "accedi"
        print("[LOGIN] Clicco 'Accedi'...")
        await page.get_by_role("button", name="Accedi").click()
        await logger.log(page, "accedi")

        step = "attesa_sister"
        print("[LOGIN] Attendo caricamento portale SISTER...")
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
        await logger.log(page, "portale_sister")

        # Gestione sessioni orfane: ogni CloseSessionsSis chiude UNA sessione
        # stale. Dopo molti riavvii possono accumularsi più sessioni, perciò
        # proviamo fino a MAX_CLOSE_ATTEMPTS volte prima di alzare l'eccezione.
        # NB: la struttura `for/else` precedente aveva un off-by-one: dopo
        # l'N-esimo close+retry il loop terminava senza ricontrollare se la
        # sessione era stata liberata, quindi sollevava errore anche quando in
        # realtà l'ultimo tentativo era riuscito. Ora il check è esplicito sia
        # in testa al loop che dopo l'ultimo close.
        MAX_CLOSE_ATTEMPTS = 10

        def _is_orphan(content: str, current_url: str) -> bool:
            return "Utente gia' in sessione" in content or "error_locked.jsp" in current_url

        content = await page.content()
        url = page.url
        attempts_done = 0
        while _is_orphan(content, url) and attempts_done < MAX_CLOSE_ATTEMPTS:
            attempts_done += 1
            print(
                f"[LOGIN] Sessione orfana rilevata (tentativo {attempts_done}/{MAX_CLOSE_ATTEMPTS}) — chiudo e riprovo..."
            )
            step = f"close_session_{attempts_done}"
            await page.goto(
                "https://sister3.agenziaentrate.gov.it/Servizi/CloseSessionsSis",
                timeout=30000,
            )
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await logger.log(page, f"close_session_{attempts_done}")

            step = f"sister_tab_retry_{attempts_done}"
            await page.goto(
                "https://iampe.agenziaentrate.gov.it/sam/UI/Login?realm=/agenziaentrate",
                timeout=30000,
            )
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await page.get_by_role("tab", name="Sister").click()
            await page.get_by_role("textbox", name="Utente:").fill(username)
            await page.get_by_role("textbox", name="Password:").fill(password)
            await page.get_by_role("button", name="Accedi").click()
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await logger.log(page, f"portale_sister_retry_{attempts_done}")

            content = await page.content()
            url = page.url

        if _is_orphan(content, url):
            print("[LOGIN][ERRORE] Troppe sessioni orfane, impossibile liberare la sessione.")
            raise Exception(
                f"Utente già in sessione su un'altra postazione (max {MAX_CLOSE_ATTEMPTS} tentativi raggiunto)"
            )

        print("[LOGIN] Login SISTER completato.")

    except Exception:
        await logger.log(page, f"ERRORE_sister_{step}")
        raise


# Mappa di alias catastali per province con nome ISTAT divergente dal nome
# usato da SISTER. Chiavi e valori sono già normalizzati con
# ``_normalize_for_match``. Estendere qui se emergono nuovi casi.
_CATASTAL_PROVINCE_ALIASES = {
    "verbano cusio ossola": "verbania",
    # ISTAT "Monza e della Brianza" (istituita 2009): SISTER non la espone come
    # ufficio provinciale catastale separato — i comuni della Brianza restano
    # catastalmente sotto "MILANO Territorio" (confermato bulk-test
    # 2026-05-15, p.es. Misinto, Lissone). Vedi anche _UNSUPPORTED_PROVINCES.
    "monza e della brianza": "milano",
    "forli cesena": "forli",
    # ISTAT usa "Reggio nell'Emilia", SISTER espone solo "REGGIO EMILIA"
    # (confermato sperimentalmente, vedi AUDIT_REPORT_2026-05-15.md)
    "reggio nell emilia": "reggio emilia",
    # Sud Sardegna è stata istituita nel 2016 ma SISTER (catasto) la mappa
    # ancora sotto CAGLIARI Territorio (es. Carloforte, Buggerru, Iglesias,
    # Senorbì). Scoperto nel bulk-test 89 particelle del 2026-05-15.
    "sud sardegna": "cagliari",
    # ISTAT usa "Pesaro e Urbino", SISTER espone solo "PESARO Territorio".
    "pesaro e urbino": "pesaro",
    # ISTAT "Valle d'Aosta/Vallée d'Aoste" (forma bilingue): SISTER ha
    # solo "AOSTA Territorio". Scoperto nel bulk-test 97 particelle del
    # 2026-05-15 (Pont-Saint-Martin, Arvier, Champorcher).
    "valle d aosta vallee d aoste": "aosta",
    "valle d aosta": "aosta",
    "vallee d aoste": "aosta",
}


# Province che SISTER NON espone perché il catasto è gestito da un sistema
# regionale separato (Sistema Tavolare austriaco) o perché l'ufficio non è
# stato mai disaggregato. Le richieste per queste province falliscono in modo
# esplicito con un messaggio attionable, invece di esaurire il timeout sul
# matching della dropdown.
#
# Chiavi normalizzate con ``_normalize_for_match``. Estendere se emergono
# nuovi casi confermati sperimentalmente.
_UNSUPPORTED_PROVINCES_SISTER = {
    # Provincia Autonoma di Trento — Catasto Tavolare (Libro Fondiario)
    "trento": "Provincia Autonoma di Trento: catasto gestito dal Sistema "
    "Tavolare (Libro Fondiario), non disponibile su SISTER.",
    # Provincia Autonoma di Bolzano / Südtirol — Catasto Tavolare
    "bolzano": "Provincia Autonoma di Bolzano: catasto gestito dal Sistema "
    "Tavolare (Libro Fondiario), non disponibile su SISTER.",
    "bolzano bozen": "Provincia Autonoma di Bolzano: catasto gestito dal "
    "Sistema Tavolare (Libro Fondiario), non disponibile "
    "su SISTER.",
}


def _normalize_for_match(value: str) -> str:
    """Normalizza una stringa per matching tollerante in ``find_best_option_match``.

    Operazioni applicate (in ordine):
      1. ``NFKD`` + drop dei combining marks (rimuove accenti à→a, ù→u, ...);
      2. apostrofi tipografici ``’`` e backtick mappati ad ASCII;
      3. apostrofi/punteggiatura/separatori (``-``, ``_``, ``/``, ``'``, ...)
         convertiti in spazio;
      4. lowercase;
      5. rimozione del suffisso `` territorio`` (SISTER lo aggiunge alle
         province, es. ``ALESSANDRIA Territorio``);
      6. collasso whitespace multipli e strip.
    """
    if not value:
        return ""
    nfkd = unicodedata.normalize("NFKD", value)
    no_marks = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    no_marks = no_marks.replace("’", "'").replace("`", "'")
    cleaned = re.sub(r"[-_/'.,]+", " ", no_marks)
    lowered = cleaned.lower()
    lowered = re.sub(r"\bterritorio\b", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


# =============================================================================
# Dropdown options: estrazione fast (single page.evaluate) + cache process-wide
# =============================================================================
#
# Motivazione perf:
#   Il pattern originale ``page.locator(sel).all()`` + N×(``get_attribute`` +
#   ``inner_text``) costa 2N round-trip CDP. Con ~110 province o ~300 comuni
#   sono 600-1800ms di IPC puro per ogni richiesta. ``page.evaluate`` esegue
#   un singolo round-trip e ritorna l'array completo in JSON (≪100ms).
#
# Cache:
#   - ``_DROPDOWN_CACHE[("province",)]`` → {"items": [(value, text)], "by_norm": {…}}
#     Province: stabili nel tempo (cambiano <1/anno). Una volta caricate, riusate
#     per tutta la vita del processo.
#   - ``_DROPDOWN_CACHE[("comune", provincia_value)]`` →
#     {"items": [(value, text)], "by_belfiore": {CB: value}, "by_norm": {norm: value}}
#     Comuni per provincia: anch'essi stabili (catastalmente). Cache per-provincia.
#
# Diagnostica:
#   - ``[OPTS]`` log ogni estrazione live con count + elapsed.
#   - ``[CACHE hit|miss|STALE|disabled]`` ogni accesso alla cache.
#   - Kill switch: ``DROPDOWN_CACHE=0`` disabilita interamente la cache (fallback
#     a estrazione live ogni volta, ma comunque con ``_collect_options_fast``).
#   - In caso di failure su ``select_option`` chiamare ``invalidate_dropdown_cache``
#     per forzare un refetch alla richiesta successiva.

_DROPDOWN_CACHE: dict = {}


async def _wait_for_ready(page, selector: str, timeout_ms: int = 15000, label: str = "") -> None:
    """Attende che ``selector`` sia presente (``attached``) nel DOM.

    Sostituisce il pattern ``wait_for_load_state("domcontentloaded")`` quando
    il vero "ready" del passo è la presenza di un elemento target specifico
    (es. la prossima ``<select>`` del form, il link "Immobile"). Ritorna
    appena il selector è ATTACHED nel DOM (non richiede visibility) — questo
    è sufficiente per gli step successivi (``select_option``, ``click``,
    ``page.evaluate`` per estrarre option) che fanno auto-wait di visibility
    se serve, e copre i casi in cui SISTER nasconde temporaneamente l'elemento
    durante transitions server-side. Lo stato ``visible`` è troppo stretto e
    causa timeout su intermittenze di rendering (regressione live osservata
    2026-05-15: 6 timeout su 97 req).

    Timeout default 15s (vs 30s di ``wait_for_load_state``) per fail-fast su
    pagine che non caricano. Diagnostica: stampa
    ``[WAIT] label='...' selector='...' elapsed=Yms`` per grep delle latenze.
    In caso di timeout l'eccezione Playwright fa propagare il contesto al
    chiamante.
    """
    t0 = time.time()
    try:
        await page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
    finally:
        elapsed_ms = (time.time() - t0) * 1000
        print(f"[WAIT] label='{label}' selector='{selector}' elapsed={elapsed_ms:.0f}ms")


def _dropdown_cache_enabled() -> bool:
    return os.getenv("DROPDOWN_CACHE", "1") == "1"


def invalidate_dropdown_cache(reason: str = "") -> None:
    """Svuota la cache delle dropdown SISTER.

    Chiamare quando un ``select_option`` fallisce con un valore preso dalla
    cache (cache stale), o su restart browser/recovery sessione. Stampa il
    motivo per diagnostica (grep ``[CACHE] invalidate``).
    """
    n = len(_DROPDOWN_CACHE)
    _DROPDOWN_CACHE.clear()
    print(f"[CACHE] invalidate cleared={n} reason={reason or 'unspecified'}")


async def _collect_options_fast(page, selector: str) -> list:
    """Estrae tutte le ``<option>`` di ``selector`` in un solo round-trip CDP.

    Ritorna lista di tuple ``(value, text)``. Sostituisce il pattern
    ``locator(...).all()`` + N×``get_attribute``/``inner_text`` (2N round-trip).
    Lascia trasparire le eccezioni Playwright al chiamante per non mascherare
    bug strutturali (selector errato, pagina chiusa, ecc.).
    """
    t0 = time.time()
    raw = await page.evaluate(
        """sel => Array.from(document.querySelectorAll(sel + ' option'))
                    .map(o => [o.value || '', (o.textContent || '').trim()])""",
        selector,
    )
    # raw è una lista di liste [value, text]; normalizza a tuple di str
    items = [(str(v or ""), str(t or "")) for v, t in raw]
    elapsed_ms = (time.time() - t0) * 1000
    print(f"[OPTS] selector='{selector}' count={len(items)} source=evaluate elapsed={elapsed_ms:.0f}ms")
    return items


def _build_comune_indexes(items: list) -> dict:
    """Costruisce indici lookup per i comuni: by codice belfiore e by nome normalizzato.

    ``items`` è la lista (value, text) ritornata da ``_collect_options_fast``.
    Le option SISTER hanno ``value`` nella forma ``CB#NOME#0#0`` (es. ``H501#ROMA#0#0``).
    """
    by_belfiore: dict = {}
    by_norm: dict = {}
    for value, text in items:
        if not value:
            continue
        # codice belfiore: prefisso prima del primo '#'
        prefix = value.split("#", 1)[0].upper().strip()
        if prefix and prefix not in by_belfiore:
            by_belfiore[prefix] = value
        if text:
            norm = _normalize_for_match(text)
            if norm and norm not in by_norm:
                by_norm[norm] = value
    return {"items": items, "by_belfiore": by_belfiore, "by_norm": by_norm}


def _build_province_indexes(items: list) -> dict:
    """Costruisce indici lookup per le province: solo by nome normalizzato."""
    by_norm: dict = {}
    for value, text in items:
        if not value or not text:
            continue
        norm = _normalize_for_match(text)
        if norm and norm not in by_norm:
            by_norm[norm] = value
    return {"items": items, "by_norm": by_norm}


def _format_options_for_debug(items: list) -> list:
    """Ritorna lista 'text (value)' per le print di diagnostica 'disponibili'.

    ``items`` è il valore ritornato da ``_collect_options_fast``.
    Filtra entry con value o text vuoto. Usato per costruire il messaggio di
    errore quando una option non viene trovata: l'output coincide con il
    formato originale (`f"{text} ({value})"`).
    """
    return [f"{t} ({v})" for v, t in items if v and t]


async def _get_province_options(page) -> dict:
    """Ritorna gli indici delle province SISTER, cache process-wide se abilitata."""
    selector = "select[name='listacom']"
    if _dropdown_cache_enabled():
        cached = _DROPDOWN_CACHE.get(("province",))
        if cached is not None:
            print(f"[CACHE] hit kind=province count={len(cached['items'])}")
            return cached
        print("[CACHE] miss kind=province fetching live")
    else:
        print("[CACHE] disabled kind=province (DROPDOWN_CACHE=0)")
    items = await _collect_options_fast(page, selector)
    idx = _build_province_indexes(items)
    if _dropdown_cache_enabled():
        _DROPDOWN_CACHE[("province",)] = idx
        print(f"[CACHE] store kind=province count={len(items)}")
    return idx


async def _get_comune_options(page, provincia_value: Optional[str]) -> dict:
    """Ritorna gli indici dei comuni per ``provincia_value``.

    Cache key = ``("comune", provincia_value)``. **IMPORTANTE**: la cache è
    abilitata SOLO se ``provincia_value`` è truthy (es. ``"MANTOVA Territorio-MN"``).
    Se è ``None`` o stringa vuota, NON cachiamo: i comuni cambiano per provincia
    e una chiave condivisa ``""`` causerebbe cross-province poisoning (regressione
    osservata 2026-05-15: Mantova 73 comuni → riusati per Savona/Pesaro/...).
    Il chiamante legacy che non conosce provincia_value paga il singolo evaluate
    per fetch (comunque rapido, ~3-5ms).
    """
    selector = "select[name='denomComune']"
    cache_ok = bool(provincia_value) and _dropdown_cache_enabled()
    if cache_ok:
        key = ("comune", provincia_value)
        cached = _DROPDOWN_CACHE.get(key)
        if cached is not None:
            print(f"[CACHE] hit kind=comune provincia='{provincia_value}' " f"count={len(cached['items'])}")
            return cached
        print(f"[CACHE] miss kind=comune provincia='{provincia_value}' fetching live")
    elif not provincia_value:
        # Path legacy: nessuna cache possibile (key collision risk)
        pass
    else:
        print("[CACHE] disabled kind=comune (DROPDOWN_CACHE=0)")
    items = await _collect_options_fast(page, selector)
    idx = _build_comune_indexes(items)
    if cache_ok:
        _DROPDOWN_CACHE[("comune", provincia_value)] = idx
        print(f"[CACHE] store kind=comune provincia='{provincia_value}' count={len(items)}")
    return idx


async def find_option_by_codice_belfiore(
    page,
    selector: str,
    codice_belfiore: str,
    provincia_value: Optional[str] = None,
) -> Optional[str]:
    """Trova un'option SISTER il cui ``value`` inizia con ``{codice_belfiore}#``.

    Le option di ``select[name='denomComune']`` in SISTER hanno valore nella
    forma ``CODICEBELFIORE#NOME#0#0`` (es. ``A737#BELFIORE#0#0``,
    ``H501#ROMA#0#0``). Quando il consumer conosce il codice belfiore catastale
    (es. da ``parcels.administrativeunit``) può evitare il match stringa sul
    nome del comune — più robusto perché il codice belfiore è una chiave
    catastale stabile, mentre i nomi soffrono di varianti ortografiche
    (apostrofi tipografici, accenti, suffissi).

    Ritorna il ``value`` dell'option oppure ``None`` se nessuna option
    nel ``select`` ha quel prefisso.

    Implementazione (perf): usa ``_collect_options_fast`` (singolo evaluate)
    con cache per-provincia se ``provincia_value`` è valorizzato (passare il
    value della provincia già selezionata permette di cachare i comuni della
    provincia corrente in modo sicuro, senza cross-province poisoning). Se
    il selector non è quello dei comuni SISTER fa fallback a un evaluate
    diretto senza cache.
    """
    if not codice_belfiore:
        return None
    cb = codice_belfiore.strip().upper()
    if not cb:
        return None
    prefix = f"{cb}#"
    # Path veloce con cache per il selector standard dei comuni
    if selector == "select[name='denomComune']":
        idx = await _get_comune_options(page, provincia_value=provincia_value)
        value = idx["by_belfiore"].get(cb)
        if value:
            print(f"[MATCH_BELFIORE] hit cache cb='{cb}' -> '{value}'")
            return value
        # No-hit: log e ritorna None (caller fa fallback su match by name)
        print(f"[MATCH_BELFIORE] miss cb='{cb}' tra {len(idx['items'])} option " f"(comuni provincia corrente)")
        return None
    # Path generico (no cache): un solo evaluate + scan
    items = await _collect_options_fast(page, selector)
    print(f"[MATCH_BELFIORE] Cerco codice belfiore '{cb}' tra {len(items)} option (no-cache)")
    for value, text in items:
        if value and value.upper().startswith(prefix):
            print(f"[MATCH_BELFIORE] Match: '{text}' -> '{value}'")
            return value
    print(f"[MATCH_BELFIORE] Nessuna option con prefisso '{prefix}'")
    return None


async def find_best_option_match(page, selector, search_text, provincia_value: Optional[str] = None):
    """Trova l'opzione che meglio corrisponde al testo cercato.

    Il matching è tollerante a:
      - case (uppercase/lowercase),
      - accenti (à/è/ì/ò/ù → a/e/i/o/u),
      - apostrofi tipografici vs ASCII (’ vs '),
      - separatori (-, _, /) trattati come spazi,
      - suffissi specifici di SISTER come " Territorio" sulle province,
      - alias catastali per province con nome divergente da ISTAT
        (es. "Verbano-Cusio-Ossola" → "Verbania").

    L'ordine di priorità del matching è: exact value → exact text → starts-with
    → contains → match dell'alias catastale. Restituisce il ``value``
    dell'option scelta, oppure ``None`` se nessuna opzione è plausibile.

    ``provincia_value`` (opzionale) è usato solo quando ``selector`` è
    ``select[name='denomComune']``: passarlo abilita la cache comune
    scope-per-provincia in modo sicuro (evita cross-province poisoning).
    """
    # Estrazione fast (single evaluate) + cache process-wide per i selector standard
    if selector == "select[name='listacom']":
        idx = await _get_province_options(page)
        items = idx["items"]
        # Tentativo lookup O(1) sulla cache by normalized name
        search_norm_pre = _normalize_for_match(search_text)
        if search_norm_pre and search_norm_pre in idx["by_norm"]:
            v = idx["by_norm"][search_norm_pre]
            print(f"[MATCH] CACHE hit provincia '{search_text}' -> '{v}'")
            return v
    elif selector == "select[name='denomComune']":
        idx = await _get_comune_options(page, provincia_value=provincia_value)
        items = idx["items"]
        search_norm_pre = _normalize_for_match(search_text)
        if search_norm_pre and search_norm_pre in idx["by_norm"]:
            v = idx["by_norm"][search_norm_pre]
            print(f"[MATCH] CACHE hit comune '{search_text}' -> '{v}'")
            return v
    else:
        # Selector non standard (es. select[name='sezione']) — solo evaluate, no cache
        items = await _collect_options_fast(page, selector)

    best_match = None
    best_score = 0

    print(f"[MATCH] Cerco '{search_text}' tra {len(items)} opzioni (post-fast)")

    search_norm = _normalize_for_match(search_text)
    search_alias = _CATASTAL_PROVINCE_ALIASES.get(search_norm)

    for value, text in items:
        if not value or not text:
            continue

        # Calcola similarity score
        search_upper = search_text.upper()
        text_upper = text.upper()
        value_upper = value.upper()
        text_norm = _normalize_for_match(text)
        value_norm = _normalize_for_match(value)

        # PRIORITÀ 1: Exact match del valore (per sezioni come P, Q, etc.)
        if search_upper == value_upper or search_norm == value_norm:
            print(f"[MATCH] Exact value match trovato: '{text}' -> '{value}'")
            return value

        # PRIORITÀ 2: Exact match del testo (incluso match normalizzato senza
        # accenti/apostrofi/suffisso " Territorio")
        if search_upper == text_upper or search_norm == text_norm:
            print(f"[MATCH] Exact text match trovato: '{text}' -> '{value}'")
            return value

        # PRIORITÀ 3: Match che inizia con il testo cercato
        if text_upper.startswith(search_upper) or text_norm.startswith(search_norm):
            score = len(search_norm) / max(len(text_norm), 1)
            if score > best_score:
                best_score = score
                best_match = value
                print(f"[MATCH] Candidato (starts with): '{text}' -> '{value}' (score: {score:.2f})")
            continue

        # PRIORITÀ 4: Value che inizia con il testo cercato
        if value_upper.startswith(search_upper) or value_norm.startswith(search_norm):
            score = len(search_norm) / max(len(value_norm), 1) * 0.9
            if score > best_score:
                best_score = score
                best_match = value
                print(f"[MATCH] Candidato (value starts with): '{text}' -> '{value}' (score: {score:.2f})")
            continue

        # PRIORITÀ 5: Match che contiene il testo cercato
        if search_upper in text_upper or (search_norm and search_norm in text_norm):
            score = len(search_norm) / max(len(text_norm), 1) * 0.6
            if score > best_score:
                best_score = score
                best_match = value
                print(f"[MATCH] Candidato (contains): '{text}' -> '{value}' (score: {score:.2f})")
            continue

        # PRIORITÀ 6: Alias catastale (es. Verbano-Cusio-Ossola → Verbania)
        if search_alias and (
            text_norm == search_alias or text_norm.startswith(search_alias) or search_alias in text_norm
        ):
            score = len(search_alias) / max(len(text_norm), 1) * 0.5
            if score > best_score:
                best_score = score
                best_match = value
                print(f"[MATCH] Candidato (alias '{search_alias}'): '{text}' -> '{value}' (score: {score:.2f})")

    if best_match:
        print(f"[MATCH] Migliore match trovato: '{best_match}' (score: {best_score:.2f})")
        return best_match
    else:
        print(f"[MATCH] Nessun match trovato per '{search_text}'")
        return None


async def run_visura(
    page,
    provincia="Trieste",
    comune="Trieste",
    sezione=None,
    foglio="9",
    particella="166",
    tipo_catasto="T",
    extract_intestati=True,
    subalterno=None,
    codice_belfiore: Optional[str] = None,
):
    time0 = time.time()
    logger = PageLogger("visura")
    sezione_info = f", sezione={sezione}" if sezione else ", sezione=None"
    subalterno_info = f", subalterno={subalterno}" if subalterno else ""
    print(
        f"[VISURA] Inizio visura: provincia={provincia}, comune={comune}{sezione_info}, foglio={foglio}, particella={particella}{subalterno_info}, tipo_catasto={tipo_catasto}"
    )

    # Fail-fast su province non supportate (Trento/Bolzano: Catasto Tavolare).
    # Evita di navigare SISTER inutilmente e restituisce un errore actionable.
    provincia_norm = _normalize_for_match(provincia)
    unsupported_reason = _UNSUPPORTED_PROVINCES_SISTER.get(provincia_norm)
    if unsupported_reason:
        raise Exception(unsupported_reason)

    # Non creare una nuova pagina, usa quella esistente
    print("[VISURA] Utilizzando pagina di autenticazione esistente")

    # STEP 1: Selezione Ufficio Provinciale
    print("[VISURA] Navigando alla pagina di scelta servizio...")
    await page.goto("https://sister3.agenziaentrate.gov.it/Visure/SceltaServizio.do?tipo=/T/TM/VCVC_", timeout=60000)
    await _wait_for_ready(page, "select[name='listacom']", label="visura_scelta_servizio")
    print("[VISURA] Pagina caricata")
    await logger.log(page, "scelta_servizio")

    # Verifica che siamo realmente nella pagina di scelta servizio
    current_url = page.url
    if "SceltaServizio.do" not in current_url:
        raise Exception(
            f"La sessione sembra essere scaduta o si è verificato un errore durante il caricamento della pagina - URL: {current_url}"
        )

    # Verifica che le province siano disponibili
    provincia_options_count = await page.locator("select[name='listacom'] option").count()
    if provincia_options_count <= 1:
        raise Exception(
            "La sessione sembra essere scaduta o si è verificato un errore durante il caricamento della pagina"
        )

    # Verifica che la pagina sia stata caricata correttamente
    content = await page.content()
    if "error" in content.lower() or "sessione scaduta" in content.lower() or "login" in content.lower():
        raise Exception(
            "La sessione sembra essere scaduta o si è verificato un errore durante il caricamento della pagina"
        )

    # Trova e seleziona la provincia corretta
    print(f"[VISURA] Cercando provincia: {provincia}")

    # Prima estrai tutte le province disponibili per debug (single evaluate via helper)
    _prov_items = await _collect_options_fast(page, "select[name='listacom']")
    available_provinces = _format_options_for_debug(_prov_items)

    # Se non ci sono province disponibili, probabilmente la sessione è scaduta
    if len(available_provinces) == 0:
        raise Exception("Nessuna provincia disponibile - la sessione potrebbe essere scaduta")

    print(
        f"[VISURA] Province disponibili: {', '.join(available_provinces[:10])}{'...' if len(available_provinces) > 10 else ''}"
    )

    provincia_value = await find_best_option_match(page, "select[name='listacom']", provincia)

    if not provincia_value:
        raise Exception(
            f"Provincia '{provincia}' non trovata nelle opzioni disponibili. Prime 10 province disponibili: {', '.join(available_provinces[:10])}"
        )

    print(f"[VISURA] Selezionando provincia: {provincia_value}")
    try:
        await page.locator("select[name='listacom']").select_option(provincia_value)
        print("[VISURA] Provincia selezionata")
    except Exception as e:
        raise Exception(f"Errore nella selezione della provincia '{provincia_value}': {e}")

    print("[VISURA] Cliccando Applica...")
    await page.locator("input[type='submit'][value='Applica']").click()
    await _wait_for_ready(
        page,
        "a:has-text('Immobile'), [role='link']:has-text('Immobile')",
        label="visura_post_applica",
    )
    print("[VISURA] Applica cliccato, pagina caricata")
    await logger.log(page, "provincia_applicata")

    # STEP 2: Ricerca per immobili
    print("[VISURA] Cliccando link Immobile...")
    await page.get_by_role("link", name="Immobile").click()
    await _wait_for_ready(page, "select[name='denomComune']", label="visura_post_immobile")
    print("[VISURA] Link Immobile cliccato")
    await logger.log(page, "immobile")

    # STEP 2.1: Seleziona tipo catasto (T=Terreni, F=Fabbricati)
    print(f"[VISURA] Selezionando tipo catasto: {tipo_catasto} ({'Terreni' if tipo_catasto == 'T' else 'Fabbricati'})")
    try:
        await page.locator("select[name='tipoCatasto']").select_option(tipo_catasto)
        print(f"[VISURA] Tipo catasto selezionato: {tipo_catasto}")
    except Exception as e:
        print(f"[VISURA] Errore nella selezione tipo catasto: {e}")
        # Continua comunque, potrebbe essere già selezionato per default

    # Trova e seleziona il comune corretto
    print(f"[VISURA] Cercando comune: {comune}")

    # Prima estrai tutti i comuni disponibili per debug (single evaluate via helper)
    _comune_items = await _collect_options_fast(page, "select[name='denomComune']")
    available_comuni = _format_options_for_debug(_comune_items)

    print(
        f"[VISURA] Comuni disponibili: {', '.join(available_comuni[:10])}{'...' if len(available_comuni) > 10 else ''}"
    )

    # Quando il chiamante fornisce il codice belfiore (es. da `parcels.administrativeunit`)
    # bypassiamo il match-by-name e selezioniamo l'option per prefisso di `value`
    # (`CODICEBELFIORE#…`), che è più robusto per i comuni con nomi ambigui o
    # varianti ortografiche divergenti tra ISTAT e SISTER.
    comune_value = None
    if codice_belfiore:
        comune_value = await find_option_by_codice_belfiore(
            page,
            "select[name='denomComune']",
            codice_belfiore,
            provincia_value=provincia_value,
        )
        if not comune_value:
            print(
                f"[VISURA] codice_belfiore='{codice_belfiore}' non trovato nelle option, "
                f"fallback al match per nome '{comune}'"
            )

    if not comune_value:
        comune_value = await find_best_option_match(
            page,
            "select[name='denomComune']",
            comune,
            provincia_value=provincia_value,
        )

    if not comune_value:
        raise Exception(
            f"Comune '{comune}' non trovato nelle opzioni disponibili per la provincia '{provincia}'. Prime 10 comuni disponibili: {', '.join(available_comuni[:10])}"
        )

    print(f"[VISURA] Selezionando comune: {comune_value}")
    try:
        await page.locator("select[name='denomComune']").select_option(comune_value)
        print("[VISURA] Comune selezionato")
    except Exception as e:
        raise Exception(f"Errore nella selezione del comune '{comune_value}': {e}")

    # IMPORTANTE: Selezionare sezione solo se specificata (non None e non "_")
    if sezione:
        print("[VISURA] Cliccando 'scegli la sezione' per attivare dropdown...")
        await page.locator("input[name='selSezione'][value='scegli la sezione']").click()
        await _wait_for_ready(page, "select[name='sezione']", label="visura_sezione_open")
        print("[VISURA] Button sezione cliccato, dropdown attivato")

        # Prima estrai tutte le opzioni disponibili per debug (single evaluate via helper)
        _sez_items = await _collect_options_fast(page, "select[name='sezione']")
        available_sections = _format_options_for_debug(_sez_items)

        print(f"[VISURA] Sezioni disponibili: {', '.join(available_sections)}")

        # Se non ci sono sezioni disponibili, salta la selezione della sezione
        if not available_sections:
            print(f"[VISURA] Nessuna sezione disponibile per il comune '{comune}', saltando selezione sezione")
        else:
            # Ora seleziona la sezione
            print(f"[VISURA] Cercando sezione: {sezione}")
            sezione_value = await find_best_option_match(page, "select[name='sezione']", sezione)

            if not sezione_value:
                # Se la sezione non è trovata ma ci sono sezioni disponibili, fallback: salta la sezione
                print(
                    f"[VISURA] Sezione '{sezione}' non trovata tra le opzioni disponibili. Sezioni disponibili: {', '.join(available_sections)}. Continuando senza selezionare sezione..."
                )
            else:
                print(f"[VISURA] Selezionando sezione: {sezione_value}")
                try:
                    await page.locator("select[name='sezione']").select_option(sezione_value)
                    print("[VISURA] Sezione selezionata")
                except Exception as e:
                    print(
                        f"[VISURA] Errore nella selezione della sezione '{sezione_value}': {e}. Continuando senza sezione..."
                    )
    else:
        print("[VISURA] Sezione non specificata, saltando selezione sezione")

    # Inserisci foglio
    print(f"[VISURA] Inserendo foglio: {foglio}")
    await page.locator("input[name='foglio']").click()
    await page.locator("input[name='foglio']").fill(str(foglio))
    print("[VISURA] Foglio inserito")

    # Inserisci particella
    print(f"[VISURA] Inserendo particella: {particella}")
    await page.locator("input[name='particella1']").click()
    await page.locator("input[name='particella1']").fill(str(particella))
    print("[VISURA] Particella inserita")

    # Inserisci subalterno (opzionale, restringe la ricerca per fabbricati)
    if subalterno:
        print(f"[VISURA] Inserendo subalterno: {subalterno}")
        await page.locator("input[name='subalterno1']").fill(str(subalterno))
        print("[VISURA] Subalterno inserito")

    # Clicca Ricerca
    print("[VISURA] Cliccando Ricerca...")
    await page.locator("input[name='scelta'][value='Ricerca']").click()
    await page.wait_for_load_state("domcontentloaded", timeout=30000)
    print("[VISURA] Ricerca cliccata")
    await logger.log(page, "ricerca")

    # STEP 3: Gestisci conferma assenza subalterno (se necessario)
    try:
        # Controlla se è presente la pagina di conferma assenza subalterno
        conferma_button = page.locator("input[name='confAssSub'][value='Conferma']")
        if await conferma_button.count() > 0:
            print("[VISURA] Rilevata richiesta conferma assenza subalterno...")
            await conferma_button.click()
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            print("[VISURA] Conferma assenza subalterno cliccata")
            await logger.log(page, "conferma_subalterno")
    except Exception as e:
        print(f"[VISURA] Errore o non necessaria conferma subalterno: {e}")

    await logger.log(page, "risultati")

    # STEP 3.1: Controlla se la ricerca ha restituito risultati
    page_text = await page.inner_text("body")
    if "NESSUNA CORRISPONDENZA TROVATA" in page_text:
        time1 = time.time()
        print(
            f"[VISURA] Nessuna corrispondenza trovata per foglio={foglio}, particella={particella} in {time1-time0:.2f}s"
        )
        return {
            "immobili": [],
            "results": [],
            "total_results": 0,
            "intestati": [],
            "error": "NESSUNA CORRISPONDENZA TROVATA",
        }

    # STEP 4: Estrazione tabella Elenco Immobili
    print("[VISURA] Estraendo tabella Elenco Immobili...")
    try:
        # Proviamo diversi selettori per trovare la tabella
        immobili = []
        selectors = [
            "table.listaIsp4",  # Selettore basato sulla classe dalla tua HTML
            "table[class*='lista']",  # Cerca tabelle con classe che contiene 'lista'
            "table:has(th:text('Foglio'))",  # Cerca tabella con header 'Foglio'
            "table",  # Fallback: qualsiasi tabella
        ]

        for selector in selectors:
            try:
                print(f"[DEBUG] Tentativo selettore: {selector}")
                immobili_table = page.locator(selector)
                count = await immobili_table.count()
                print(f"[DEBUG] Trovate {count} tabelle con selettore {selector}")

                if count > 0:
                    # Se ci sono più tabelle, proviamo a trovare quella giusta
                    for i in range(count):
                        try:
                            table_elem = immobili_table.nth(i)
                            immobili_html = await table_elem.inner_html(timeout=10000)

                            # Verifica se contiene le colonne che ci aspettiamo
                            if "Foglio" in immobili_html or "Particella" in immobili_html:
                                immobili = parse_table(immobili_html)
                                print(
                                    f"[VISURA] Tabella Immobili estratta: {len(immobili)} righe con selettore {selector} (tabella {i})"
                                )
                                break
                        except Exception as e:
                            print(f"[DEBUG] Errore con tabella {i}: {e}")
                            continue

                    if immobili:
                        break

            except Exception as e:
                print(f"[DEBUG] Errore con selettore {selector}: {e}")
                continue

        if not immobili:
            print("[VISURA] Tabella Elenco Immobili non trovata con nessun selettore")
            await logger.log(page, "immobili_non_trovati")
            immobili = []
    except Exception as e:
        print(f"[VISURA] Errore estrazione immobili: {e}")
        immobili = []

    # Se non servono intestati, la tabella immobili è tutto ciò che serve
    if not extract_intestati:
        time1 = time.time()
        print(f"[VISURA] Visura completata con successo in {time1-time0:.2f} secondi")
        print(f"[VISURA] {len(immobili)} immobili estratti dalla tabella")
        return {
            "immobili": immobili,
            "results": [],
            "total_results": len(immobili),
            "intestati": [],
        }

    # STEP 5: Estrai intestati (solo quando extract_intestati=True)
    # Usato da /visura/intestati per terreni — tipicamente 1-2 risultati
    print("[VISURA] Estraendo intestati...")
    intestati = []

    try:
        # Try multiple selectors for the Intestati button
        intestati_button_selectors = [
            "input[name='intestati'][value='Intestati']",
            "input[value='Intestati']",
            "input[name='intestati']",
            "button:has-text('Intestati')",
            "input[type='submit'][value*='ntestat']",
            "*[value='Intestati']",
        ]

        intestati_button = None
        for selector in intestati_button_selectors:
            try:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    intestati_button = locator.first
                    print(f"[VISURA] Bottone Intestati trovato con selettore: {selector}")
                    break
            except Exception as e:
                print(f"[VISURA] Selettore {selector} fallito: {e}")
                continue

        if intestati_button:
            await intestati_button.click()
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            print("[VISURA] Intestati cliccato")
            await logger.log(page, "intestati")

            # Estrai tabella Elenco Intestati
            intestati_selectors = [
                "table.listaIsp4",
                "table[class*='lista']",
                "table:has(th:text('Nominativo o denominazione'))",
                "table:has(th:text('Codice fiscale'))",
                "table:has(th:text('Titolarità'))",
                "table:has(th:text('Cognome'))",
                "table:has(th:text('Nome'))",
                "table",
            ]

            for selector in intestati_selectors:
                try:
                    intestati_table = page.locator(selector)
                    count = await intestati_table.count()

                    if count > 0:
                        for i in range(count):
                            try:
                                table_elem = intestati_table.nth(i)
                                intestati_html = await table_elem.inner_html(timeout=10000)

                                if (
                                    "Cognome" in intestati_html
                                    or "Nome" in intestati_html
                                    or "Soggetto" in intestati_html
                                    or "Nominativo o denominazione" in intestati_html
                                    or "Codice fiscale" in intestati_html
                                    or "Titolarità" in intestati_html
                                ):
                                    intestati = parse_table(intestati_html)
                                    print(f"[VISURA] Tabella Intestati estratta: {len(intestati)} righe")
                                    break
                                else:
                                    temp_intestati = parse_table(intestati_html)
                                    if temp_intestati and len(temp_intestati) > 0:
                                        if "Foglio" not in intestati_html and "Particella" not in intestati_html:
                                            intestati = temp_intestati
                                            print(
                                                f"[VISURA] Tabella Intestati estratta (fallback): {len(intestati)} righe"
                                            )
                                            break
                            except Exception as e:
                                print(f"[DEBUG] Errore con tabella intestati {i}: {e}")
                                continue

                        if intestati:
                            break

                except Exception as e:
                    print(f"[DEBUG] Errore con selettore intestati {selector}: {e}")
                    continue
        else:
            print("[VISURA] Bottone Intestati non trovato")

    except Exception as e:
        print(f"[VISURA] Errore estrazione intestati: {e}")

    time1 = time.time()
    print(f"[VISURA] Visura completata con successo in {time1-time0:.2f} secondi")

    result = {
        "immobili": immobili,
        "results": [{"result_index": 1, "immobile": immobili[0] if immobili else {}, "intestati": intestati}],
        "total_results": len(immobili),
        "intestati": intestati,
    }

    return result


async def logout(page: Page):
    """Effettua il logout dal portale SISTER"""
    logger = PageLogger("logout")
    try:
        await logger.log(page, "before_logout")
        print("[LOGOUT] Cercando il bottone 'Esci'...")

        # Proviamo diversi selettori per il bottone di logout
        logout_selectors = [
            "input[value='Esci']",  # Input con value Esci
            "button:has-text('Esci')",  # Button che contiene il testo Esci
            "a:has-text('Esci')",  # Link che contiene il testo Esci
            "input[type='submit'][value*='Esci']",  # Input submit che contiene Esci
            "*[onclick*='logout']",  # Qualsiasi elemento con onclick che contiene logout
            "*[onclick*='Esci']",  # Qualsiasi elemento con onclick che contiene Esci
        ]

        logout_success = False

        for selector in logout_selectors:
            try:
                print(f"[LOGOUT] Tentativo selettore: {selector}")
                logout_button = page.locator(selector)
                count = await logout_button.count()
                print(f"[LOGOUT] Trovati {count} elementi con selettore {selector}")

                if count > 0:
                    await logout_button.first.click()
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass  # Logout page may keep making requests; navigation already happened
                    print(f"[LOGOUT] Logout effettuato con successo usando selettore: {selector}")
                    logout_success = True
                    break

            except Exception as e:
                print(f"[LOGOUT] Errore con selettore {selector}: {e}")
                continue

        if not logout_success:
            print("[LOGOUT] ATTENZIONE: Non è stato possibile trovare il bottone 'Esci'")
            await logger.log(page, "logout_bottone_non_trovato")
        else:
            await logger.log(page, "after_logout")
            print("[LOGOUT] Sessione chiusa correttamente")

    except Exception as e:
        print(f"[LOGOUT] Errore durante il logout: {e}")
        await logger.log(page, "logout_errore")


async def extract_all_sezioni(page: Page, tipo_catasto: str = "T", max_province: int = 200) -> list:
    """
    Estrae tutte le sezioni per tutte le province e comuni d'Italia.

    Args:
        page: Pagina Playwright autenticata
        tipo_catasto: 'T' per Terreni, 'F' per Fabbricati
        max_province: Numero massimo di province da processare

    Returns:
        Lista di dizionari con dati delle sezioni
    """
    sezioni_data = []
    logger = PageLogger("sezioni")

    try:
        print(f"[SEZIONI] Iniziando estrazione sezioni per tipo catasto: {tipo_catasto} (max {max_province} province)")

        # Naviga alla pagina di scelta servizio
        print("[SEZIONI] Navigando alla pagina di scelta servizio...")
        await page.goto(
            "https://sister3.agenziaentrate.gov.it/Visure/SceltaServizio.do?tipo=/T/TM/VCVC_", timeout=60000
        )
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
        print("[SEZIONI] Pagina caricata")
        await logger.log(page, "scelta_servizio")

        # Estrai tutte le province
        print("[SEZIONI] Estraendo lista province...")
        _prov_items_iv = await _collect_options_fast(page, "select[name='listacom']")
        province_list = []

        for value, text in _prov_items_iv:
            if value and text and value.strip() and text.strip():
                # Salta "NAZIONALE" che sembra problematico
                if "NAZIONALE" not in text.upper():
                    province_list.append({"value": value.strip(), "text": text.strip()})

        # Limita il numero di province per evitare timeout
        province_list = province_list[:max_province]

        print(f"[SEZIONI] Processando {len(province_list)} province")

        for i, provincia in enumerate(province_list):
            print(f"[SEZIONI] Processando provincia {i+1}/{len(province_list)}: {provincia['text']}")

            try:
                # Seleziona la provincia (stesso modo di run_visura)
                print(f"[SEZIONI] Selezionando provincia: {provincia['value']}")
                await page.locator("select[name='listacom']").select_option(provincia["value"])
                print("[SEZIONI] Provincia selezionata")

                print("[SEZIONI] Cliccando Applica...")
                await page.locator("input[type='submit'][value='Applica']").click()
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                print("[SEZIONI] Applica cliccato, pagina caricata")

                # Vai alla ricerca immobili (stesso modo di run_visura)
                print("[SEZIONI] Cliccando link Immobile...")
                await page.get_by_role("link", name="Immobile").click()
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                print("[SEZIONI] Link Immobile cliccato")

                # Seleziona tipo catasto (stesso modo di run_visura)
                print(f"[SEZIONI] Selezionando tipo catasto: {tipo_catasto}")
                try:
                    await page.locator("select[name='tipoCatasto']").select_option(tipo_catasto)
                    print(f"[SEZIONI] Tipo catasto selezionato: {tipo_catasto}")
                except Exception as e:
                    print(f"[SEZIONI] Errore selezione tipo catasto per {provincia['text']}: {e}")

                # Estrai tutti i comuni per questa provincia
                print("[SEZIONI] Estraendo lista comuni...")
                _comune_items_iv = await _collect_options_fast(page, "select[name='denomComune']")
                comuni_list = []

                for value, text in _comune_items_iv:
                    if value and text and value.strip() and text.strip():
                        comuni_list.append({"value": value.strip(), "text": text.strip()})

                print(f"[SEZIONI] Trovati {len(comuni_list)} comuni per {provincia['text']}")

                for j, comune in enumerate(comuni_list):
                    print(
                        f"[SEZIONI] Processando comune {j+1}/{len(comuni_list)} per {provincia['text']}: {comune['text']}"
                    )

                    try:
                        # Seleziona il comune (stesso modo di run_visura)
                        print(f"[SEZIONI] Selezionando comune: {comune['value']}")
                        await page.locator("select[name='denomComune']").select_option(comune["value"])
                        print("[SEZIONI] Comune selezionato")

                        # Attiva selezione sezione (ESATTO come in run_visura)
                        print("[SEZIONI] Cliccando 'scegli la sezione' per attivare dropdown...")
                        await page.locator("input[name='selSezione'][value='scegli la sezione']").click()
                        await page.wait_for_load_state("domcontentloaded", timeout=30000)
                        print("[SEZIONI] Button sezione cliccato, dropdown attivato")

                        # Estrai le sezioni per questo comune (stesso modo di run_visura)
                        print(f"[SEZIONI] Estraendo sezioni per comune {comune['text']}...")
                        comune_sezioni_data = []

                        try:
                            # Prima verifica se ci sono sezioni disponibili
                            _sez_items_iv = await _collect_options_fast(page, "select[name='sezione']")
                            available_sections = []

                            for value, text in _sez_items_iv:
                                if value and text and value.strip() and text.strip():
                                    available_sections.append({"value": value.strip(), "text": text.strip()})

                            print(f"[SEZIONI] Trovate {len(available_sections)} sezioni per {comune['text']}")

                            # Aggiungi tutte le sezioni trovate
                            for sezione in available_sections:
                                comune_sezioni_data.append(
                                    {
                                        "provincia_nome": provincia["text"],
                                        "provincia_value": provincia["value"],
                                        "comune_nome": comune["text"],
                                        "comune_value": comune["value"],
                                        "sezione_nome": sezione["text"],
                                        "sezione_value": sezione["value"],
                                        "tipo_catasto": tipo_catasto,
                                    }
                                )

                            # Se non ci sono sezioni, aggiungi comunque il comune senza sezione
                            if len(available_sections) == 0:
                                print(
                                    f"[SEZIONI] Nessuna sezione trovata per {comune['text']}, aggiungendo comune senza sezione"
                                )
                                comune_sezioni_data.append(
                                    {
                                        "provincia_nome": provincia["text"],
                                        "provincia_value": provincia["value"],
                                        "comune_nome": comune["text"],
                                        "comune_value": comune["value"],
                                        "sezione_nome": None,
                                        "sezione_value": None,
                                        "tipo_catasto": tipo_catasto,
                                    }
                                )

                        except Exception as e:
                            print(f"[SEZIONI] Errore estrazione sezioni per {comune['text']}: {e}")
                            # Aggiungi record senza sezione in caso di errore
                            comune_sezioni_data.append(
                                {
                                    "provincia_nome": provincia["text"],
                                    "provincia_value": provincia["value"],
                                    "comune_nome": comune["text"],
                                    "comune_value": comune["value"],
                                    "sezione_nome": None,
                                    "sezione_value": None,
                                    "tipo_catasto": tipo_catasto,
                                }
                            )

                        # Aggiungi le sezioni alla lista locale
                        if comune_sezioni_data:
                            sezioni_data.extend(comune_sezioni_data)
                            print(f"[SEZIONI] Aggiunte {len(comune_sezioni_data)} sezioni per {comune['text']}")

                    except Exception as e:
                        print(f"[SEZIONI] Errore processando comune {comune['text']}: {e}")
                        continue

                print(
                    f"[SEZIONI] Provincia {provincia['text']} completata. Sezioni totali trovate finora: {len(sezioni_data)}"
                )

                # Torna alla pagina principale per la prossima provincia
                print("[SEZIONI] Tornando alla pagina principale per prossima provincia...")
                await page.goto(
                    "https://sister3.agenziaentrate.gov.it/Visure/SceltaServizio.do?tipo=/T/TM/VCVC_", timeout=60000
                )
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                print("[SEZIONI] Tornato alla pagina principale")

            except Exception as e:
                print(f"[SEZIONI] Errore processando provincia {provincia['text']}: {e}")
                continue

        print(f"[SEZIONI] Estrazione completata. Trovate {len(sezioni_data)} sezioni totali")
        return sezioni_data

    except Exception as e:
        print(f"[SEZIONI] Errore durante estrazione sezioni: {e}")
        return sezioni_data


async def run_visura_immobile(
    page,
    provincia="Trieste",
    comune="Trieste",
    sezione=None,
    foglio="9",
    particella="166",
    subalterno=None,
    codice_belfiore: Optional[str] = None,
):
    """
    Esegue una visura catastale per un immobile specifico (solo per fabbricati con subalterno).
    Questa funzione è ottimizzata per ottenere solo gli intestati di un immobile specifico.

    Args:
        page: Pagina Playwright autenticata
        provincia: Nome della provincia
        comune: Nome del comune
        sezione: Sezione territoriale (opzionale)
        foglio: Numero foglio
        particella: Numero particella
        subalterno: Numero subalterno (obbligatorio per questa funzione)

    Returns:
        Dict con intestati dell'immobile specificato
    """
    time0 = time.time()
    logger = PageLogger("visura_immobile")
    sezione_info = f", sezione={sezione}" if sezione else ", sezione=None"
    print(
        f"[VISURA_IMMOBILE] Inizio visura immobile: provincia={provincia}, comune={comune}{sezione_info}, foglio={foglio}, particella={particella}, subalterno={subalterno}"
    )

    if not subalterno:
        raise ValueError("Il subalterno è obbligatorio per le visure per immobile specifico")

    # Fail-fast su province non supportate (Trento/Bolzano: Catasto Tavolare).
    provincia_norm = _normalize_for_match(provincia)
    unsupported_reason = _UNSUPPORTED_PROVINCES_SISTER.get(provincia_norm)
    if unsupported_reason:
        raise Exception(unsupported_reason)

    # STEP 1: Selezione Ufficio Provinciale
    print("[VISURA_IMMOBILE] Navigando alla pagina di scelta servizio...")
    await page.goto("https://sister3.agenziaentrate.gov.it/Visure/SceltaServizio.do?tipo=/T/TM/VCVC_", timeout=60000)
    await _wait_for_ready(page, "select[name='listacom']", label="immobile_scelta_servizio")
    print("[VISURA_IMMOBILE] Pagina caricata")
    await logger.log(page, "scelta_servizio")

    # Verifica che siamo realmente nella pagina di scelta servizio
    current_url = page.url
    if "SceltaServizio.do" not in current_url:
        raise Exception(f"La sessione sembra essere scaduta o si è verificato un errore - URL: {current_url}")

    # Trova e seleziona la provincia corretta
    print(f"[VISURA_IMMOBILE] Cercando provincia: {provincia}")
    provincia_value = await find_best_option_match(page, "select[name='listacom']", provincia)

    if not provincia_value:
        raise Exception(f"Provincia '{provincia}' non trovata nelle opzioni disponibili")

    print(f"[VISURA_IMMOBILE] Selezionando provincia: {provincia_value}")
    await page.locator("select[name='listacom']").select_option(provincia_value)
    print("[VISURA_IMMOBILE] Cliccando Applica...")
    await page.locator("input[type='submit'][value='Applica']").click()
    await _wait_for_ready(
        page,
        "a:has-text('Immobile'), [role='link']:has-text('Immobile')",
        label="immobile_post_applica",
    )
    await logger.log(page, "provincia_applicata")

    # STEP 2: Ricerca per immobili
    print("[VISURA_IMMOBILE] Cliccando link Immobile...")
    await page.get_by_role("link", name="Immobile").click()
    await _wait_for_ready(page, "select[name='denomComune']", label="immobile_post_immobile")
    await logger.log(page, "immobile")

    # STEP 2.1: Seleziona tipo catasto FABBRICATI (F)
    print("[VISURA_IMMOBILE] Selezionando tipo catasto: F (Fabbricati)")
    await page.locator("select[name='tipoCatasto']").select_option("F")

    # Trova e seleziona il comune
    print(f"[VISURA_IMMOBILE] Cercando comune: {comune}")
    comune_value = None
    if codice_belfiore:
        comune_value = await find_option_by_codice_belfiore(
            page,
            "select[name='denomComune']",
            codice_belfiore,
            provincia_value=provincia_value,
        )
        if not comune_value:
            print(
                f"[VISURA_IMMOBILE] codice_belfiore='{codice_belfiore}' non trovato, "
                f"fallback al match per nome '{comune}'"
            )
    if not comune_value:
        comune_value = await find_best_option_match(
            page,
            "select[name='denomComune']",
            comune,
            provincia_value=provincia_value,
        )

    if not comune_value:
        raise Exception(f"Comune '{comune}' non trovato nelle opzioni disponibili")

    print(f"[VISURA_IMMOBILE] Selezionando comune: {comune_value}")
    await page.locator("select[name='denomComune']").select_option(comune_value)

    # Seleziona sezione se specificata
    if sezione:
        print("[VISURA_IMMOBILE] Cliccando 'scegli la sezione' per attivare dropdown...")
        await page.locator("input[name='selSezione'][value='scegli la sezione']").click()
        await _wait_for_ready(page, "select[name='sezione']", label="immobile_sezione_open")

        # Controlla se ci sono sezioni disponibili (single evaluate via helper)
        _sez_items_im = await _collect_options_fast(page, "select[name='sezione']")
        available_sections = _format_options_for_debug(_sez_items_im)

        if not available_sections:
            print(f"[VISURA_IMMOBILE] Nessuna sezione disponibile per il comune '{comune}', saltando selezione sezione")
        else:
            print(f"[VISURA_IMMOBILE] Cercando sezione: {sezione}")
            sezione_value = await find_best_option_match(page, "select[name='sezione']", sezione)

            if not sezione_value:
                print(
                    f"[VISURA_IMMOBILE] Sezione '{sezione}' non trovata tra le opzioni disponibili. Sezioni disponibili: {', '.join(available_sections)}. Continuando senza selezionare sezione..."
                )
            else:
                print(f"[VISURA_IMMOBILE] Selezionando sezione: {sezione_value}")
                try:
                    await page.locator("select[name='sezione']").select_option(sezione_value)
                    print("[VISURA_IMMOBILE] Sezione selezionata")
                except Exception as e:
                    print(
                        f"[VISURA_IMMOBILE] Errore nella selezione della sezione '{sezione_value}': {e}. Continuando senza sezione..."
                    )

    # Inserisci dati immobile
    print(f"[VISURA_IMMOBILE] Inserendo foglio: {foglio}")
    await page.locator("input[name='foglio']").fill(str(foglio))

    print(f"[VISURA_IMMOBILE] Inserendo particella: {particella}")
    await page.locator("input[name='particella1']").fill(str(particella))

    print(f"[VISURA_IMMOBILE] Inserendo subalterno: {subalterno}")
    await page.locator("input[name='subalterno1']").fill(str(subalterno))

    # Clicca Ricerca
    print("[VISURA_IMMOBILE] Cliccando Ricerca...")
    await page.locator("input[name='scelta'][value='Ricerca']").click()
    await page.wait_for_load_state("domcontentloaded", timeout=30000)
    await logger.log(page, "ricerca")

    # STEP 3: Gestisci conferma assenza subalterno (se necessario)
    try:
        conferma_button = page.locator("input[name='confAssSub'][value='Conferma']")
        if await conferma_button.count() > 0:
            print("[VISURA_IMMOBILE] Rilevata richiesta conferma assenza subalterno...")
            await conferma_button.click()
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await logger.log(page, "conferma_subalterno")
    except Exception as e:
        print(f"[VISURA_IMMOBILE] Errore o non necessaria conferma subalterno: {e}")

    await logger.log(page, "risultati")

    # STEP 4: Estrazione dati immobile (opzionale, principalmente per verifica)
    print("[VISURA_IMMOBILE] Estraendo dati immobile...")
    immobile_data = {}
    try:
        immobili_table = page.locator("table.listaIsp4").first
        if await immobili_table.count() > 0:
            immobili_html = await immobili_table.inner_html()
            immobili = parse_table(immobili_html)
            immobile_data = immobili[0] if immobili else {}
            print(f"[VISURA_IMMOBILE] Dati immobile estratti: {immobile_data}")
    except Exception as e:
        print(f"[VISURA_IMMOBILE] Errore estrazione dati immobile: {e}")

    # STEP 5: Estrazione intestati
    print("[VISURA_IMMOBILE] Cliccando Intestati...")
    intestati = []
    try:
        # Try multiple selectors for the Intestati button
        intestati_button_selectors = [
            "input[name='intestati'][value='Intestati']",
            "input[value='Intestati']",
            "input[name='intestati']",
            "button:has-text('Intestati')",
            "input[type='submit'][value*='ntestat']",  # Case insensitive partial match
            "input[type='button'][value*='ntestat']",
            "*[value='Intestati']",
            "a:has-text('Intestati')",
        ]

        intestati_button = None
        for selector in intestati_button_selectors:
            try:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    intestati_button = locator.first
                    print(f"[VISURA_IMMOBILE] Bottone Intestati trovato con selettore: {selector}")
                    break
            except Exception as e:
                print(f"[VISURA_IMMOBILE] Selettore {selector} fallito: {e}")
                continue

        if intestati_button:
            await intestati_button.click()
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            print("[VISURA_IMMOBILE] Intestati cliccato")
            await logger.log(page, "intestati")

            # Estrai tabella Elenco Intestati
            print("[VISURA_IMMOBILE] Estraendo tabella Elenco Intestati...")
            selectors = [
                "table.listaIsp4",
                "table[class*='lista']",
                "table:has(th:text('Cognome'))",
                "table:has(th:text('Nome'))",
                "table:has(th:text('Nominativo o denominazione'))",
                "table:has(th:text('Codice fiscale'))",
                "table:has(th:text('Titolarità'))",
                "table",
            ]

            for selector in selectors:
                try:
                    intestati_table = page.locator(selector)
                    count = await intestati_table.count()

                    if count > 0:
                        for i in range(count):
                            try:
                                table_elem = intestati_table.nth(i)
                                intestati_html = await table_elem.inner_html(timeout=10000)

                                if (
                                    "Cognome" in intestati_html
                                    or "Nome" in intestati_html
                                    or "Soggetto" in intestati_html
                                    or "Nominativo o denominazione" in intestati_html
                                    or "Codice fiscale" in intestati_html
                                    or "Titolarità" in intestati_html
                                ):
                                    intestati = parse_table(intestati_html)
                                    print(f"[VISURA_IMMOBILE] Tabella Intestati estratta: {len(intestati)} righe")
                                    break
                                else:
                                    temp_intestati = parse_table(intestati_html)
                                    if temp_intestati and len(temp_intestati) > 0:
                                        if "Foglio" not in intestati_html and "Particella" not in intestati_html:
                                            intestati = temp_intestati
                                            print(
                                                f"[VISURA_IMMOBILE] Tabella Intestati estratta (fallback): {len(intestati)} righe"
                                            )
                                            break
                            except Exception as e:
                                print(f"[DEBUG] Errore con tabella intestati {i}: {e}")
                                continue

                        if intestati:
                            break

                except Exception as e:
                    print(f"[DEBUG] Errore con selettore intestati {selector}: {e}")
                    continue
        else:
            print("[VISURA_IMMOBILE] Bottone Intestati non trovato con nessun selettore")

            # Debug: stampa tutti gli input e button disponibili
            try:
                all_inputs = await page.locator("input").all()
                print(f"[DEBUG] Trovati {len(all_inputs)} elementi input:")
                for i, inp in enumerate(all_inputs):
                    try:
                        tag_name = await inp.evaluate("el => el.tagName")
                        input_type = await inp.get_attribute("type") or "text"
                        name = await inp.get_attribute("name") or ""
                        value = await inp.get_attribute("value") or ""
                        id_attr = await inp.get_attribute("id") or ""
                        class_attr = await inp.get_attribute("class") or ""
                        print(
                            f"[DEBUG]   {i}: {tag_name} type='{input_type}' name='{name}' value='{value}' id='{id_attr}' class='{class_attr}'"
                        )
                    except Exception as e:
                        print(f"[DEBUG]   {i}: Error getting attributes: {e}")

                all_buttons = await page.locator("button").all()
                print(f"[DEBUG] Trovati {len(all_buttons)} elementi button:")
                for i, btn in enumerate(all_buttons):
                    try:
                        text = await btn.inner_text()
                        name = await btn.get_attribute("name") or ""
                        value = await btn.get_attribute("value") or ""
                        id_attr = await btn.get_attribute("id") or ""
                        class_attr = await btn.get_attribute("class") or ""
                        print(
                            f"[DEBUG]   {i}: text='{text}' name='{name}' value='{value}' id='{id_attr}' class='{class_attr}'"
                        )
                    except Exception as e:
                        print(f"[DEBUG]   {i}: Error getting button attributes: {e}")

            except Exception as e:
                print(f"[DEBUG] Errore nel debug degli elementi: {e}")
    except Exception as e:
        print(f"[VISURA_IMMOBILE] Errore estrazione intestati: {e}")

    time1 = time.time()
    print(f"[VISURA_IMMOBILE] Visura immobile completata in {time1-time0:.2f} secondi")

    result = {"immobile": immobile_data, "intestati": intestati, "total_intestati": len(intestati)}

    return result
