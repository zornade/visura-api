import asyncio
import os
import sys

from playwright.async_api import async_playwright

# Aggiungi la root del progetto al path per importare utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from utils import PageLogger


async def main():
    print("[TEST_PAGELOGGER] Avvio verifica PageLogger...")

    # Inizializza sessione
    PageLogger.reset_session()
    logger = PageLogger("test_flow")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            print("[TEST_PAGELOGGER] Navigo su Google...")
            await page.goto("https://www.google.com")
            await logger.log(page, "google_home")

            print("[TEST_PAGELOGGER] Navigo su Wikipedia...")
            await page.goto("https://it.wikipedia.org")
            await logger.log(page, "wikipedia_home")

            print("\n[TEST_PAGELOGGER] Controllo file generati...")
            log_dir = os.path.join("logs/pages", PageLogger._session_id, "test_flow")

            # Attendi un momento per il flush su disco
            await asyncio.sleep(1)

            if os.path.exists(log_dir):
                files = os.listdir(log_dir)
                print(f"[TEST_PAGELOGGER] File trovati in {log_dir}:")
                for f in files:
                    print(f" - {f}")

                if len(files) >= 2:
                    print("\n[TEST_PAGELOGGER] VERIFICA COMPLETATA CON SUCCESSO!")
                else:
                    print("\n[TEST_PAGELOGGER] ERRORE: Numero di file insufficiente.")
            else:
                print(f"\n[TEST_PAGELOGGER] ERRORE: Directory {log_dir} non trovata.")

        except Exception as e:
            print(f"[TEST_PAGELOGGER] Errore durante il test: {e}")
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
