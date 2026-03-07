import logging
from contextlib import suppress

logger = logging.getLogger(__name__)
from bs4 import BeautifulSoup
import time
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
import os
import re
from datetime import datetime

PAGES_LOG_DIR = "./logs/pages"


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
            rows.append(dict(zip(headers, cells)))
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

    @classmethod
    def reset_session(cls):
        """Resetta la sessione (da chiamare ad ogni avvio del server)."""
        cls._session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        cls._flow_counters = {}

    def __init__(self, flow_name: str):
        if PageLogger._session_id is None:
            PageLogger._session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        # Contatore per differenziare flussi ripetuti (visura_002, visura_003…)
        count = PageLogger._flow_counters.get(flow_name, 0) + 1
        PageLogger._flow_counters[flow_name] = count

        self.flow_name = flow_name
        self.step = 0

        dir_name = flow_name if count == 1 else f"{flow_name}_{count:03d}"
        self.base_dir = os.path.join(PAGES_LOG_DIR, PageLogger._session_id, dir_name)
        os.makedirs(self.base_dir, exist_ok=True)

    async def log(self, page: Page, step_name: str) -> None:
        """Salva l'HTML corrente della pagina su disco."""
        self.step += 1
        try:
            if not page or page.is_closed():
                logger.info(f"[PAGE_LOG] {self.flow_name}/{step_name}: pagina chiusa, skip")
                return
            with suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            url = page.url
            html = await page.content()
            safe_name = re.sub(r'[^\w\-]', '_', step_name)
            filename = f"{self.step:02d}_{safe_name}.html"
            filepath = os.path.join(self.base_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"<!-- URL: {url} -->\n")
                f.write(f"<!-- Step: {step_name} -->\n")
                f.write(f"<!-- Timestamp: {datetime.now().isoformat()} -->\n\n")
                f.write(html)
            logger.info(f"[PAGE_LOG] {self.flow_name}/{filename}")
        except Exception as e:
            if "navigating" in str(e).lower() or "target closed" in str(e).lower():
                logger.debug(f"[PAGE_LOG] {self.flow_name}/{step_name} saltato (navigazione in corso)")
            else:
                logger.error(f"[PAGE_LOG] Errore salvataggio {step_name}: {e}")


async def login(page: Page):
    ade_username = os.getenv("ADE_USERNAME")
    ade_password = os.getenv("ADE_PASSWORD")
    auth_provider_name = os.getenv("AUTH_PROVIDER", "SIELTE").upper()
    
    timeout_2fa = int(os.getenv("2FA_TIMEOUT_SECONDS", "90"))
    
    if not ade_username or not ade_password:
        raise ValueError("ADE_USERNAME and ADE_PASSWORD environment variables must be set")
        
    logger.info(f"[LOGIN] Avvio autenticazione tramite provider: {auth_provider_name}")

    from auth.factory import get_provider

    page_logger = PageLogger("login")

    try:
        provider = get_provider(auth_provider_name)
        await provider.login(page, ade_username, ade_password, page_logger=page_logger)

        logger.info("[LOGIN] Cerco servizio SISTER...")
        ricerca_box = page.get_by_role("textbox", name="Cerca il servizio")
        try:
            await ricerca_box.wait_for(state="visible", timeout=timeout_2fa * 1000)
        except Exception as e:
            # Check immediato per sessione bloccata o errore redirect
            content = await page.content()
            if "Utente gia' in sessione" in content or "error_locked.jsp" in page.url:
                raise Exception("Utente già in sessione su un'altra postazione (Rilevato all'ingresso)")
            logger.debug(f"[LOGIN][DEBUG] Timeout ricerca box. URL attuale: {page.url}")
            raise e

        await page_logger.log(page, "before_sister_search")
        await ricerca_box.click()
        await ricerca_box.fill("SISTER")
        await ricerca_box.press("Enter")
        await page_logger.log(page, "after_sister_search")

        logger.info("[LOGIN] Clicco 'Vai al servizio'...")
        await page.get_by_role("link", name="Vai al servizio").first.click()
        await page.wait_for_load_state("networkidle")
        await page_logger.log(page, "sister_entry")

        # Check errori comuni SISTER
        content = await page.content()
        url = page.url
        if "Utente gia' in sessione" in content or "error_locked.jsp" in url:
            await page_logger.log(page, "error_session_locked")
            raise Exception("Utente già in sessione su un'altra postazione")
        if "Utente non abilitato" in content:
            await page_logger.log(page, "error_not_enabled")
            raise Exception(f"L'utente {auth_provider_name} non è abilitato all'accesso a SISTER.")

        logger.info("[LOGIN] Clicco 'Conferma' se presente...")
        conferma_btn = page.get_by_role("button", name="Conferma")
        if await conferma_btn.count() > 0 and await conferma_btn.is_visible():
            await conferma_btn.click()

        logger.info("[LOGIN] Navigo verso le visure catastali...")
        await page.get_by_role("link", name="Consultazioni e Certificazioni").click()
        await page_logger.log(page, "consultazioni_certificazioni")
        await page.get_by_role("link", name="Visure catastali").click()
        await page_logger.log(page, "visure_catastali")

        conferma_lettura = page.get_by_role("link", name="Conferma Lettura")
        if await conferma_lettura.count() > 0 and await conferma_lettura.is_visible():
            await conferma_lettura.click()
            await page_logger.log(page, "conferma_lettura")

        logger.info("[LOGIN] Accesso completato con successo.")

    except Exception as e:
        logger.error(f"[LOGIN][ERRORE] Fallimento durante il login con provider {auth_provider_name}: {e}")
        await page_logger.log(page, "login_exception")
        raise e

