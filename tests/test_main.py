import asyncio
import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError as PydanticValidationError

import main
from main import (
    SezioniExtractionRequest,
    VisuraInput,
    VisuraIntestatiInput,
    VisuraIntestatiRequest,
    VisuraRequest,
    VisuraResponse,
    VisuraService,
    extract_sezioni,
    get_visura_service,
    graceful_shutdown_endpoint,
    health_check,
    ottieni_visura,
    richiedi_intestati_immobile,
    richiedi_visura,
)


class FakeQueue:
    def __init__(self, size=0):
        self._size = size

    def qsize(self):
        return self._size


class FakeBrowserManager:
    def __init__(self, authenticated=True):
        self.authenticated = authenticated
        self.auth_page = object()


class FakeService:
    def __init__(self):
        self.request_queue = FakeQueue(0)
        self.browser_manager = FakeBrowserManager(True)
        self.added_requests = []
        self.added_intestati_requests = []
        self.responses = {}

    async def add_request(self, request):
        self.added_requests.append(request)
        self.request_queue._size += 1
        return request.request_id

    async def add_intestati_request(self, request):
        self.added_intestati_requests.append(request)
        self.request_queue._size += 1
        return request.request_id

    async def get_response(self, request_id):
        return self.responses.get(request_id)

    async def graceful_shutdown(self):
        return None


def test_richiedi_visura_enqueues_both_catasto_types_when_missing_tipo_catasto():
    service = FakeService()
    request = VisuraInput(
        provincia="Trieste",
        comune="TRIESTE",
        foglio="9",
        particella="166",
        sezione="_",
        subalterno=None,
        tipo_catasto=None,
    )

    response = asyncio.run(richiedi_visura(request, service))
    payload = json.loads(response.body)

    assert payload["status"] == "queued"
    assert payload["tipos_catasto"] == ["T", "F"]
    assert len(payload["request_ids"]) == 2
    assert len(service.added_requests) == 2
    assert all(req.sezione is None for req in service.added_requests)


def test_ottieni_visura_returns_processing_when_result_not_ready():
    service = FakeService()

    response = asyncio.run(ottieni_visura("req_1", service))
    payload = json.loads(response.body)

    assert payload == {
        "request_id": "req_1",
        "status": "processing",
        "message": "Richiesta in elaborazione",
    }


def test_ottieni_visura_returns_completed_payload_when_result_is_available():
    service = FakeService()
    service.responses["req_1"] = VisuraResponse(
        request_id="req_1",
        success=True,
        tipo_catasto="F",
        data={"immobili": []},
    )

    response = asyncio.run(ottieni_visura("req_1", service))
    payload = json.loads(response.body)

    assert payload["request_id"] == "req_1"
    assert payload["status"] == "completed"
    assert payload["tipo_catasto"] == "F"
    assert payload["data"] == {"immobili": []}
    assert payload["error"] is None


def test_richiedi_intestati_immobile_queues_request_and_returns_queue_position():
    service = FakeService()
    request = VisuraIntestatiInput(
        provincia="Trieste",
        comune="TRIESTE",
        foglio="9",
        particella="166",
        tipo_catasto="F",
        subalterno="3",
        sezione="_",
    )

    response = asyncio.run(richiedi_intestati_immobile(request, service))
    payload = json.loads(response.body)

    assert payload["status"] == "queued"
    assert payload["queue_position"] == 1
    assert len(service.added_intestati_requests) == 1
    assert service.added_intestati_requests[0].sezione is None


def test_health_check_reflects_service_state():
    service = FakeService()
    service.request_queue._size = 4
    service.browser_manager.authenticated = False

    response = asyncio.run(health_check(service))
    payload = json.loads(response.body)

    assert payload == {"status": "healthy", "authenticated": False, "queue_size": 4}


def test_visura_request_sets_timestamp_automatically():
    request = VisuraRequest(
        request_id="req_1",
        tipo_catasto="T",
        provincia="Trieste",
        comune="TRIESTE",
        foglio="9",
        particella="166",
    )

    assert request.timestamp is not None


