from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
import os
import re
from utils import PageLogger

# Costanti
LOGIN_URL = "https://iampe.agenziaentrate.gov.it/sam/UI/Login?realm=/agenziaentrate"


def _get_and_validate_credentials(method: str) -> tuple[str, str]:
    """Estrae e valida le credenziali ADE dalle variabili d'ambiente."""
    ade_username = os.getenv("ADE_USERNAME")
    ade_password = os.getenv("ADE_PASSWORD")

    if not ade_username or not ade_password:
        raise ValueError(f"Le variabili d'ambiente ADE_USERNAME e ADE_PASSWORD devono essere impostate per il login {method}")

    return ade_username, ade_password


async def _navigate_to_login(page: Page, method: str) -> None:
    """Naviga alla pagina di login dell'Agenzia delle Entrate."""
    print(f"[LOGIN][{method}] Navigo alla pagina di login...")
    await page.goto(LOGIN_URL)


async def _click_with_fallback(
    page: Page,
    name_pattern,
    fallback_selector: str,
    method: str,
    action_name: str,
    timeout: int = 20000,
    role: str = None
) -> None:
    """Prova a cliccare un elemento con selettore di fallback.
    
    Args:
        page: Oggetto pagina Playwright
        name_pattern: Stringa o regex per il nome dell'elemento
        fallback_selector: Selettore CSS/Playwright di fallback
        method: Metodo di login (per logging)
        action_name: Descrizione azione (per logging)
        timeout: Timeout click in ms
        role: Ruolo HTML specifico da provare prima (es. 'tab', 'button')
    """
    if role:
        # Prova prima il ruolo specifico
        try:
            await page.get_by_role(role, name=name_pattern).click(timeout=timeout)
            return
        except PlaywrightTimeoutError:
            pass
    
    # Prova button e poi link (comportamento di default)
    try:
        await page.get_by_role("button", name=name_pattern).click(timeout=timeout)
        return
    except PlaywrightTimeoutError:
        pass
    
    try:
        await page.get_by_role("link", name=name_pattern).click(timeout=timeout)
        return
    except PlaywrightTimeoutError:
        pass
    
    # Ultimo fallback tramite locator
    print(f"[LOGIN][{method}] {action_name} - provo il selettore di fallback...")
    await page.locator(fallback_selector).first.click(timeout=timeout)


async def login(page: Page):
    login_method = os.getenv("ADE_LOGIN_METHOD", "SPID").strip().upper()
    logger = PageLogger("login")
    step = "init"

    try:
        if login_method == "CIE":
            await login_cie(page, logger)
        elif login_method == "SPID":
            await login_spid(page, logger)
        else:
            raise ValueError("ADE_LOGIN_METHOD deve essere 'CIE' oppure 'SPID'")

        print(f"[LOGIN][{login_method}] accesso riuscito.")
        await logger.log(page, f"{login_method.lower()}_accesso_riuscito")

        # Flusso post-login comune per entrambi i metodi
        step = "open_sister_after_auth"
        await _open_sister_after_auth(page, logger)
    except Exception:
        await logger.log(page, f"ERRORE_{step}")
        raise


async def login_spid(page: Page, logger: PageLogger):
    method = "SPID"
    ade_username, ade_password = _get_and_validate_credentials(method)
    step = "init"

    try:
        step = "goto_login"
        await _navigate_to_login(page, method)
        await logger.log(page, "spid_goto_login")

        step = "entra_con_spid"
        print(f"[LOGIN][{method}] Clicco 'Entra con SPID'...")
        await page.get_by_role("button", name="Entra con SPID").click()
        await logger.log(page, "spid_entra_con_spid")

        step = "sielte_id"
        print(f"[LOGIN][{method}] Clicco 'Sielte ID'...")
        await page.locator('a[href*="sielte"]').click()
        await logger.log(page, "spid_sielte_id")

        step = "username"
        print(f"[LOGIN][{method}] Inserisco username...")
        await page.get_by_role("textbox", name="Codice Fiscale / Partita IVA").press("CapsLock")
        await page.get_by_role("textbox", name="Codice Fiscale / Partita IVA").fill(ade_username)
        await logger.log(page, "spid_username")

        step = "password"
        print(f"[LOGIN][{method}] Inserisco password...")
        await page.get_by_role("textbox", name="Password").click()
        await page.get_by_role("textbox", name="Password").fill(ade_password)
        await logger.log(page, "spid_password")

        step = "prosegui"
        print(f"[LOGIN][{method}] Clicco 'Prosegui'...")
        await page.get_by_role("button", name="Prosegui").click()
        await logger.log(page, "spid_prosegui")

        step = "notifica_push"
        print(f"[LOGIN][{method}] Cerco link notifica (può non esserci)...")
        try:
            await _click_with_fallback(
                page,
                "Utilizza il le notifiche Ricevi una notifica sull'app MySielteID",
                'a.link-sso:has(img[alt="Utilizza il le notifiche"]):has(p:text("Ricevi una notifica sull\'app MySielteID"))',
                method,
                "clic su link notifica",
                timeout=4000,
                role="link"
            )
            print(f"[LOGIN][{method}] Link notifica cliccato (trovato).")
        except PlaywrightTimeoutError:
            print(f"[LOGIN][{method}] Nessun link notifica trovato, continuo comunque.")
        await logger.log(page, "spid_notifica_push")

        step = "wait_mobile_authorization"
        print(f"[LOGIN][{method}] Attendo conferma autorizzazione da mobile...")
        await logger.log(page, "spid_wait_mobile_authorization")

        step = "autorizza"
        print(f"[LOGIN][{method}] Clicco 'Autorizza'...")
        await _click_with_fallback(
            page,
            re.compile("Autorizza", re.IGNORECASE),
            "button:has-text('Autorizza'), input[type='submit'][value='Autorizza'], input[type='button'][value='Autorizza']",
            method,
            "Clic su 'Autorizza'",
            timeout=120000,
            role="button"
        )
        await logger.log(page, "spid_autorizza")
    except Exception:
        await logger.log(page, f"ERRORE_spid_{step}")
        raise