async def find_best_option_match(page, selector, search_text):
    """Trova l'opzione che meglio corrisponde al testo cercato"""
    options = await page.locator(f"{selector} option").all()
    best_match = None
    best_score = 0
    
    logger.info(f"[MATCH] Cerco '{search_text}' tra {len(options)} opzioni")
    
    for option in options:
        value = await option.get_attribute("value")
        text = await option.inner_text()
        
        if not value or not text:
            continue
            
        # Calcola similarity score
        search_upper = search_text.upper()
        text_upper = text.upper()
        value_upper = value.upper()
        
        # PRIORITÀ 1: Exact match del valore (per sezioni come P, Q, etc.)
        if search_upper == value_upper:
            logger.info(f"[MATCH] Exact value match trovato: '{text}' -> '{value}'")
            return value
            
        # PRIORITÀ 2: Exact match del testo
        if search_upper == text_upper:
            logger.info(f"[MATCH] Exact text match trovato: '{text}' -> '{value}'")
            return value
            
        # PRIORITÀ 3: Match che inizia con il testo cercato
        if text_upper.startswith(search_upper):
            score = len(search_text) / len(text)
            if score > best_score:
                best_score = score
                best_match = value
                logger.info(f"[MATCH] Candidato (starts with): '{text}' -> '{value}' (score: {score:.2f})")
        
        # PRIORITÀ 4: Value che inizia con il testo cercato
        elif value_upper.startswith(search_upper):
            score = len(search_text) / len(value) * 0.9  # Leggera penalità
            if score > best_score:
                best_score = score
                best_match = value
                logger.info(f"[MATCH] Candidato (value starts with): '{text}' -> '{value}' (score: {score:.2f})")
        
        # PRIORITÀ 5: Match che contiene il testo cercato
        elif search_upper in text_upper:
            score = len(search_text) / len(text) * 0.6  # Maggiore penalità per evitare falsi positivi
            if score > best_score:
                best_score = score
                best_match = value
                logger.info(f"[MATCH] Candidato (contains): '{text}' -> '{value}' (score: {score:.2f})")
    
    if best_match:
        logger.info(f"[MATCH] Migliore match trovato: '{best_match}' (score: {best_score:.2f})")
        return best_match
    else:
        logger.info(f"[MATCH] Nessun match trovato per '{search_text}'")
        return None

