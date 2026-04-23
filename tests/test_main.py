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


def test_ottieni_visura_wraps_unexpected_exception_as_http_500():
    class BrokenService(FakeService):
        async def get_response(self, request_id):
            raise RuntimeError("store unavailable")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(ottieni_visura("req_1", BrokenService()))
    assert exc.value.status_code == 500


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
