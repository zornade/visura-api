import asyncio
import logging
import sys
import json
import os
from datetime import datetime
from typing import Optional, Dict
from dataclasses import dataclass
import time
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from contextlib import asynccontextmanager
from utils import PageLogger, login, run_visura, logout, extract_all_sezioni, run_visura_immobile
from pydantic import BaseModel, Field, validator

# Carica variabili d'ambiente da .env
load_dotenv()

# Configurazione logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()

# Create logs directory if it doesn't exist and we have permission
log_handlers = [logging.StreamHandler()]
try:
    if not os.path.exists('./logs'):
        os.makedirs('./logs', exist_ok=True)
    log_handlers.append(logging.FileHandler('./logs/visura.log'))
except (PermissionError, OSError) as e:
    print(f"Warning: Cannot create log file: {e}")

logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=log_handlers
)
logger = logging.getLogger(__name__)

# Custom Exception Classes
class VisuraError(Exception):
    """Base exception for visura-related errors"""
    pass

class AuthenticationError(VisuraError):
    """Raised when authentication fails"""
    pass

class BrowserError(VisuraError):
    """Raised when browser operations fail"""
    pass

class ValidationError(VisuraError):
    """Raised when input validation fails"""
    pass

@dataclass
class VisuraRequest:
    request_id: str
    tipo_catasto: str
    provincia: str
    comune: str
    foglio: str
    particella: str
    sezione: Optional[str] = None
    subalterno: Optional[str] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

@dataclass
class VisuraIntestatiRequest:
    """Richiesta per ottenere gli intestati di un immobile specifico"""
    request_id: str
    tipo_catasto: str
    provincia: str
    comune: str
    foglio: str
    particella: str
    subalterno: Optional[str] = None
    sezione: Optional[str] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

@dataclass
class VisuraResponse:
    request_id: str
    success: bool
    tipo_catasto: str
    data: Optional[Dict] = None
    error: Optional[str] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