async def run_visura(page, provincia='Trieste', comune='Trieste', sezione=None, foglio='9', particella='166', tipo_catasto='T', extract_intestati=True):
    time0=time.time()
    page_logger = PageLogger("visura")
    sezione_info = f", sezione={sezione}" if sezione else ", sezione=None"
    logger.info(f"[VISURA] Inizio visura: provincia={provincia}, comune={comune}{sezione_info}, foglio={foglio}, particella={particella}, tipo_catasto={tipo_catasto}")
    
    # Non creare una nuova pagina, usa quella esistente
    logger.info("[VISURA] Utilizzando pagina di autenticazione esistente")
    
    # STEP 1: Selezione Ufficio Provinciale
    logger.info("[VISURA] Navigando alla pagina di scelta servizio...")
    await page.goto("https://sister3.agenziaentrate.gov.it/Visure/SceltaServizio.do?tipo=/T/TM/VCVC_", timeout=60000)
    await page.wait_for_load_state("networkidle", timeout=30000)
    logger.info("[VISURA] Pagina caricata")
    await page_logger.log(page, "scelta_servizio")
    
    # Verifica che siamo realmente nella pagina di scelta servizio
    current_url = page.url
    if "SceltaServizio.do" not in current_url:
        raise Exception(f"La sessione sembra essere scaduta o si è verificato un errore durante il caricamento della pagina - URL: {current_url}")
    
    # Verifica che le province siano disponibili
    provincia_options_count = await page.locator("select[name='listacom'] option").count()
    if provincia_options_count <= 1:
        raise Exception("La sessione sembra essere scaduta o si è verificato un errore durante il caricamento della pagina")
    
    # Verifica che la pagina sia stata caricata correttamente
    content = await page.content()
    if "error" in content.lower() or "sessione scaduta" in content.lower() or "login" in content.lower():
        raise Exception("La sessione sembra essere scaduta o si è verificato un errore durante il caricamento della pagina")
    
    # Trova e seleziona la provincia corretta
    logger.info(f"[VISURA] Cercando provincia: {provincia}")
    
    # Prima estrai tutte le province disponibili per debug
    provincia_options = await page.locator("select[name='listacom'] option").all()
    available_provinces = []
    for option in provincia_options:
        value = await option.get_attribute("value")
        text = await option.inner_text()
        if value and text:
            available_provinces.append(f"{text} ({value})")
    
    # Se non ci sono province disponibili, probabilmente la sessione è scaduta
    if len(available_provinces) == 0:
        raise Exception("Nessuna provincia disponibile - la sessione potrebbe essere scaduta")
    
    logger.info(f"[VISURA] Province disponibili: {', '.join(available_provinces[:10])}{'...' if len(available_provinces) > 10 else ''}")
    
    provincia_value = await find_best_option_match(page, "select[name='listacom']", provincia)
    
    if not provincia_value:
        raise Exception(f"Provincia '{provincia}' non trovata nelle opzioni disponibili. Prime 10 province disponibili: {', '.join(available_provinces[:10])}")
    
    logger.info(f"[VISURA] Selezionando provincia: {provincia_value}")
    try:
        await page.locator("select[name='listacom']").select_option(provincia_value)
        logger.info("[VISURA] Provincia selezionata")
    except Exception as e:
        raise Exception(f"Errore nella selezione della provincia '{provincia_value}': {e}")
    
    logger.info("[VISURA] Cliccando Applica...")
    await page.locator("input[type='submit'][value='Applica']").click()
    await page.wait_for_load_state("networkidle", timeout=30000)
    logger.info("[VISURA] Applica cliccato, pagina caricata")
    await page_logger.log(page, "provincia_applicata")
    
    # STEP 2: Ricerca per immobili
    logger.info("[VISURA] Cliccando link Immobile...")
    await page.get_by_role("link", name="Immobile").click()
    await page.wait_for_load_state("networkidle", timeout=30000)
    logger.info("[VISURA] Link Immobile cliccato")
    await page_logger.log(page, "immobile")
    
    # STEP 2.1: Seleziona tipo catasto (T=Terreni, F=Fabbricati)
    logger.info(f"[VISURA] Selezionando tipo catasto: {tipo_catasto} ({'Terreni' if tipo_catasto == 'T' else 'Fabbricati'})")
    try:
        await page.locator("select[name='tipoCatasto']").select_option(tipo_catasto)
        logger.info(f"[VISURA] Tipo catasto selezionato: {tipo_catasto}")
    except Exception as e:
        logger.error(f"[VISURA] Errore nella selezione tipo catasto: {e}")
        # Continua comunque, potrebbe essere già selezionato per default
    
    # Trova e seleziona il comune corretto
    logger.info(f"[VISURA] Cercando comune: {comune}")
    
    # Prima estrai tutti i comuni disponibili per debug
    comune_options = await page.locator("select[name='denomComune'] option").all()
    available_comuni = []
    for option in comune_options:
        value = await option.get_attribute("value")
        text = await option.inner_text()
        if value and text:
            available_comuni.append(f"{text} ({value})")
    
    logger.info(f"[VISURA] Comuni disponibili: {', '.join(available_comuni[:10])}{'...' if len(available_comuni) > 10 else ''}")
    
    comune_value = await find_best_option_match(page, "select[name='denomComune']", comune)
    
    if not comune_value:
        raise Exception(f"Comune '{comune}' non trovato nelle opzioni disponibili per la provincia '{provincia}'. Prime 10 comuni disponibili: {', '.join(available_comuni[:10])}")
    
    logger.info(f"[VISURA] Selezionando comune: {comune_value}")
    try:
        await page.locator("select[name='denomComune']").select_option(comune_value)
        logger.info("[VISURA] Comune selezionato")
    except Exception as e:
        raise Exception(f"Errore nella selezione del comune '{comune_value}': {e}")
    
    # IMPORTANTE: Selezionare sezione solo se specificata (non None e non "_")
    if sezione:
        logger.info("[VISURA] Cliccando 'scegli la sezione' per attivare dropdown...")
        await page.locator("input[name='selSezione'][value='scegli la sezione']").click()
        await page.wait_for_load_state("networkidle", timeout=30000)
        logger.info("[VISURA] Button sezione cliccato, dropdown attivato")
        
        # Prima estrai tutte le opzioni disponibili per debug
        options = await page.locator("select[name='sezione'] option").all()
        available_sections = []
        for option in options:
            value = await option.get_attribute("value")
            text = await option.inner_text()
            if value and text:
                available_sections.append(f"{text} ({value})")
        
        logger.info(f"[VISURA] Sezioni disponibili: {', '.join(available_sections)}")
        
        # Se non ci sono sezioni disponibili, salta la selezione della sezione
        if not available_sections:
            logger.info(f"[VISURA] Nessuna sezione disponibile per il comune '{comune}', saltando selezione sezione")
        else:
            # Ora seleziona la sezione
            logger.info(f"[VISURA] Cercando sezione: {sezione}")
            sezione_value = await find_best_option_match(page, "select[name='sezione']", sezione)
            
            if not sezione_value:
                # Se la sezione non è trovata ma ci sono sezioni disponibili, fallback: salta la sezione
                logger.info(f"[VISURA] Sezione '{sezione}' non trovata tra le opzioni disponibili. Sezioni disponibili: {', '.join(available_sections)}. Continuando senza selezionare sezione...")
            else:
                logger.info(f"[VISURA] Selezionando sezione: {sezione_value}")
                try:
                    await page.locator("select[name='sezione']").select_option(sezione_value)
                    logger.info("[VISURA] Sezione selezionata")
                except Exception as e:
                    logger.error(f"[VISURA] Errore nella selezione della sezione '{sezione_value}': {e}. Continuando senza sezione...")
    else:
        logger.info("[VISURA] Sezione non specificata, saltando selezione sezione")
    
    # Inserisci foglio
    logger.info(f"[VISURA] Inserendo foglio: {foglio}")
    await page.locator("input[name='foglio']").click()
    await page.locator("input[name='foglio']").fill(str(foglio))
    logger.info("[VISURA] Foglio inserito")
    
    # Inserisci particella
    logger.info(f"[VISURA] Inserendo particella: {particella}")
    await page.locator("input[name='particella1']").click()
    await page.locator("input[name='particella1']").fill(str(particella))
    logger.info("[VISURA] Particella inserita")
    
    # Clicca Ricerca
    logger.info("[VISURA] Cliccando Ricerca...")
    await page.locator("input[name='scelta'][value='Ricerca']").click()
    await page.wait_for_load_state("networkidle", timeout=30000)
    logger.info("[VISURA] Ricerca cliccata")
    await page_logger.log(page, "ricerca")
    
    # STEP 3: Gestisci conferma assenza subalterno (se necessario)
    try:
        # Controlla se è presente la pagina di conferma assenza subalterno
        conferma_button = page.locator("input[name='confAssSub'][value='Conferma']")
        if await conferma_button.count() > 0:
            logger.info("[VISURA] Rilevata richiesta conferma assenza subalterno...")
            await conferma_button.click()
            await page.wait_for_load_state("networkidle", timeout=30000)
            logger.info("[VISURA] Conferma assenza subalterno cliccata")
            await page_logger.log(page, "conferma_subalterno")
    except Exception as e:
        logger.error(f"[VISURA] Errore o non necessaria conferma subalterno: {e}")
    
    await page_logger.log(page, "risultati")
    
    # STEP 3.1: Controlla se la ricerca ha restituito risultati
    page_text = await page.inner_text("body")
    if "NESSUNA CORRISPONDENZA TROVATA" in page_text:
        time1 = time.time()
        logger.info(f"[VISURA] Nessuna corrispondenza trovata per foglio={foglio}, particella={particella} in {time1-time0:.2f}s")
        return {
            "immobili": [],
            "results": [],
            "total_results": 0,
            "intestati": [],
            "error": "NESSUNA CORRISPONDENZA TROVATA"
        }
    
    # STEP 4: Estrazione tabella Elenco Immobili
    logger.info("[VISURA] Estraendo tabella Elenco Immobili...")
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
                logger.debug(f"[DEBUG] Tentativo selettore: {selector}")
                immobili_table = page.locator(selector)
                count = await immobili_table.count()
                logger.debug(f"[DEBUG] Trovate {count} tabelle con selettore {selector}")
                
                if count > 0:
                    # Se ci sono più tabelle, proviamo a trovare quella giusta
                    for i in range(count):
                        try:
                            table_elem = immobili_table.nth(i)
                            immobili_html = await table_elem.inner_html(timeout=10000)
                            
                            # Verifica se contiene le colonne che ci aspettiamo
                            if 'Foglio' in immobili_html or 'Particella' in immobili_html:
                                immobili = parse_table(immobili_html)
                                logger.info(f"[VISURA] Tabella Immobili estratta: {len(immobili)} righe con selettore {selector} (tabella {i})")
                                break
                        except Exception as e:
                            logger.error(f"[DEBUG] Errore con tabella {i}: {e}")
                            continue
                    
                    if immobili:
                        break
                        
            except Exception as e:
                logger.error(f"[DEBUG] Errore con selettore {selector}: {e}")
                continue
        
        if not immobili:
            logger.info("[VISURA] Tabella Elenco Immobili non trovata con nessun selettore")
            await page_logger.log(page, "immobili_non_trovati")
            immobili = []
    except Exception as e:
        logger.error(f"[VISURA] Errore estrazione immobili: {e}")
        immobili = []

    # STEP 5: Gestisci risultati multipli iterando su ogni radio button
    logger.info("[VISURA] Cercando radio button per risultati multipli...")
    
    # Array per raccogliere tutti i risultati
    all_results = []
    
    try:
        # Trova tutti i radio button per la selezione degli immobili
        radio_buttons = page.locator("input[type='radio'][property='visImmSel'], input[type='radio'][name='visImmSel']")
        radio_count = await radio_buttons.count()
        logger.info(f"[VISURA] Trovati {radio_count} radio button per selezione immobili")
        
        if radio_count == 0:
            logger.info("[VISURA] Nessun radio button trovato, provo direttamente con Intestati")
            # Se non ci sono radio button, procedi direttamente
            radio_count = 1
        
        # Itera attraverso ogni risultato
        for result_index in range(radio_count):
            logger.info(f"[VISURA] Processando risultato {result_index + 1}/{radio_count}")
            
            # Controlla se questo immobile è "Soppressa" prima di processarlo
            current_immobile_data = immobili[result_index] if result_index < len(immobili) else {}
            partita = current_immobile_data.get('Partita', current_immobile_data.get('partita', ''))
            
            if partita == "Soppressa":
                logger.info(f"[VISURA] Risultato {result_index + 1} ha partita 'Soppressa', saltando estrazione intestati")
                # Aggiungi questo risultato alla lista senza intestati
                result_data = {
                    "result_index": result_index + 1,
                    "immobile": current_immobile_data,
                    "intestati": []  # Nessun intestato per record soppressi
                }
                all_results.append(result_data)
                logger.info(f"[VISURA] Risultato {result_index + 1} completato (saltato per Soppressa)")
                continue
            
            # Se ci sono radio button, seleziona quello corrente
            if radio_count > 1 or await radio_buttons.count() > 0:
                try:
                    logger.info(f"[VISURA] Selezionando radio button {result_index}")
                    await radio_buttons.nth(result_index).click()
                    await page.wait_for_timeout(1000)  # Breve pausa
                    logger.info(f"[VISURA] Radio button {result_index} selezionato")
                except Exception as e:
                    logger.error(f"[VISURA] Errore nella selezione radio button {result_index}: {e}")
                    continue
            
            # Inizializza lista intestati vuota
            intestati = []
            
            # Estrai intestati solo se richiesto
            if extract_intestati:
                # Clicca su "Intestati" per questo risultato
                logger.info(f"[VISURA] Cliccando Intestati per risultato {result_index + 1}...")
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
                        "a:has-text('Intestati')"
                    ]
                    
                    intestati_button = None
                    for selector in intestati_button_selectors:
                        try:
                            locator = page.locator(selector)
                            if await locator.count() > 0:
                                intestati_button = locator.first
                                logger.info(f"[VISURA] Bottone Intestati trovato con selettore: {selector}")
                                break
                        except Exception as e:
                            logger.info(f"[VISURA] Selettore {selector} fallito: {e}")
                            continue
                    
                    if intestati_button:
                        await intestati_button.click()
                        await page.wait_for_load_state("networkidle", timeout=30000)
                        logger.info(f"[VISURA] Intestati cliccato per risultato {result_index + 1}")
                        await page_logger.log(page, f"intestati_r{result_index + 1}")

                        # Estrai tabella Elenco Intestati per questo risultato
                        logger.info(f"[VISURA] Estraendo tabella Elenco Intestati per risultato {result_index + 1}...")
                        
                        selectors = [
                            "table.listaIsp4",  # Stessa classe delle tabelle
                            "table[class*='lista']",  # Cerca tabelle con classe che contiene 'lista'
                            "table:has(th:text('Cognome'))",  # Cerca tabella con header 'Cognome'
                            "table:has(th:text('Nome'))",  # Cerca tabella con header 'Nome'
                            "table:has(th:text('Nominativo o denominazione'))",  # Nuovo header specifico
                            "table:has(th:text('Codice fiscale'))",  # Nuovo header specifico
                            "table:has(th:text('Titolarità'))",  # Nuovo header specifico
                            "table",  # Fallback: qualsiasi tabella
                        ]
                        
                        for selector in selectors:
                            try:
                                logger.debug(f"[DEBUG] Tentativo selettore intestati: {selector}")
                                intestati_table = page.locator(selector)
                                count = await intestati_table.count()
                                logger.debug(f"[DEBUG] Trovate {count} tabelle con selettore {selector}")
                                
                                if count > 0:
                                    # Se ci sono più tabelle, proviamo a trovare quella giusta
                                    for i in range(count):
                                        try:
                                            table_elem = intestati_table.nth(i)
                                            intestati_html = await table_elem.inner_html(timeout=10000)
                                            
                                            # Verifica se contiene le colonne che ci aspettiamo per gli intestati
                                            if ('Cognome' in intestati_html or 'Nome' in intestati_html or 'Soggetto' in intestati_html or 
                                                'Nominativo o denominazione' in intestati_html or 'Codice fiscale' in intestati_html or 
                                                'Titolarità' in intestati_html):
                                                intestati = parse_table(intestati_html)
                                                logger.info(f"[VISURA] Tabella Intestati estratta per risultato {result_index + 1}: {len(intestati)} righe")
                                                break
                                            else:
                                                # Proviamo comunque a parsare la tabella per vedere cosa contiene
                                                temp_intestati = parse_table(intestati_html)
                                                logger.debug(f"[DEBUG] Tabella {i} non contiene colonne intestati attese, ma contiene:")
                                                logger.debug(f"[DEBUG] Headers trovati nella tabella: {list(temp_intestati[0].keys()) if temp_intestati else 'Nessun dato'}")
                                                
                                                # Se la tabella ha dati e non è quella degli immobili, proviamo ad usarla
                                                if temp_intestati:
                                                    # Verifica che non sia la tabella immobili (che contiene "Foglio")
                                                    if 'Foglio' not in intestati_html and 'Particella' not in intestati_html:
                                                        intestati = temp_intestati
                                                        logger.info(f"[VISURA] Tabella Intestati estratta (fallback) per risultato {result_index + 1}: {len(intestati)} righe")
                                                        break
                                        except Exception as e:
                                            logger.error(f"[DEBUG] Errore con tabella intestati {i}: {e}")
                                            continue
                                    
                                    if intestati:
                                        break
                                        
                            except Exception as e:
                                logger.error(f"[DEBUG] Errore con selettore intestati {selector}: {e}")
                                continue
                        
                        # Se ci sono altri risultati da processare, torna alla pagina precedente
                        if result_index < radio_count - 1:
                            logger.info(f"[VISURA] Tornando indietro per processare il prossimo risultato...")
                            try:
                                # Cerca il bottone "Indietro"
                                indietro_button = page.locator("input[name='indietro'][value='Indietro']")
                                if await indietro_button.count() > 0:
                                    await indietro_button.click()
                                    await page.wait_for_load_state("networkidle", timeout=30000)
                                    logger.info(f"[VISURA] Tornato indietro, pronto per risultato {result_index + 2}")
                                    await page_logger.log(page, f"indietro_r{result_index + 1}")
                                else:
                                    logger.info("[VISURA] Bottone Indietro non trovato")
                                    break
                            except Exception as e:
                                logger.error(f"[VISURA] Errore nel tornare indietro: {e}")
                                break
                        
                    else:
                        logger.info(f"[VISURA] Bottone Intestati non trovato per risultato {result_index + 1}")
                        
                except Exception as e:
                    logger.error(f"[VISURA] Errore estrazione intestati per risultato {result_index + 1}: {e}")
            else:
                logger.info(f"[VISURA] Estrazione intestati saltata per risultato {result_index + 1} (extract_intestati=False)")
            
            # Aggiungi questo risultato alla lista
            result_data = {
                "result_index": result_index + 1,
                "immobile": current_immobile_data,
                "intestati": intestati
            }
            all_results.append(result_data)
            logger.info(f"[VISURA] Risultato {result_index + 1} completato: {len(intestati)} intestati trovati")
        
        logger.info(f"[VISURA] Completato processing di {len(all_results)} risultati")
        
    except Exception as e:
        logger.error(f"[VISURA] Errore generale nel processing risultati multipli: {e}")
        # Fallback: se c'è un errore, prova il metodo originale
        all_results = []

    # Se non abbiamo risultati multipli, usa il metodo originale come fallback
    if not all_results:
        logger.info("[VISURA] Nessun risultato multiplo trovato, usando metodo originale...")
        intestati = []
        
        # Estrai intestati solo se richiesto
        if extract_intestati:
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
                    "a:has-text('Intestati')"
                ]
                
                intestati_button = None
                for selector in intestati_button_selectors:
                    try:
                        locator = page.locator(selector)
                        if await locator.count() > 0:
                            intestati_button = locator.first
                            logger.info(f"[VISURA] Bottone Intestati trovato con selettore (fallback): {selector}")
                            break
                    except Exception as e:
                        logger.info(f"[VISURA] Selettore {selector} fallito (fallback): {e}")
                        continue
                
                if intestati_button:
                    await intestati_button.click()
                    await page.wait_for_load_state("networkidle", timeout=30000)
                    logger.info("[VISURA] Intestati cliccato (metodo originale)")
                    await page_logger.log(page, "intestati_fallback")

                    # Estrai tabella Elenco Intestati
                    selectors = [
                        "table.listaIsp4",
                        "table[class*='lista']",
                        "table:has(th:text('Cognome'))",
                        "table:has(th:text('Nome'))",
                        "table:has(th:text('Nominativo o denominazione'))",  # Nuovo header specifico
                        "table:has(th:text('Codice fiscale'))",  # Nuovo header specifico
                        "table:has(th:text('Titolarità'))",  # Nuovo header specifico
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
                                        
                                        if ('Cognome' in intestati_html or 'Nome' in intestati_html or 'Soggetto' in intestati_html or
                                            'Nominativo o denominazione' in intestati_html or 'Codice fiscale' in intestati_html or 
                                            'Titolarità' in intestati_html):
                                            intestati = parse_table(intestati_html)
                                            logger.info(f"[VISURA] Tabella Intestati estratta (metodo originale): {len(intestati)} righe")
                                            break
                                        else:
                                            temp_intestati = parse_table(intestati_html)
                                            if temp_intestati:
                                                if 'Foglio' not in intestati_html and 'Particella' not in intestati_html:
                                                    intestati = temp_intestati
                                                    logger.info(f"[VISURA] Tabella Intestati estratta (fallback originale): {len(intestati)} righe")
                                                    break
                                    except Exception as e:
                                        logger.error(f"[DEBUG] Errore con tabella intestati {i}: {e}")
                                        continue
                                
                                if intestati:
                                    break
                                    
                        except Exception as e:
                            logger.error(f"[DEBUG] Errore con selettore intestati {selector}: {e}")
                            continue
                    
                else:
                    logger.info("[VISURA] Bottone Intestati non trovato (metodo originale)")
                    
            except Exception as e:
                logger.error(f"[VISURA] Errore nel metodo originale: {e}")
        else:
            logger.info("[VISURA] Estrazione intestati saltata (extract_intestati=False)")
        
        # Crea un singolo risultato per compatibilità
        all_results = [{
            "result_index": 1,
            "immobile": immobili[0] if immobili else {},
            "intestati": intestati
        }]

    time1=time.time()
    logger.info(f"[VISURA] Visura completata con successo in {time1-time0:.2f} secondi")
    logger.info(f"[VISURA] Totale risultati processati: {len(all_results)}")

    # Prepara il risultato finale
    result = {
        "immobili": immobili,
        "results": all_results,
        "total_results": len(all_results)
    }
    
    # Mantieni compatibilità con il formato originale per il primo risultato
    if all_results:
        result["intestati"] = all_results[0]["intestati"]
    else:
        result["intestati"] = []

    return result


async def logout(page: Page):
    """Effettua il logout dal portale SISTER"""
    page_logger = PageLogger("logout")
    try:
        await page_logger.log(page, "before_logout")
        logger.info("[LOGOUT] Cercando il bottone 'Esci'...")
        
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
                logger.info(f"[LOGOUT] Tentativo selettore: {selector}")
                logout_button = page.locator(selector)
                count = await logout_button.count()
                logger.info(f"[LOGOUT] Trovati {count} elementi con selettore {selector}")
                
                if count > 0:
                    await logout_button.first.click()
                    with suppress(Exception):
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    logger.info(f"[LOGOUT] Logout effettuato con successo usando selettore: {selector}")
                    logout_success = True
                    break
                    
            except Exception as e:
                logger.error(f"[LOGOUT] Errore con selettore {selector}: {e}")
                continue
        
        if not logout_success:
            logger.warning("[LOGOUT] ATTENZIONE: Non è stato possibile trovare il bottone 'Esci'")
            await page_logger.log(page, "logout_bottone_non_trovato")
        else:
            await page_logger.log(page, "after_logout")
            logger.info("[LOGOUT] Sessione chiusa correttamente")
            
    except Exception as e:
        logger.error(f"[LOGOUT] Errore durante il logout: {e}")
        await page_logger.log(page, "logout_errore")

async def extract_all_sezioni(page: Page, tipo_catasto: str = 'T', max_province: int = 200) -> list:
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
    page_logger = PageLogger("sezioni")
    
    try:
        logger.info(f"[SEZIONI] Iniziando estrazione sezioni per tipo catasto: {tipo_catasto} (max {max_province} province)")
        
        # Naviga alla pagina di scelta servizio
        logger.info("[SEZIONI] Navigando alla pagina di scelta servizio...")
        await page.goto("https://sister3.agenziaentrate.gov.it/Visure/SceltaServizio.do?tipo=/T/TM/VCVC_", timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=30000)
        logger.info("[SEZIONI] Pagina caricata")
        await page_logger.log(page, "scelta_servizio")
        
        # Estrai tutte le province
        logger.info("[SEZIONI] Estraendo lista province...")
        provincia_options = await page.locator("select[name='listacom'] option").all()
        province_list = []
        
        for option in provincia_options:
            value = await option.get_attribute("value")
            text = await option.inner_text()
            if value and text and value.strip() and text.strip():
                # Salta "NAZIONALE" che sembra problematico
                if "NAZIONALE" not in text.upper():
                    province_list.append({"value": value.strip(), "text": text.strip()})
        
        # Limita il numero di province per evitare timeout
        province_list = province_list[:max_province]
        
        logger.info(f"[SEZIONI] Processando {len(province_list)} province")
        
        for i, provincia in enumerate(province_list):
            logger.info(f"[SEZIONI] Processando provincia {i+1}/{len(province_list)}: {provincia['text']}")
            
            try:
                # Seleziona la provincia (stesso modo di run_visura)
                logger.info(f"[SEZIONI] Selezionando provincia: {provincia['value']}")
                await page.locator("select[name='listacom']").select_option(provincia['value'])
                logger.info("[SEZIONI] Provincia selezionata")
                
                logger.info("[SEZIONI] Cliccando Applica...")
                await page.locator("input[type='submit'][value='Applica']").click()
                await page.wait_for_load_state("networkidle", timeout=30000)
                logger.info("[SEZIONI] Applica cliccato, pagina caricata")
                
                # Vai alla ricerca immobili (stesso modo di run_visura)
                logger.info("[SEZIONI] Cliccando link Immobile...")
                await page.get_by_role("link", name="Immobile").click()
                await page.wait_for_load_state("networkidle", timeout=30000)
                logger.info("[SEZIONI] Link Immobile cliccato")
                
                # Seleziona tipo catasto (stesso modo di run_visura)
                logger.info(f"[SEZIONI] Selezionando tipo catasto: {tipo_catasto}")
                try:
                    await page.locator("select[name='tipoCatasto']").select_option(tipo_catasto)
                    logger.info(f"[SEZIONI] Tipo catasto selezionato: {tipo_catasto}")
                except Exception as e:
                    logger.error(f"[SEZIONI] Errore selezione tipo catasto per {provincia['text']}: {e}")
                
                # Estrai tutti i comuni per questa provincia
                logger.info("[SEZIONI] Estraendo lista comuni...")
                comune_options = await page.locator("select[name='denomComune'] option").all()
                comuni_list = []
                
                for option in comune_options:
                    value = await option.get_attribute("value")
                    text = await option.inner_text()
                    if value and text and value.strip() and text.strip():
                        comuni_list.append({"value": value.strip(), "text": text.strip()})
                
                logger.info(f"[SEZIONI] Trovati {len(comuni_list)} comuni per {provincia['text']}")
                
                for j, comune in enumerate(comuni_list):
                    logger.info(f"[SEZIONI] Processando comune {j+1}/{len(comuni_list)} per {provincia['text']}: {comune['text']}")
                    
                    try:
                        # Seleziona il comune (stesso modo di run_visura)
                        logger.info(f"[SEZIONI] Selezionando comune: {comune['value']}")
                        await page.locator("select[name='denomComune']").select_option(comune['value'])
                        logger.info("[SEZIONI] Comune selezionato")
                        
                        # Attiva selezione sezione (ESATTO come in run_visura)
                        logger.info("[SEZIONI] Cliccando 'scegli la sezione' per attivare dropdown...")
                        await page.locator("input[name='selSezione'][value='scegli la sezione']").click()
                        await page.wait_for_load_state("networkidle", timeout=30000)
                        logger.info("[SEZIONI] Button sezione cliccato, dropdown attivato")
                        
                        # Estrai le sezioni per questo comune (stesso modo di run_visura)
                        logger.info(f"[SEZIONI] Estraendo sezioni per comune {comune['text']}...")
                        comune_sezioni_data = []
                        
                        try:
                            # Prima verifica se ci sono sezioni disponibili
                            sezione_options = await page.locator("select[name='sezione'] option").all()
                            available_sections = []
                            
                            for option in sezione_options:
                                value = await option.get_attribute("value")
                                text = await option.inner_text()
                                if value and text and value.strip() and text.strip():
                                    available_sections.append({
                                        "value": value.strip(), 
                                        "text": text.strip()
                                    })
                            
                            logger.info(f"[SEZIONI] Trovate {len(available_sections)} sezioni per {comune['text']}")
                            
                            # Aggiungi tutte le sezioni trovate
                            for sezione in available_sections:
                                comune_sezioni_data.append({
                                    "provincia_nome": provincia['text'],
                                    "provincia_value": provincia['value'],
                                    "comune_nome": comune['text'],
                                    "comune_value": comune['value'],
                                    "sezione_nome": sezione['text'],
                                    "sezione_value": sezione['value'],
                                    "tipo_catasto": tipo_catasto
                                })
                            
                            # Se non ci sono sezioni, aggiungi comunque il comune senza sezione
                            if len(available_sections) == 0:
                                logger.info(f"[SEZIONI] Nessuna sezione trovata per {comune['text']}, aggiungendo comune senza sezione")
                                comune_sezioni_data.append({
                                    "provincia_nome": provincia['text'],
                                    "provincia_value": provincia['value'],
                                    "comune_nome": comune['text'],
                                    "comune_value": comune['value'],
                                    "sezione_nome": None,
                                    "sezione_value": None,
                                    "tipo_catasto": tipo_catasto
                                })
                                    
                        except Exception as e:
                            logger.error(f"[SEZIONI] Errore estrazione sezioni per {comune['text']}: {e}")
                            # Aggiungi record senza sezione in caso di errore
                            comune_sezioni_data.append({
                                "provincia_nome": provincia['text'],
                                "provincia_value": provincia['value'],
                                "comune_nome": comune['text'],
                                "comune_value": comune['value'],
                                "sezione_nome": None,
                                "sezione_value": None,
                                "tipo_catasto": tipo_catasto
                            })
                        
                        # Aggiungi le sezioni alla lista locale
                        if comune_sezioni_data:
                            sezioni_data.extend(comune_sezioni_data)
                            logger.info(f"[SEZIONI] Aggiunte {len(comune_sezioni_data)} sezioni per {comune['text']}")
                            
                    except Exception as e:
                        logger.error(f"[SEZIONI] Errore processando comune {comune['text']}: {e}")
                        continue
                
                logger.info(f"[SEZIONI] Provincia {provincia['text']} completata. Sezioni totali trovate finora: {len(sezioni_data)}")
                
                # Torna alla pagina principale per la prossima provincia
                logger.info("[SEZIONI] Tornando alla pagina principale per prossima provincia...")
                await page.goto("https://sister3.agenziaentrate.gov.it/Visure/SceltaServizio.do?tipo=/T/TM/VCVC_", timeout=60000)
                await page.wait_for_load_state("networkidle", timeout=30000)
                logger.info("[SEZIONI] Tornato alla pagina principale")
                
            except Exception as e:
                logger.error(f"[SEZIONI] Errore processando provincia {provincia['text']}: {e}")
                continue
        
        logger.info(f"[SEZIONI] Estrazione completata. Trovate {len(sezioni_data)} sezioni totali")
        return sezioni_data
        
    except Exception as e:
        logger.error(f"[SEZIONI] Errore durante estrazione sezioni: {e}")
        return sezioni_data


async def run_visura_immobile(page, provincia='Trieste', comune='Trieste', sezione=None, foglio='9', particella='166', subalterno=None):
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
    page_logger = PageLogger("visura_immobile")
    sezione_info = f", sezione={sezione}" if sezione else ", sezione=None"
    logger.info(f"[VISURA_IMMOBILE] Inizio visura immobile: provincia={provincia}, comune={comune}{sezione_info}, foglio={foglio}, particella={particella}, subalterno={subalterno}")
    
    if not subalterno:
        raise ValueError("Il subalterno è obbligatorio per le visure per immobile specifico")
    
    # STEP 1: Selezione Ufficio Provinciale
    logger.info("[VISURA_IMMOBILE] Navigando alla pagina di scelta servizio...")
    await page.goto("https://sister3.agenziaentrate.gov.it/Visure/SceltaServizio.do?tipo=/T/TM/VCVC_", timeout=60000)
    await page.wait_for_load_state("networkidle", timeout=30000)
    logger.info("[VISURA_IMMOBILE] Pagina caricata")
    await page_logger.log(page, "scelta_servizio")
    
    # Verifica che siamo realmente nella pagina di scelta servizio
    current_url = page.url
    if "SceltaServizio.do" not in current_url:
        raise Exception(f"La sessione sembra essere scaduta o si è verificato un errore - URL: {current_url}")
    
    # Trova e seleziona la provincia corretta
    logger.info(f"[VISURA_IMMOBILE] Cercando provincia: {provincia}")
    provincia_value = await find_best_option_match(page, "select[name='listacom']", provincia)
    
    if not provincia_value:
        raise Exception(f"Provincia '{provincia}' non trovata nelle opzioni disponibili")
    
    logger.info(f"[VISURA_IMMOBILE] Selezionando provincia: {provincia_value}")
    await page.locator("select[name='listacom']").select_option(provincia_value)
    logger.info("[VISURA_IMMOBILE] Cliccando Applica...")
    await page.locator("input[type='submit'][value='Applica']").click()
    await page.wait_for_load_state("networkidle", timeout=30000)
    await page_logger.log(page, "provincia_applicata")
    
    # STEP 2: Ricerca per immobili
    logger.info("[VISURA_IMMOBILE] Cliccando link Immobile...")
    await page.get_by_role("link", name="Immobile").click()
    await page.wait_for_load_state("networkidle", timeout=30000)
    await page_logger.log(page, "immobile")
    
    # STEP 2.1: Seleziona tipo catasto FABBRICATI (F)
    logger.info("[VISURA_IMMOBILE] Selezionando tipo catasto: F (Fabbricati)")
    await page.locator("select[name='tipoCatasto']").select_option("F")
    
    # Trova e seleziona il comune
    logger.info(f"[VISURA_IMMOBILE] Cercando comune: {comune}")
    comune_value = await find_best_option_match(page, "select[name='denomComune']", comune)
    
    if not comune_value:
        raise Exception(f"Comune '{comune}' non trovato nelle opzioni disponibili")
    
    logger.info(f"[VISURA_IMMOBILE] Selezionando comune: {comune_value}")
    await page.locator("select[name='denomComune']").select_option(comune_value)
    
    # Seleziona sezione se specificata
    if sezione:
        logger.info("[VISURA_IMMOBILE] Cliccando 'scegli la sezione' per attivare dropdown...")
        await page.locator("input[name='selSezione'][value='scegli la sezione']").click()
        await page.wait_for_load_state("networkidle", timeout=30000)
        
        # Controlla se ci sono sezioni disponibili
        options = await page.locator("select[name='sezione'] option").all()
        available_sections = []
        for option in options:
            value = await option.get_attribute("value")
            text = await option.inner_text()
            if value and text:
                available_sections.append(f"{text} ({value})")
        
        if not available_sections:
            logger.info(f"[VISURA_IMMOBILE] Nessuna sezione disponibile per il comune '{comune}', saltando selezione sezione")
        else:
            logger.info(f"[VISURA_IMMOBILE] Cercando sezione: {sezione}")
            sezione_value = await find_best_option_match(page, "select[name='sezione']", sezione)
            
            if not sezione_value:
                logger.info(f"[VISURA_IMMOBILE] Sezione '{sezione}' non trovata tra le opzioni disponibili. Sezioni disponibili: {', '.join(available_sections)}. Continuando senza selezionare sezione...")
            else:
                logger.info(f"[VISURA_IMMOBILE] Selezionando sezione: {sezione_value}")
                try:
                    await page.locator("select[name='sezione']").select_option(sezione_value)
                    logger.info("[VISURA_IMMOBILE] Sezione selezionata")
                except Exception as e:
                    logger.error(f"[VISURA_IMMOBILE] Errore nella selezione della sezione '{sezione_value}': {e}. Continuando senza sezione...")
    
    # Inserisci dati immobile
    logger.info(f"[VISURA_IMMOBILE] Inserendo foglio: {foglio}")
    await page.locator("input[name='foglio']").fill(str(foglio))
    
    logger.info(f"[VISURA_IMMOBILE] Inserendo particella: {particella}")
    await page.locator("input[name='particella1']").fill(str(particella))
    
    logger.info(f"[VISURA_IMMOBILE] Inserendo subalterno: {subalterno}")
    await page.locator("input[name='subalterno1']").fill(str(subalterno))
    
    # Clicca Ricerca
    logger.info("[VISURA_IMMOBILE] Cliccando Ricerca...")
    await page.locator("input[name='scelta'][value='Ricerca']").click()
    await page.wait_for_load_state("networkidle", timeout=30000)
    await page_logger.log(page, "ricerca")
    
    # STEP 3: Gestisci conferma assenza subalterno (se necessario)
    try:
        conferma_button = page.locator("input[name='confAssSub'][value='Conferma']")
        if await conferma_button.count() > 0:
            logger.info("[VISURA_IMMOBILE] Rilevata richiesta conferma assenza subalterno...")
            await conferma_button.click()
            await page.wait_for_load_state("networkidle", timeout=30000)
            await page_logger.log(page, "conferma_subalterno")
    except Exception as e:
        logger.error(f"[VISURA_IMMOBILE] Errore o non necessaria conferma subalterno: {e}")
    
    await page_logger.log(page, "risultati")
    
    # STEP 4: Estrazione dati immobile (opzionale, principalmente per verifica)
    logger.info("[VISURA_IMMOBILE] Estraendo dati immobile...")
    immobile_data = {}
    try:
        immobili_table = page.locator("table.listaIsp4").first
        if await immobili_table.count() > 0:
            immobili_html = await immobili_table.inner_html()
            immobili = parse_table(immobili_html)
            immobile_data = immobili[0] if immobili else {}
            logger.info(f"[VISURA_IMMOBILE] Dati immobile estratti: {immobile_data}")
    except Exception as e:
        logger.error(f"[VISURA_IMMOBILE] Errore estrazione dati immobile: {e}")
    
    # STEP 5: Estrazione intestati
    logger.info("[VISURA_IMMOBILE] Cliccando Intestati...")
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
            "a:has-text('Intestati')"
        ]
        
        intestati_button = None
        for selector in intestati_button_selectors:
            try:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    intestati_button = locator.first
                    logger.info(f"[VISURA_IMMOBILE] Bottone Intestati trovato con selettore: {selector}")
                    break
            except Exception as e:
                logger.info(f"[VISURA_IMMOBILE] Selettore {selector} fallito: {e}")
                continue
        
        if intestati_button:
            await intestati_button.click()
            await page.wait_for_load_state("networkidle", timeout=30000)
            logger.info("[VISURA_IMMOBILE] Intestati cliccato")
            await page_logger.log(page, "intestati")

            # Estrai tabella Elenco Intestati
            logger.info("[VISURA_IMMOBILE] Estraendo tabella Elenco Intestati...")
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
                                
                                if ('Cognome' in intestati_html or 'Nome' in intestati_html or 'Soggetto' in intestati_html or
                                    'Nominativo o denominazione' in intestati_html or 'Codice fiscale' in intestati_html or 
                                    'Titolarità' in intestati_html):
                                    intestati = parse_table(intestati_html)
                                    logger.info(f"[VISURA_IMMOBILE] Tabella Intestati estratta: {len(intestati)} righe")
                                    break
                                else:
                                    temp_intestati = parse_table(intestati_html)
                                    if temp_intestati:
                                        if 'Foglio' not in intestati_html and 'Particella' not in intestati_html:
                                            intestati = temp_intestati
                                            logger.info(f"[VISURA_IMMOBILE] Tabella Intestati estratta (fallback): {len(intestati)} righe")
                                            break
                            except Exception as e:
                                logger.error(f"[DEBUG] Errore con tabella intestati {i}: {e}")
                                continue
                        
                        if intestati:
                            break
                            
                except Exception as e:
                    logger.error(f"[DEBUG] Errore con selettore intestati {selector}: {e}")
                    continue
        else:
            logger.info("[VISURA_IMMOBILE] Bottone Intestati non trovato con nessun selettore")
            
            # Debug: stampa tutti gli input e button disponibili
            try:
                all_inputs = await page.locator("input").all()
                logger.debug(f"[DEBUG] Trovati {len(all_inputs)} elementi input:")
                for i, inp in enumerate(all_inputs):
                    try:
                        tag_name = await inp.evaluate("el => el.tagName")
                        input_type = await inp.get_attribute("type") or "text"
                        name = await inp.get_attribute("name") or ""
                        value = await inp.get_attribute("value") or ""
                        id_attr = await inp.get_attribute("id") or ""
                        class_attr = await inp.get_attribute("class") or ""
                        logger.debug(f"[DEBUG]   {i}: {tag_name} type='{input_type}' name='{name}' value='{value}' id='{id_attr}' class='{class_attr}'")
                    except Exception as e:
                        logger.debug(f"[DEBUG]   {i}: Error getting attributes: {e}")
                
                all_buttons = await page.locator("button").all()
                logger.debug(f"[DEBUG] Trovati {len(all_buttons)} elementi button:")
                for i, btn in enumerate(all_buttons):
                    try:
                        text = await btn.inner_text()
                        name = await btn.get_attribute("name") or ""
                        value = await btn.get_attribute("value") or ""
                        id_attr = await btn.get_attribute("id") or ""
                        class_attr = await btn.get_attribute("class") or ""
                        logger.debug(f"[DEBUG]   {i}: text='{text}' name='{name}' value='{value}' id='{id_attr}' class='{class_attr}'")
                    except Exception as e:
                        logger.debug(f"[DEBUG]   {i}: Error getting button attributes: {e}")
                        
            except Exception as e:
                logger.error(f"[DEBUG] Errore nel debug degli elementi: {e}")
    except Exception as e:
        logger.error(f"[VISURA_IMMOBILE] Errore estrazione intestati: {e}")
    
    time1 = time.time()
    logger.info(f"[VISURA_IMMOBILE] Visura immobile completata in {time1-time0:.2f} secondi")
    
    result = {
        "immobile": immobile_data,
        "intestati": intestati,
        "total_intestati": len(intestati)
    }
    
    return result
