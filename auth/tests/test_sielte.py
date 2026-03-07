import asyncio
import os
import sys

from playwright.async_api import async_playwright

# Aggiungi la root del progetto al path per importare utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from utils import login


async def main():
    # Forza il provider a Sielte per questo test
    os.environ["AUTH_PROVIDER"] = "SIELTE"
    print("[TEST_SIELTE] Avvio test manuale con provider SIELTE (SPID)")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await login(page)
            print("[TEST_SIELTE] Autenticazione SIELTE completata con successo, siamo nell'area utente.")

            # Tieni aperto il browser per verifica manuale
            print("[TEST_SIELTE] Browser in pausa. Premi Ctrl+C per terminare.")
            await asyncio.sleep(300)

        except Exception as e:
            print(f"[TEST_SIELTE] Errore durante il test SIELTE: {e}")
        finally:
            print("[TEST_SIELTE] Chiusura browser...")
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