class BrowserManager:
    def __init__(self):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.auth_page: Optional[Page] = None
        self.authenticated = False
        self.keep_alive_running = False
        self.last_login_time = None

    async def initialize(self):
        """Inizializza il browser e il contexto"""
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                handle_sigint=False,   # Non chiudere Chromium su Ctrl+C — gestiamo noi il logout
                handle_sigterm=False,  # Idem per SIGTERM
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--no-first-run',
                    '--no-zygote',
                    '--single-process',
                    '--disable-extensions'
                ]
            )
            
            self.context = await self.browser.new_context()
            logger.info("Browser inizializzato")
        except Exception as e:
            logger.error(f"Failed to initialize browser: {e}")
            raise BrowserError(f"Browser initialization failed: {e}") from e
        
    async def login(self):
        """Esegue il login nella prima tab"""
        try:
            # Chiudi la vecchia pagina prima di crearne una nuova
            if self.auth_page and getattr(self.auth_page, 'is_closed', lambda: True)() is False:
                try:
                    await self.auth_page.close()
                    logger.info("Vecchia pagina di autenticazione chiusa")
                except Exception as e:
                    logger.warning(f"Errore chiudendo vecchia pagina: {e}")
            
            for attempt in range(2):
                try:
                    page = await self.context.new_page()
                    await login(page)
                    break
                except Exception as e:
                    if ("Target page, context or browser has been closed" in str(e) or 
                        "Browser closed" in str(e) or "Target closed" in str(e)) and attempt == 0:
                        logger.warning(f"Contesto chiuso ({e}), reinizializzazione in corso... (tentativo {attempt+1})")
                        await self.initialize()
                    else:
                        raise
            self.auth_page = page
            self.authenticated = True
            self.last_login_time = datetime.now()
            logger.info("Login completato con successo")
        except Exception as e:
            logger.error(f"Errore durante il login: {e}")
            self.authenticated = False
            raise AuthenticationError(f"Login failed: {e}") from e

    async def start_keep_alive(self):
        """Mantiene la sessione attiva con attività realistiche"""
        self.keep_alive_running = True
        
        async def keep_alive_worker():
            last_check = datetime.now()
            while self.keep_alive_running:
                try:
                    if self.auth_page and not self.auth_page.is_closed():
                        current_time = datetime.now()
                        
                        # Ogni 5 minuti, fai una verifica più approfondita
                        if (current_time - last_check).total_seconds() > 300:
                            await self._perform_session_refresh()
                            last_check = current_time
                        else:
                            # Keep-alive leggero ogni 30 secondi
                            await self._perform_light_keepalive()
                    
                    await asyncio.sleep(30)
                    
                except Exception as e:
                    logger.error(f"Errore in keep-alive: {e}")
                    await asyncio.sleep(60)
        
        asyncio.create_task(keep_alive_worker())
    
    async def _perform_light_keepalive(self):
        """Keep-alive leggero: movimento del mouse"""
        try:
            await self.auth_page.mouse.move(100, 100)
            await asyncio.sleep(0.1)
            await self.auth_page.mouse.move(200, 200)
            logger.debug("Keep-alive movimento mouse eseguito")
        except Exception as e:
            logger.warning(f"Errore in light keep-alive: {e}")
    
    async def _perform_session_refresh(self):
        """Refresh approfondito della sessione navigando alla pagina di scelta servizio"""
        try:
            logger.info("Eseguendo refresh della sessione...")
            
            await self.auth_page.goto("https://sister3.agenziaentrate.gov.it/Visure/SceltaServizio.do?tipo=/T/TM/VCVC_", timeout=30000)
            await self.auth_page.wait_for_load_state("networkidle", timeout=15000)
            
            try:
                provincia_options = await self.auth_page.locator("select[name='listacom'] option").count()
                if provincia_options <= 1:
                    logger.warning("Sessione scaduta durante refresh - province non disponibili")
                    self.authenticated = False
                    return False
                else:
                    logger.info(f"Session refresh completato - {provincia_options-1} province disponibili")
                    return True
            except Exception as e:
                logger.warning(f"Errore nel verificare province: {e}")
                self.authenticated = False
                return False
                
        except Exception as e:
            logger.error(f"Errore in session refresh: {e}")
            self.authenticated = False
            return False
    
    async def stop_keep_alive(self):
        """Ferma il keep-alive"""
        self.keep_alive_running = False
    
    async def _check_session_validity(self):
        """Verifica se la sessione è ancora valida"""
        try:
            if not self.auth_page or self.auth_page.is_closed():
                logger.warning("Pagina di autenticazione non disponibile")
                return False
            
            current_url = self.auth_page.url
            if "agenziaentrate.gov.it" not in current_url or "sister" not in current_url:
                logger.warning(f"Non siamo più nel portale SISTER - URL: {current_url}")
                return False
            
            if "SceltaServizio.do" not in current_url:
                await self.auth_page.goto("https://sister3.agenziaentrate.gov.it/Visure/SceltaServizio.do?tipo=/T/TM/VCVC_", timeout=30000)
                await self.auth_page.wait_for_load_state("networkidle", timeout=15000)
            
            provincia_options = await self.auth_page.locator("select[name='listacom'] option").count()
            if provincia_options <= 1:
                logger.warning("Province non disponibili - sessione probabilmente scaduta")
                return False
                
            logger.info(f"Sessione valida - {provincia_options-1} province disponibili")
            return True
            
        except Exception as e:
            logger.error(f"Errore nella verifica della sessione: {e}")
            return False
    
    async def _try_session_recovery(self) -> bool:
        """Tenta di recuperare la sessione SISTER senza rifare il login SPID.
        Naviga direttamente alla pagina di scelta servizio e verifica se è ancora valida."""
        try:
            if not self.auth_page or self.auth_page.is_closed():
                return False
            
            recovery_logger = PageLogger("recovery")
            logger.info("Tentativo di recupero sessione SISTER senza SPID...")
            
            # Prova a navigare direttamente alla pagina Visure
            await self.auth_page.goto(
                "https://sister3.agenziaentrate.gov.it/Visure/SceltaServizio.do?tipo=/T/TM/VCVC_",
                timeout=30000
            )
            await self.auth_page.wait_for_load_state("networkidle", timeout=15000)
            await recovery_logger.log(self.auth_page, "goto_scelta_servizio")
            
            current_url = self.auth_page.url
            content = await self.auth_page.content()
            
            # Se siamo stati reindirizzati al login → sessione scaduta davvero
            if "iampe.agenziaentrate.gov.it" in current_url or "Login" in current_url:
                logger.info("Sessione SISTER scaduta, serve login SPID completo")
                return False
            
            # Se c'è errore di sessione bloccata
            if "Utente gia' in sessione" in content or "error_locked.jsp" in current_url:
                logger.warning("Utente già in sessione, serve login SPID completo")
                return False
            
            # Verifica che ci siano le province (segno che la sessione funziona)
            provincia_options = await self.auth_page.locator("select[name='listacom'] option").count()
            if provincia_options > 1:
                logger.info(f"Sessione SISTER recuperata! {provincia_options-1} province disponibili")
                self.authenticated = True
                self.last_login_time = datetime.now()
                return True
            
            # Se la pagina è quella giusta ma senza province, proviamo il percorso completo
            if "agenziaentrate.gov.it" in current_url and "sister" in current_url:
                try:
                    await self.auth_page.get_by_role("button", name="Conferma").click(timeout=5000)
                    await recovery_logger.log(self.auth_page, "conferma")
                    await self.auth_page.get_by_role("link", name="Consultazioni e Certificazioni").click(timeout=5000)
                    await recovery_logger.log(self.auth_page, "consultazioni")
                    await self.auth_page.get_by_role("link", name="Visure catastali").click(timeout=5000)
                    await recovery_logger.log(self.auth_page, "visure_catastali")
                    await self.auth_page.get_by_role("link", name="Conferma Lettura").click(timeout=5000)
                    await recovery_logger.log(self.auth_page, "conferma_lettura")
                    
                    logger.info("Sessione SISTER recuperata tramite navigazione interna")
                    self.authenticated = True
                    self.last_login_time = datetime.now()
                    return True
                except Exception as e:
                    logger.warning(f"Navigazione interna fallita: {e}")
                    await recovery_logger.log(self.auth_page, "navigazione_fallita")
                    return False
            
            await recovery_logger.log(self.auth_page, "stato_sconosciuto")
            return False
            
        except Exception as e:
            logger.warning(f"Recupero sessione fallito: {e}")
            return False

    async def _ensure_authenticated(self):
        """Assicura che il sistema sia autenticato, ri-autentica se necessario.
        Prima tenta il recupero sessione senza SPID, poi fallback a login completo."""
        if not self.authenticated or not await self._check_session_validity():
            # Step 1: tenta recupero sessione senza SPID
            if await self._try_session_recovery():
                logger.info("Sessione recuperata senza login SPID")
                return
            
            # Step 2: fallback a login SPID completo
            logger.info("Sessione non recuperabile, login SPID completo...")
            try:
                await self.login()
                await self.start_keep_alive()
                logger.info("Re-autenticazione SPID completata")
            except Exception as e:
                logger.error(f"Errore nella re-autenticazione: {e}")
                raise AuthenticationError(f"Re-authentication failed: {e}") from e

    async def esegui_visura(self, request: VisuraRequest) -> VisuraResponse:
        """Esegue una visura catastale"""
        try:
            await self._ensure_authenticated()
            
            try:
                # Per i terreni estraiamo sempre gli intestati, per i fabbricati no
                extract_intestati = request.tipo_catasto == 'T'
                
                result = await run_visura(
                    self.auth_page,
                    request.provincia,
                    request.comune,
                    request.sezione,
                    request.foglio,
                    request.particella,
                    request.tipo_catasto,
                    extract_intestati
                )
            except Exception as e:
                raise BrowserError(f"Failed to execute visura: {e}") from e
            
            logger.info(f"Visura completata per request {request.request_id}")
            return VisuraResponse(
                request_id=request.request_id,
                success=True,
                tipo_catasto=request.tipo_catasto,
                data=result,
            )
            
        except (AuthenticationError, BrowserError) as e:
            logger.error(f"Errore in visura {request.request_id}: {e}")
            return VisuraResponse(
                request_id=request.request_id,
                success=False,
                tipo_catasto=request.tipo_catasto,
                error=str(e),
            )
        except Exception as e:
            logger.error(f"Errore inatteso in visura {request.request_id}: {e}")
            return VisuraResponse(
                request_id=request.request_id,
                success=False,
                tipo_catasto=request.tipo_catasto,
                error=f"Errore inatteso: {str(e)}",
            )

    async def esegui_visura_intestati(self, request: VisuraIntestatiRequest) -> VisuraResponse:
        """Esegue una visura per ottenere gli intestati di un immobile specifico."""
        try:
            await self._ensure_authenticated()
            
            if request.tipo_catasto == 'F' and request.subalterno:
                result = await run_visura_immobile(
                    self.auth_page,
                    provincia=request.provincia,
                    comune=request.comune,
                    sezione=request.sezione,
                    foglio=request.foglio,
                    particella=request.particella,
                    subalterno=request.subalterno
                )
            else:
                result = await run_visura(
                    self.auth_page,
                    request.provincia,
                    request.comune,
                    request.sezione,
                    request.foglio,
                    request.particella,
                    request.tipo_catasto,
                    extract_intestati=True
                )
            
            logger.info(f"Visura intestati completata per {request.request_id}")
            return VisuraResponse(
                request_id=request.request_id,
                success=True,
                tipo_catasto=request.tipo_catasto,
                data=result,
            )
            
        except Exception as e:
            logger.error(f"Errore in visura intestati {request.request_id}: {e}")
            return VisuraResponse(
                request_id=request.request_id,
                success=False,
                tipo_catasto=request.tipo_catasto,
                error=str(e),
            )

    async def restart_browser_if_needed(self):
        """Riavvia il browser se necessario"""
        try:
            if self.browser and not self.browser.is_connected():
                logger.info("Browser disconnesso, riavviando...")
                await self.close()
                await self.initialize()
                await self.login()
                await self.start_keep_alive()
                logger.info("Browser riavviato con successo")
        except Exception as e:
            logger.error(f"Errore nel riavvio browser: {e}")
            raise BrowserError(f"Failed to restart browser: {e}") from e

    async def close(self):
        """Chiude il browser e torna sempre al portale"""
        await self.stop_keep_alive()
        try:
            if self.auth_page and not self.auth_page.is_closed():
                try:
                    await self.auth_page.get_by_role("link", name=" Torna al portale").click()
                except Exception as e:
                    logger.warning(f"Impossibile cliccare 'Torna al portale': {e}")
        except Exception as e:
            logger.warning(f"Errore durante il tentativo di tornare al portale: {e}")
        try:
            if self.context:
                await self.context.close()
        except Exception as e:
            logger.warning(f"Errore durante la chiusura del context: {e}")
        try:
            if self.browser:
                await self.browser.close()
        except Exception as e:
            logger.warning(f"Errore durante la chiusura del browser: {e}")
        logger.info("Browser chiuso")

    async def graceful_shutdown(self):
        """Effettua uno shutdown graceful con logout"""
        logger.info("Iniziando shutdown graceful...")
        
        try:
            if self.auth_page and not self.auth_page.is_closed():
                logger.info("Effettuando logout dalla sessione...")
                await logout(self.auth_page)
        except Exception as e:
            logger.warning(f"Errore durante il logout: {e}")
        
        await self.close()
        logger.info("Shutdown graceful completato")

