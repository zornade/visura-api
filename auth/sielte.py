from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .base import BaseAuthProvider


class SielteAuthProvider(BaseAuthProvider):
    """
    Authentication provider for SPID Sielte.
    Uses the legacy Sielte connection flow.
    """

    async def login(self, page: Page, username: str, password: str, page_logger=None) -> None:
        print("[LOGIN_SIELTE] Navigo alla pagina di login...")
        await page.goto("https://iampe.agenziaentrate.gov.it/sam/UI/Login?realm=/agenziaentrate")
        if page_logger:
            await page_logger.log(page, "goto_login")
        print("[LOGIN_SIELTE] Clicco 'SPID'...")

        # Click the SPID tab - The actual ID or role might differ, assuming 'SPID' for now
        # based on Sielte being a SPID provider
        spid_tab = page.get_by_role("tab", name="SPID")
        if await spid_tab.count() > 0:
            await spid_tab.click()
            if page_logger:
                await page_logger.log(page, "tab_spid")

        print("[LOGIN_SIELTE] Seleziono Sielte...")
        # Placeholder for Sielte selection if there's a SPID provider list
        sielte_btn = page.get_by_role("link", name="Sielte")
        if await sielte_btn.count() > 0:
            await sielte_btn.click()
            if page_logger:
                await page_logger.log(page, "sielte_selected")

        print("[LOGIN_SIELTE] Attendo redirect al provider Sielte...")
        await page.wait_for_selector("#username", timeout=15000)

        print("[LOGIN_SIELTE] Inserisco username...")
        await page.locator("#username").fill(username)
        if page_logger:
            await page_logger.log(page, "username_filled")

        print("[LOGIN_SIELTE] Inserisco password...")
        await page.locator("#password").fill(password)
        if page_logger:
            await page_logger.log(page, "password_filled")

        print("[LOGIN_SIELTE] Clicco 'Procedi' / 'Invia'...")
        # The button name might be "Accesso", "Procedi", "Invia" on Sielte
        submit_btn = page.get_by_role("button", name="Procedi")
        if await submit_btn.count() == 0:
            submit_btn = page.locator("button[type='submit']")

        if page_logger:
            await page_logger.log(page, "before_submit")
        await submit_btn.click()
        if page_logger:
            await page_logger.log(page, "after_submit")

        print("[LOGIN_SIELTE] Cerco link notifica Sielte...")
        try:
            # Fallback: cerca <a> con alt="Utilizza le notifiche" e <p> con testo 'Ricevi una notifica sull'app MySielteID'
            sielte_notif_link = page.locator(
                'a.link-sso:has(img[alt*="notifiche"]):has(p:text("Ricevi una notifica sull\'app MySielteID"))'
            )
            if await sielte_notif_link.count() > 0:
                await sielte_notif_link.click(timeout=4000)
                print("[LOGIN_SIELTE] Cliccato link notifica Sielte.")
            else:
                # Altro bottone generico Sielte
                await page.locator("text='Autorizza con l\\'App'").click(timeout=4000)
        except PlaywrightTimeoutError:
            print("[LOGIN_SIELTE] Nessun link notifica trovato o già autorizzato.")

        if page_logger:
            await page_logger.log(page, "after_notification_step")

        print("[LOGIN_SIELTE] Clicco 'Autorizza' se presente...")
        autorizza_btn = page.get_by_role("button", name="Autorizza")
        if await autorizza_btn.count() > 0:
            await autorizza_btn.click()

        # Login completato, ora il controllo passa a utils.py per la navigazione SISTER
        print("[LOGIN_SIELTE] Autenticazione SIELTE completata con successo.")
        if page_logger:
            await page_logger.log(page, "sielte_auth_success")