async def login_cie(page: Page, logger: PageLogger):
    method = "CIE"
    ade_username, ade_password = _get_and_validate_credentials(method)
    step = "init"

    try:
        step = "goto_login"
        await _navigate_to_login(page, method)
        await logger.log(page, "cie_goto_login")

        step = "tab_cie"
        print(f"[LOGIN][{method}] Seleziono tab CIE...")
        await _click_with_fallback(
            page,
            "CIE",
            "a[role='tab'][aria-controls='tab-2']",
            method,
            "Selezione tab CIE",
            timeout=15000,
            role="tab"
        )
        await logger.log(page, "cie_tab_cie")

        step = "entra_con_cie"
        print(f"[LOGIN][{method}] Clicco 'Entra con CIE'...")
        await _click_with_fallback(
            page,
            re.compile("Entra con CIE", re.IGNORECASE),
            "a:has-text('Entra con CIE'), button:has-text('Entra con CIE')",
            method,
            "Clic su 'Entra con CIE'",
            timeout=20000
        )
        await logger.log(page, "cie_entra_con_cie")

        step = "username"
        print(f"[LOGIN][{method}] Inserisco username...")
        await page.locator("input#username[name='username']").fill(ade_username)
        await logger.log(page, "cie_username")

        step = "password"
        print(f"[LOGIN][{method}] Inserisco password...")
        await page.locator("input#password[name='password']").fill(ade_password)
        await logger.log(page, "cie_password")

        step = "procedi"
        print(f"[LOGIN][{method}] Clicco 'Procedi'...")
        await page.locator("button[type='submit']").first.click()
        await logger.log(page, "cie_procedi")

        step = "wait_mobile_authorization"
        print(f"[LOGIN][{method}] Attendo conferma autorizzazione da mobile...")
        await logger.log(page, "cie_wait_mobile_authorization")

        step = "prosegui_after_authorization"
        print(f"[LOGIN][{method}] Clicco 'Prosegui' dopo autorizzazione...")
        await _click_with_fallback(
            page,
            re.compile("Prosegui", re.IGNORECASE),
            "button[type='submit'][name='_eventId_proceed'], button:has-text('Prosegui'), input[type='submit'][value='Prosegui']",
            method,
            "Clic su 'Prosegui' dopo autorizzazione",
            timeout=120000,
            role="button"
        )
        await logger.log(page, "cie_prosegui_after_authorization")
    except Exception:
        await logger.log(page, f"ERRORE_cie_{step}")
        raise

async def _open_sister_after_auth(page: Page, logger: PageLogger):
    print("[LOGIN] Cerco servizio SISTER...")
    await page.get_by_role("textbox", name="Cerca il servizio").click()
    await page.get_by_role("textbox", name="Cerca il servizio").fill("SISTER")
    await page.get_by_role("textbox", name="Cerca il servizio").press("Enter")
    await logger.log(page, "postauth_cerca_sister")

    print("[LOGIN] Clicco 'Vai al servizio'...")
    await page.get_by_role("link", name="Vai al servizio").first.click()
    await logger.log(page, "postauth_vai_al_servizio")

    print("[LOGIN] Attendo caricamento pagina...")
    await page.wait_for_load_state("networkidle")
    await logger.log(page, "postauth_networkidle")

    print("[LOGIN] Controllo blocco sessione...")
    content = await page.content()
    url = page.url
    if (
        "Utente gia' in sessione" in content
        or "error_locked.jsp" in url
    ):
        print("[LOGIN][ERRORE] Utente già in sessione su un'altra postazione!")
        raise Exception("Utente già in sessione su un'altra postazione")
    await logger.log(page, "postauth_sessione_ok")

    print("[LOGIN] Clicco 'Conferma'...")
    await page.get_by_role("button", name="Conferma").click()
    await logger.log(page, "postauth_conferma")

    print("[LOGIN] Clicco 'Consultazioni e Certificazioni'...")
    await page.get_by_role("link", name="Consultazioni e Certificazioni").click()
    await logger.log(page, "postauth_consultazioni")

    print("[LOGIN] Clicco 'Visure catastali'...")
    await page.get_by_role("link", name="Visure catastali").click()
    await logger.log(page, "postauth_visure_catastali")

    print("[LOGIN] Clicco 'Conferma Lettura'...")
    await page.get_by_role("link", name="Conferma Lettura").click()
    await logger.log(page, "postauth_conferma_lettura")