class VisuraService:
    def __init__(self):
        self.browser_manager = BrowserManager()
        self.request_queue = asyncio.Queue()
        self.response_store: Dict[str, VisuraResponse] = {}
        self.processing = False
        
    async def initialize(self):
        """Inizializza il servizio"""
        await self.browser_manager.initialize()
        await self.browser_manager.login()
        await self.browser_manager.start_keep_alive()
        
        # Avvia il worker per processare le richieste
        asyncio.create_task(self._process_requests())
        
    async def _process_requests(self):
        """Processa le richieste in coda"""
        self.processing = True
        
        while self.processing:
            try:
                request_data = await self.request_queue.get()
                request = request_data['request']
                
                if isinstance(request, VisuraRequest):
                    response = await self.browser_manager.esegui_visura(request)
                    self.response_store[request.request_id] = response
                    logger.info(f"Processata richiesta visura {request.request_id}")
                
                elif isinstance(request, VisuraIntestatiRequest):
                    response = await self.browser_manager.esegui_visura_intestati(request)
                    self.response_store[request.request_id] = response
                    logger.info(f"Processata richiesta intestati {request.request_id}")
                
                else:
                    logger.error(f"Tipo di richiesta sconosciuto: {type(request)}")
                
                self.request_queue.task_done()
                
                # Pausa tra le richieste per non sovraccaricare SISTER
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Errore nel processare richieste: {e}")
                await asyncio.sleep(5)
    
    async def add_request(self, request: VisuraRequest) -> str:
        """Aggiunge una richiesta alla coda"""
        await self.request_queue.put({'request': request})
        logger.info(f"Richiesta visura {request.request_id} aggiunta alla coda (posizione: {self.request_queue.qsize()})")
        return request.request_id
    
    async def add_intestati_request(self, request: VisuraIntestatiRequest) -> str:
        """Aggiunge una richiesta intestati alla coda"""
        await self.request_queue.put({'request': request})
        logger.info(f"Richiesta intestati {request.request_id} aggiunta alla coda (posizione: {self.request_queue.qsize()})")
        return request.request_id
    
    async def get_response(self, request_id: str) -> Optional[VisuraResponse]:
        """Ottiene la risposta per un request_id"""
        return self.response_store.get(request_id)
    
    async def shutdown(self):
        """Chiude il servizio"""
        self.processing = False
        await self.browser_manager.close()
    
    async def graceful_shutdown(self):
        """Chiude il servizio con logout graceful"""
        logger.info("Iniziando graceful shutdown del servizio...")
        self.processing = False
        await self.browser_manager.graceful_shutdown()
        logger.info("Graceful shutdown del servizio completato")