def test_get_visura_service_raises_503_when_not_initialized():
    original_service = main.visura_service
    try:
        main.visura_service = None
        with pytest.raises(HTTPException) as exc:
            get_visura_service()
        assert exc.value.status_code == 503
    finally:
        main.visura_service = original_service


def test_visura_input_rejects_invalid_tipo_catasto():
    with pytest.raises(PydanticValidationError):
        VisuraInput(
            provincia="Trieste",
            comune="TRIESTE",
            foglio="9",
            particella="166",
            tipo_catasto="X",
        )


def test_visura_intestati_input_requires_subalterno_for_fabbricati():
    with pytest.raises(main.ValidationError):
        VisuraIntestatiInput(
            provincia="Trieste",
            comune="TRIESTE",
            foglio="9",
            particella="166",
            tipo_catasto="F",
            subalterno=None,
        )


def test_visura_intestati_input_rejects_subalterno_for_terreni():
    with pytest.raises(main.ValidationError):
        VisuraIntestatiInput(
            provincia="Trieste",
            comune="TRIESTE",
            foglio="9",
            particella="166",
            tipo_catasto="T",
            subalterno="1",
        )


def test_sezioni_extraction_request_defaults_are_applied():
    request = SezioniExtractionRequest()
    assert request.tipo_catasto == "T"
    assert request.max_province == 200


def test_richiedi_visura_single_tipo_catasto_creates_one_request():
    service = FakeService()
    request = VisuraInput(
        provincia="Trieste",
        comune="TRIESTE",
        foglio="9",
        particella="166",
        tipo_catasto="T",
    )

    response = asyncio.run(richiedi_visura(request, service))
    payload = json.loads(response.body)

    assert payload["tipos_catasto"] == ["T"]
    assert len(payload["request_ids"]) == 1
    assert len(service.added_requests) == 1


def test_ottieni_visura_returns_error_status_when_response_unsuccessful():
    service = FakeService()
    service.responses["req_1"] = VisuraResponse(
        request_id="req_1",
        success=False,
        tipo_catasto="T",
        data=None,
        error="boom",
    )

    response = asyncio.run(ottieni_visura("req_1", service))
    payload = json.loads(response.body)

    assert payload["status"] == "error"
    assert payload["error"] == "boom"


