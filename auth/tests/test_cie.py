import asyncio
import os
import sys

from playwright.async_api import async_playwright

# Aggiungi la root del progetto al path per importare utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from utils import login


async def main():
    # Forza il provider a CIE per questo test
    os.environ["AUTH_PROVIDER"] = "CIE"
    print("[TEST_CIE] Avvio test manuale con provider CIE")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await login(page)
            print("[TEST_CIE] Autenticazione CIE completata con successo, siamo nell'area utente.")

            # Verifica E2E: cerchiamo un elemento interno
            await page.wait_for_selector("text='Consultazioni e Certificazioni'", timeout=10000)
            print("[TEST_CIE] Verifica E2E superata: link Consultazioni trovato.")

            # Tieni aperto il browser per verifica manuale
            print("[TEST_CIE] Browser in pausa. Premi Ctrl+C per terminare.")
            await asyncio.sleep(60)

        except Exception as e:
            print(f"[TEST_CIE] Errore durante il test CIE: {e}")
        finally:
            print("[TEST_CIE] Chiusura browser...")
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