# Global service instance - initialized during lifespan
visura_service: Optional[VisuraService] = None

def get_visura_service() -> VisuraService:
    """Dependency to get the visura service"""
    if visura_service is None:
        raise HTTPException(status_code=503, detail="Servizio non inizializzato")
    return visura_service

# Signal handler per shutdown graceful
# Nota: NON usiamo signal handler custom perché sys.exit() uccide il processo
# prima che il logout async possa completare. Uvicorn gestisce già SIGINT/SIGTERM
# e passa per il lifespan shutdown dove il logout viene eseguito correttamente.

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global visura_service
    PageLogger.reset_session()  # Nuova sessione di log per ogni avvio
    visura_service = VisuraService()
    await visura_service.initialize()
    logger.info("Servizio visure avviato")
    yield
    # Shutdown — uvicorn arriva qui dopo SIGINT/SIGTERM
    logger.info("Shutdown in corso, eseguendo logout...")
    if visura_service:
        await visura_service.graceful_shutdown()
    logger.info("Servizio visure fermato con graceful shutdown")

# API FastAPI
app = FastAPI(title="Servizio Visure Catastali", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Modelli di richiesta
# ---------------------------------------------------------------------------

class VisuraInput(BaseModel):
    """Richiesta per una visura catastale"""
    provincia: str = Field(..., min_length=1, description="Nome della provincia")
    comune: str = Field(..., min_length=1, description="Nome del comune")
    foglio: str = Field(..., min_length=1, description="Numero di foglio")
    particella: str = Field(..., min_length=1, description="Numero di particella")
    sezione: Optional[str] = Field(None, description="Sezione (opzionale)")
    tipo_catasto: Optional[str] = Field(None, pattern=r"^[TF]$", description="'T' = Terreni, 'F' = Fabbricati (se omesso esegue entrambi)")
    
    @validator('tipo_catasto')
    def validate_tipo_catasto(cls, v):
        if v is not None and v not in ['T', 'F']:
            raise ValidationError(f"tipo_catasto deve essere 'T' o 'F', ricevuto {v}")
        return v

class VisuraIntestatiInput(BaseModel):
    """Richiesta per ottenere gli intestati di un immobile specifico"""
    provincia: str = Field(..., min_length=1, description="Nome della provincia")
    comune: str = Field(..., min_length=1, description="Nome del comune")
    foglio: str = Field(..., min_length=1, description="Numero di foglio")
    particella: str = Field(..., min_length=1, description="Numero di particella")
    tipo_catasto: str = Field(..., pattern=r"^[TF]$", description="'T' = Terreni, 'F' = Fabbricati")
    subalterno: Optional[str] = Field(None, description="Numero di subalterno (obbligatorio per Fabbricati)")
    sezione: Optional[str] = Field(None, description="Sezione (opzionale)")
    
    @validator('tipo_catasto')
    def validate_tipo_catasto(cls, v):
        if v not in ['T', 'F']:
            raise ValidationError(f"tipo_catasto deve essere 'T' o 'F', ricevuto {v}")
        return v
    
    @validator('subalterno')
    def validate_subalterno(cls, v, values):
        tipo_catasto = values.get('tipo_catasto')
        if tipo_catasto == 'F' and not v:
            raise ValidationError("subalterno è obbligatorio per i fabbricati (tipo_catasto='F')")
        if tipo_catasto == 'T' and v:
            raise ValidationError("subalterno non va indicato per i terreni (tipo_catasto='T')")
        return v

class SezioniExtractionRequest(BaseModel):
    """Richiesta per l'estrazione delle sezioni territoriali"""
    tipo_catasto: str = Field("T", pattern=r"^[TF]$", description="'T' = Terreni, 'F' = Fabbricati")
    max_province: int = Field(200, ge=1, le=200, description="Numero massimo di province da processare (default: tutte)")

# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@app.post("/visura")
async def richiedi_visura(
    request: VisuraInput, 
    service: VisuraService = Depends(get_visura_service)
):
    """Richiede una visura catastale fornendo direttamente i dati catastali"""
    try:
        sezione = None if request.sezione == "_" else request.sezione
        
        tipos_catasto = [request.tipo_catasto] if request.tipo_catasto else ["T", "F"]
        request_ids = []
        
        for tipo_catasto in tipos_catasto:
            request_id = f"req_{tipo_catasto}_{int(time.time() * 1000)}"
            visura_req = VisuraRequest(
                request_id=request_id,
                tipo_catasto=tipo_catasto,
                provincia=request.provincia,
                comune=request.comune,
                sezione=sezione,
                foglio=request.foglio,
                particella=request.particella
            )
            await service.add_request(visura_req)
            request_ids.append(request_id)
        
        return JSONResponse({
            "request_ids": request_ids,
            "tipos_catasto": tipos_catasto,
            "status": "queued",
            "message": f"Richieste aggiunte alla coda per {request.comune} F.{request.foglio} P.{request.particella}"
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore nella richiesta visura: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/visura/{request_id}")
async def ottieni_visura(
    request_id: str, 
    service: VisuraService = Depends(get_visura_service)
):
    """Ottiene il risultato di una visura"""
    try:
        response = await service.get_response(request_id)
        
        if response is None:
            return JSONResponse({
                "request_id": request_id,
                "status": "processing",
                "message": "Richiesta in elaborazione"
            })
        
        return JSONResponse({
            "request_id": request_id,
            "tipo_catasto": response.tipo_catasto,
            "status": "completed" if response.success else "error",
            "data": response.data,
            "error": response.error,
            "timestamp": response.timestamp.isoformat()
        })
        
    except Exception as e:
        logger.error(f"Errore nell'ottenere visura: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/visura/intestati")
async def richiedi_intestati_immobile(
    request: VisuraIntestatiInput,
    service: VisuraService = Depends(get_visura_service)
):
    """Richiede gli intestati per un immobile specifico."""
    try:
        sezione = None if request.sezione == "_" else request.sezione
        
        request_id = f"intestati_{request.tipo_catasto}_{request.subalterno or 'none'}_{int(time.time() * 1000)}"
        
        intestati_request = VisuraIntestatiRequest(
            request_id=request_id,
            tipo_catasto=request.tipo_catasto,
            provincia=request.provincia,
            comune=request.comune,
            foglio=request.foglio,
            particella=request.particella,
            subalterno=request.subalterno,
            sezione=sezione
        )
        
        await service.add_intestati_request(intestati_request)
        
        return JSONResponse({
            "request_id": request_id,
            "tipo_catasto": request.tipo_catasto,
            "subalterno": request.subalterno,
            "status": "queued",
            "message": f"Richiesta intestati aggiunta alla coda per {request.comune} F.{request.foglio} P.{request.particella}",
            "queue_position": service.request_queue.qsize()
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore nella richiesta intestati: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check(service: VisuraService = Depends(get_visura_service)):
    """Controlla lo stato del servizio"""
    return JSONResponse({
        "status": "healthy",
        "authenticated": service.browser_manager.authenticated,
        "queue_size": service.request_queue.qsize()
    })

@app.post("/shutdown")
async def graceful_shutdown_endpoint(service: VisuraService = Depends(get_visura_service)):
    """Effettua uno shutdown graceful del servizio"""
    try:
        logger.info("Shutdown graceful richiesto via API")
        await service.graceful_shutdown()
        return JSONResponse({
            "status": "success",
            "message": "Shutdown graceful completato"
        })
    except Exception as e:
        logger.error(f"Errore durante shutdown graceful via API: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/sezioni/extract")
async def extract_sezioni(
    request: SezioniExtractionRequest,
    service: VisuraService = Depends(get_visura_service)
):
    """
    Estrae le sezioni territoriali d'Italia per il tipo catasto specificato.
    ATTENZIONE: Questa operazione può richiedere diverse ore!
    I dati vengono restituiti nella risposta.
    """
    try:
        logger.info(f"Iniziando estrazione sezioni per tipo catasto: {request.tipo_catasto}, max province: {request.max_province}")
        
        if not service.browser_manager.authenticated or not service.browser_manager.auth_page:
            raise HTTPException(status_code=503, detail="Servizio non autenticato")
        
        sezioni_data = await extract_all_sezioni(service.browser_manager.auth_page, request.tipo_catasto, request.max_province)
        
        if not sezioni_data:
            return JSONResponse({
                "status": "no_data",
                "message": "Nessuna sezione estratta",
                "count": 0
            })
        
        logger.info(f"Estrazione sezioni completata: {len(sezioni_data)} totali")
        
        return JSONResponse({
            "status": "success",
            "message": f"Estrazione completata per tipo catasto {request.tipo_catasto}",
            "total_extracted": len(sezioni_data),
            "tipo_catasto": request.tipo_catasto,
            "sezioni": sezioni_data
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore durante estrazione sezioni: {e}")
        raise HTTPException(status_code=500, detail=str(e))