def test_richiedi_visura_wraps_unexpected_exception_as_http_500():
    class BrokenService(FakeService):
        async def add_request(self, request):
            raise RuntimeError("queue down")

    request = VisuraInput(
        provincia="Trieste",
        comune="TRIESTE",
        foglio="9",
        particella="166",
        tipo_catasto="T",
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(richiedi_visura(request, BrokenService()))
    assert exc.value.status_code == 500
    assert "queue down" not in exc.value.detail
    assert "RuntimeError" not in exc.value.detail


def test_ottieni_visura_wraps_unexpected_exception_as_http_500():
    class BrokenService(FakeService):
        async def get_response(self, request_id):
            raise RuntimeError("store unavailable")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(ottieni_visura("req_1", BrokenService()))
    assert exc.value.status_code == 500
    assert "store unavailable" not in exc.value.detail


def test_richiedi_intestati_wraps_unexpected_exception_as_http_500():
    class BrokenService(FakeService):
        async def add_intestati_request(self, request):
            raise RuntimeError("queue down")

    request = VisuraIntestatiInput(
        provincia="Trieste",
        comune="TRIESTE",
        foglio="9",
        particella="166",
        tipo_catasto="F",
        subalterno="3",
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(richiedi_intestati_immobile(request, BrokenService()))
    assert exc.value.status_code == 500
    assert "queue down" not in exc.value.detail


def test_graceful_shutdown_endpoint_success():
    service = FakeService()
    response = asyncio.run(graceful_shutdown_endpoint(service))
    payload = json.loads(response.body)

    assert payload["status"] == "success"


def test_graceful_shutdown_endpoint_failure_returns_http_500():
    class BrokenService(FakeService):
        async def graceful_shutdown(self):
            raise RuntimeError("shutdown failed")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(graceful_shutdown_endpoint(BrokenService()))
    assert exc.value.status_code == 500


def test_extract_sezioni_returns_503_when_not_authenticated():
    service = FakeService()
    service.browser_manager.authenticated = False
    request = SezioniExtractionRequest(tipo_catasto="T", max_province=1)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(extract_sezioni(request, service))
    assert exc.value.status_code == 503


def test_extract_sezioni_returns_no_data_when_empty_result(monkeypatch):
    async def fake_extract_all_sezioni(_page, _tipo_catasto, _max_province):
        return []

    monkeypatch.setattr(main, "extract_all_sezioni", fake_extract_all_sezioni)

    service = FakeService()
    request = SezioniExtractionRequest(tipo_catasto="F", max_province=1)
    response = asyncio.run(extract_sezioni(request, service))
    payload = json.loads(response.body)

    assert payload == {"status": "no_data", "message": "Nessuna sezione estratta", "count": 0}


def test_extract_sezioni_returns_success_payload(monkeypatch):
    data = [{"provincia_nome": "Trieste", "comune_nome": "TRIESTE"}]

    async def fake_extract_all_sezioni(_page, _tipo_catasto, _max_province):
        return data

    monkeypatch.setattr(main, "extract_all_sezioni", fake_extract_all_sezioni)

    service = FakeService()
    request = SezioniExtractionRequest(tipo_catasto="T", max_province=2)
    response = asyncio.run(extract_sezioni(request, service))
    payload = json.loads(response.body)

    assert payload["status"] == "success"
    assert payload["total_extracted"] == 1
    assert payload["sezioni"] == data


def test_visura_intestati_request_sets_timestamp_automatically():
    request = VisuraIntestatiRequest(
        request_id="int_1",
        tipo_catasto="F",
        provincia="Trieste",
        comune="TRIESTE",
        foglio="9",
        particella="166",
        subalterno="3",
    )

    assert request.timestamp is not None


def test_visura_response_sets_timestamp_automatically():
    response = VisuraResponse(request_id="r1", success=True, tipo_catasto="T", data={"ok": True})
    assert response.timestamp is not None


def test_visura_service_add_request_and_get_response_with_real_queue(monkeypatch):
    class DummyBrowserManager:
        async def close(self):
            return None

        async def graceful_shutdown(self):
            return None

    monkeypatch.setattr(main, "BrowserManager", DummyBrowserManager)
    service = VisuraService()

    request = VisuraRequest(
        request_id="req_10",
        tipo_catasto="T",
        provincia="Trieste",
        comune="TRIESTE",
        foglio="9",
        particella="166",
    )
    returned_id = asyncio.run(service.add_request(request))
    assert returned_id == "req_10"
    assert service.request_queue.qsize() == 1

    response = VisuraResponse(request_id="req_10", success=True, tipo_catasto="T", data={"ok": 1})
    service.response_store["req_10"] = response
    fetched = asyncio.run(service.get_response("req_10"))
    assert fetched is response


def test_visura_service_shutdown_and_graceful_shutdown_toggle_processing(monkeypatch):
    class DummyBrowserManager:
        def __init__(self):
            self.closed = False
            self.graceful = False

        async def close(self):
            self.closed = True

        async def graceful_shutdown(self):
            self.graceful = True

    monkeypatch.setattr(main, "BrowserManager", DummyBrowserManager)
    service = VisuraService()
    service.processing = True

    asyncio.run(service.shutdown())
    assert service.processing is False
    assert service.browser_manager.closed is True

    service.processing = True
    asyncio.run(service.graceful_shutdown())
    assert service.processing is False
    assert service.browser_manager.graceful is True


# ---------------------------------------------------------------------------
# Test: verify_api_key (autenticazione opzionale via header X-API-Key)
# ---------------------------------------------------------------------------


def test_verify_api_key_disabled_when_env_not_set(monkeypatch):
    """Senza API_KEY in env, la verifica passa anche con header assente."""
    monkeypatch.delenv("API_KEY", raising=False)
    result = asyncio.run(main.verify_api_key(api_key=None))
    assert result is None


def test_verify_api_key_disabled_ignores_provided_header(monkeypatch):
    """Senza API_KEY in env, qualsiasi header (incluso uno random) è accettato."""
    monkeypatch.delenv("API_KEY", raising=False)
    result = asyncio.run(main.verify_api_key(api_key="random-value"))
    assert result is None


def test_verify_api_key_rejects_missing_header_when_enabled(monkeypatch):
    """Con API_KEY in env, header assente → HTTP 403."""
    monkeypatch.setenv("API_KEY", "secret-token")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.verify_api_key(api_key=None))
    assert exc.value.status_code == 403


def test_verify_api_key_rejects_wrong_header_when_enabled(monkeypatch):
    """Con API_KEY in env, header errato → HTTP 403."""
    monkeypatch.setenv("API_KEY", "secret-token")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.verify_api_key(api_key="wrong-token"))
    assert exc.value.status_code == 403


