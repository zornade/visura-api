import asyncio
from main import BrowserManager
import logging

logging.basicConfig(level=logging.INFO)

async def test():
    manager = BrowserManager()
    await manager.initialize()
    print("Initialize success")
    
    # Simulate a crash
    await manager.context.close()
    
    # Try to login, this should trigger auto-recovery and succeed
    await manager.login()
    print("Login success after recovery")
    await manager.browser.close()
    await manager.playwright.stop()

if __name__ == "__main__":
    asyncio.run(test())