def test_verify_api_key_accepts_correct_header_when_enabled(monkeypatch):
    """Con API_KEY in env, header corretto → ritorna la chiave."""
    monkeypatch.setenv("API_KEY", "secret-token")
    result = asyncio.run(main.verify_api_key(api_key="secret-token"))
    assert result == "secret-token"


# --- F4: verify_api_key_strict (fail-closed per endpoint amministrativi) ---


def test_verify_api_key_strict_rejects_when_env_not_set(monkeypatch):
    """Senza API_KEY in env, lo strict mode risponde 503 anche con header valido."""
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.verify_api_key_strict(api_key="anything"))
    assert exc.value.status_code == 503


def test_verify_api_key_strict_rejects_when_env_not_set_and_no_header(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.verify_api_key_strict(api_key=None))
    assert exc.value.status_code == 503


def test_verify_api_key_strict_rejects_missing_header(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-token")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.verify_api_key_strict(api_key=None))
    assert exc.value.status_code == 403


def test_verify_api_key_strict_rejects_wrong_header(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-token")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.verify_api_key_strict(api_key="wrong-token"))
    assert exc.value.status_code == 403


def test_verify_api_key_strict_accepts_correct_header(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-token")
    result = asyncio.run(main.verify_api_key_strict(api_key="secret-token"))
    assert result == "secret-token"


def test_verify_api_key_uses_constant_time_compare(monkeypatch):
    """F3: la verifica deve usare ``secrets.compare_digest`` (resistente a timing attack)."""
    import secrets as _secrets

    monkeypatch.setenv("API_KEY", "secret-token")
    calls = []
    original = _secrets.compare_digest

    def _spy(a, b):
        calls.append((a, b))
        return original(a, b)

    monkeypatch.setattr(main.secrets, "compare_digest", _spy)
    asyncio.run(main.verify_api_key(api_key="secret-token"))
    asyncio.run(main.verify_api_key_strict(api_key="secret-token"))
    assert len(calls) == 2


# --- F5: TTL su response_store ---


def test_response_store_is_ttl_cache(monkeypatch):
    """`VisuraService.response_store` deve essere una TTLCache (fix F5)."""
    from cachetools import TTLCache

    monkeypatch.setenv("RESPONSE_TTL_SECONDS", "60")
    monkeypatch.setenv("RESPONSE_STORE_MAXSIZE", "5")
    monkeypatch.setenv("MAX_QUEUE_SIZE", "10")
    service = VisuraService()
    try:
        assert isinstance(service.response_store, TTLCache)
        assert service.response_store.ttl == 60
        assert service.response_store.maxsize == 5
    finally:
        # Evita warning su asyncio.Queue
        pass


def test_response_store_evicts_after_ttl():
    """Le entry vengono rimosse dopo che il TTL è scaduto.

    Smoke test contro l'implementazione reale di TTLCache: usiamo un TTL di
    0 secondi così la prima ``expire()`` rimuove sempre l'entry.
    """
    from cachetools import TTLCache

    cache = TTLCache(maxsize=10, ttl=0)
    cache["a"] = "x"
    cache.expire()
    assert cache.get("a") is None


# --- F10: rate-limit coda → HTTP 429 ---


def test_request_queue_uses_max_queue_size_env(monkeypatch):
    monkeypatch.setenv("MAX_QUEUE_SIZE", "7")
    service = VisuraService()
    assert service.request_queue.maxsize == 7


def test_add_request_raises_queue_full_when_full(monkeypatch):
    """Coda piena → QueueFullError dal service layer."""
    monkeypatch.setenv("MAX_QUEUE_SIZE", "1")
    service = VisuraService()
    req1 = VisuraRequest(
        request_id="r1",
        tipo_catasto="T",
        provincia="Trieste",
        comune="TRIESTE",
        sezione=None,
        foglio="1",
        particella="1",
        subalterno=None,
    )
    req2 = VisuraRequest(
        request_id="r2",
        tipo_catasto="T",
        provincia="Trieste",
        comune="TRIESTE",
        sezione=None,
        foglio="1",
        particella="2",
        subalterno=None,
    )
    asyncio.run(service.add_request(req1))
    with pytest.raises(main.QueueFullError):
        asyncio.run(service.add_request(req2))


def test_richiedi_visura_returns_429_when_queue_full():
    """Endpoint deve restituire HTTP 429 se add_request solleva QueueFullError."""

    class FullService(FakeService):
        async def add_request(self, request):
            raise main.QueueFullError("Coda piena (limite 1)")

    service = FullService()
    request = VisuraInput(
        provincia="Trieste",
        comune="TRIESTE",
        foglio="9",
        particella="166",
        sezione="_",
        subalterno=None,
        tipo_catasto="T",
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(richiedi_visura(request, service))
    assert exc.value.status_code == 429


def test_richiedi_intestati_returns_429_when_queue_full():
    class FullService(FakeService):
        async def add_intestati_request(self, request):
            raise main.QueueFullError("Coda piena (limite 1)")

    service = FullService()
    request = VisuraIntestatiInput(
        provincia="Trieste",
        comune="TRIESTE",
        foglio="9",
        particella="166",
        sezione="_",
        subalterno=None,
        tipo_catasto="T",
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(richiedi_intestati_immobile(request, service))
    assert exc.value.status_code == 429


# -------- P1 #6: codice_belfiore field --------


def test_visura_input_accepts_valid_codice_belfiore():
    """H501 (Roma) e' un codice belfiore valido (lettera + 3 cifre)."""
    request = VisuraInput(
        provincia="Roma",
        comune="Roma",
        foglio="9",
        particella="166",
        tipo_catasto="T",
        codice_belfiore="H501",
    )
    assert request.codice_belfiore == "H501"


def test_visura_input_rejects_invalid_codice_belfiore_format():
    """Formato non aderente al pattern lettera+3cifre deve fallire."""
    with pytest.raises(PydanticValidationError):
        VisuraInput(
            provincia="Roma",
            comune="Roma",
            foglio="9",
            particella="166",
            tipo_catasto="T",
            codice_belfiore="1234",
        )
    with pytest.raises(PydanticValidationError):
        VisuraInput(
            provincia="Roma",
            comune="Roma",
            foglio="9",
            particella="166",
            tipo_catasto="T",
            codice_belfiore="HH501",
        )


def test_visura_request_dataclass_carries_codice_belfiore():
    request = VisuraRequest(
        request_id="req_1",
        tipo_catasto="T",
        provincia="Roma",
        comune="Roma",
        foglio="9",
        particella="166",
        codice_belfiore="H501",
    )
    assert request.codice_belfiore == "H501"


def test_visura_intestati_input_accepts_codice_belfiore():
    request = VisuraIntestatiInput(
        provincia="Roma",
        comune="Roma",
        foglio="9",
        particella="166",
        tipo_catasto="T",
        subalterno=None,
        codice_belfiore="H501",
    )
    assert request.codice_belfiore == "H501"


# -------- Fallback T<->F when first attempt returns NESSUNA CORRISPONDENZA --------


def test_visura_input_accepts_fallback_other_catasto_flag():
    request = VisuraInput(
        provincia="Roma",
        comune="Roma",
        foglio="9",
        particella="166",
        tipo_catasto="T",
        fallback_other_catasto=True,
    )
    assert request.fallback_other_catasto is True


def test_visura_input_defaults_fallback_to_false():
    request = VisuraInput(
        provincia="Roma",
        comune="Roma",
        foglio="9",
        particella="166",
        tipo_catasto="T",
    )
    assert request.fallback_other_catasto is False


def test_visura_request_dataclass_carries_fallback_flag():
    request = VisuraRequest(
        request_id="req_1",
        tipo_catasto="T",
        provincia="Roma",
        comune="Roma",
        foglio="9",
        particella="166",
        fallback_other_catasto=True,
    )
    assert request.fallback_other_catasto is True


def test_esegui_visura_falls_back_to_other_catasto_when_not_found(monkeypatch):
    """Se il primo run_visura ritorna NESSUNA CORRISPONDENZA e fallback_other_catasto e' True,
    si ritenta sull'altro tipo e si adotta il risultato se trova qualcosa."""
    calls = []

    async def fake_run_visura(page, prov, com, sez, foglio, part, tipo, **kwargs):
        calls.append(tipo)
        if tipo == "T":
            return {
                "immobili": [],
                "results": [],
                "total_results": 0,
                "intestati": [],
                "error": "NESSUNA CORRISPONDENZA TROVATA",
            }
        return {"immobili": [{"foo": "bar"}], "results": [{"foo": "bar"}], "total_results": 1, "intestati": []}

    monkeypatch.setattr("main.run_visura", fake_run_visura)

    bm = main.BrowserManager()
    bm.authenticated = True
    bm.auth_page = object()

    async def _noop():
        return None

    bm._ensure_authenticated = _noop  # type: ignore

    req = VisuraRequest(
        request_id="req_test",
        tipo_catasto="T",
        provincia="Roma",
        comune="Roma",
        foglio="1",
        particella="1",
        fallback_other_catasto=True,
    )
    response = asyncio.run(bm.esegui_visura(req))

    assert response.success is True
    assert calls == ["T", "F"]
    assert response.tipo_catasto == "F"
    assert response.data["tipo_catasto_used"] == "F"
    assert response.data["tipo_catasto_requested"] == "T"
    assert response.data["fallback_used"] is True
    assert response.data["total_results"] == 1


def test_esegui_visura_no_fallback_when_disabled(monkeypatch):
    """Senza fallback_other_catasto il NESSUNA CORRISPONDENZA viene mantenuto."""
    calls = []

    async def fake_run_visura(page, prov, com, sez, foglio, part, tipo, **kwargs):
        calls.append(tipo)
        return {
            "immobili": [],
            "results": [],
            "total_results": 0,
            "intestati": [],
            "error": "NESSUNA CORRISPONDENZA TROVATA",
        }

    monkeypatch.setattr("main.run_visura", fake_run_visura)

    bm = main.BrowserManager()
    bm.authenticated = True
    bm.auth_page = object()

    async def _noop():
        return None

    bm._ensure_authenticated = _noop  # type: ignore

    req = VisuraRequest(
        request_id="req_test",
        tipo_catasto="T",
        provincia="Roma",
        comune="Roma",
        foglio="1",
        particella="1",
        fallback_other_catasto=False,
    )
    response = asyncio.run(bm.esegui_visura(req))

    assert calls == ["T"]
    assert response.data["tipo_catasto_used"] == "T"
    assert response.data["fallback_used"] is False


def test_esegui_visura_no_fallback_when_first_succeeds(monkeypatch):
    """Se il primo tentativo trova risultati, il fallback non viene eseguito."""
    calls = []

    async def fake_run_visura(page, prov, com, sez, foglio, part, tipo, **kwargs):
        calls.append(tipo)
        return {"immobili": [{"x": 1}], "results": [{"x": 1}], "total_results": 1, "intestati": []}

    monkeypatch.setattr("main.run_visura", fake_run_visura)

    bm = main.BrowserManager()
    bm.authenticated = True
    bm.auth_page = object()

    async def _noop():
        return None

    bm._ensure_authenticated = _noop  # type: ignore

    req = VisuraRequest(
        request_id="req_test",
        tipo_catasto="T",
        provincia="Roma",
        comune="Roma",
        foglio="1",
        particella="1",
        fallback_other_catasto=True,
    )
    response = asyncio.run(bm.esegui_visura(req))

    assert calls == ["T"]
    assert response.data["tipo_catasto_used"] == "T"
    assert response.data["fallback_used"] is False


# ---------------------------------------------------------------------------
# Login retry on SPID push timeout (auto-relogin)
# ---------------------------------------------------------------------------


def test_login_retries_on_playwright_timeout(monkeypatch):
    """Su PlaywrightTimeoutError (push SPID non approvata) il login ritenta
    fino a LOGIN_MAX_ATTEMPTS e ha successo se l'utente approva al 2 giro."""
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("LOGIN_RETRY_DELAY_S", "0")

    calls = {"login": 0}

    async def fake_login(page):
        calls["login"] += 1
        if calls["login"] < 2:
            raise PlaywrightTimeoutError("push non approvata")
        return None

    class FakePage:
        def is_closed(self):
            return False

        async def close(self):
            return None

    class FakeContext:
        async def new_page(self):
            return FakePage()

    monkeypatch.setattr("main.login", fake_login)
    bm = main.BrowserManager()
    bm.context = FakeContext()

    asyncio.run(bm.login())

    assert calls["login"] == 2
    assert bm.authenticated is True


def test_login_fails_fast_on_non_timeout_error(monkeypatch):
    """Errori non-timeout (es. credenziali errate -> RuntimeError) NON ritentano."""
    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("LOGIN_RETRY_DELAY_S", "0")

    calls = {"login": 0}

    async def fake_login(page):
        calls["login"] += 1
        raise RuntimeError("credenziali errate")

    class FakePage:
        def is_closed(self):
            return False

        async def close(self):
            return None

    class FakeContext:
        async def new_page(self):
            return FakePage()

    monkeypatch.setattr("main.login", fake_login)
    bm = main.BrowserManager()
    bm.context = FakeContext()

    with pytest.raises(main.AuthenticationError):
        asyncio.run(bm.login())

    assert calls["login"] == 1
    assert bm.authenticated is False


def test_login_exhausts_retries_then_fails(monkeypatch):
    """Se la push non viene mai approvata, dopo N tentativi si solleva AuthenticationError."""
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("LOGIN_RETRY_DELAY_S", "0")

    calls = {"login": 0}

    async def fake_login(page):
        calls["login"] += 1
        raise PlaywrightTimeoutError("push mai approvata")

    class FakePage:
        def is_closed(self):
            return False

        async def close(self):
            return None

    class FakeContext:
        async def new_page(self):
            return FakePage()

    monkeypatch.setattr("main.login", fake_login)
    bm = main.BrowserManager()
    bm.context = FakeContext()

    with pytest.raises(main.AuthenticationError):
        asyncio.run(bm.login())

    assert calls["login"] == 3
    assert bm.authenticated is False
