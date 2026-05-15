import asyncio
import os

import pytest

import utils
from utils import parse_table


@pytest.fixture(autouse=True)
def _clear_dropdown_cache():
    """Svuota la cache dropdown tra ogni test per evitare pollution cross-test."""
    utils.invalidate_dropdown_cache(reason="pytest_fixture")
    yield
    utils.invalidate_dropdown_cache(reason="pytest_fixture_teardown")


def test_parse_table_extracts_rows_and_columns():
    html = """
    <table>
      <tr><th>Name</th><th>Role</th></tr>
      <tr><td>Alice</td><td>Admin</td></tr>
      <tr><td>Bob</td><td>User</td></tr>
    </table>
    """

    rows = parse_table(html)

    assert rows == [
        {"Name": "Alice", "Role": "Admin"},
        {"Name": "Bob", "Role": "User"},
    ]


def test_parse_table_pads_missing_cells_with_empty_string():
    html = """
    <table>
      <tr><th>Name</th><th>Role</th><th>City</th></tr>
      <tr><td>Alice</td><td>Admin</td></tr>
    </table>
    """

    rows = parse_table(html)

    assert rows == [{"Name": "Alice", "Role": "Admin", "City": ""}]


def test_parse_table_returns_empty_list_when_no_data_rows():
    html = "<table><tr><th>Name</th></tr></table>"

    rows = parse_table(html)

    assert rows == []


class _FakeOption:
    def __init__(self, value, text):
        self._value = value
        self._text = text

    async def get_attribute(self, _name):
        return self._value

    async def inner_text(self):
        return self._text


class _FakeLocator:
    def __init__(self, options):
        self._options = options

    async def all(self):
        return self._options


class _FakePageForMatch:
    def __init__(self, options):
        self._options = options

    def locator(self, _selector):
        return _FakeLocator(self._options)

    async def evaluate(self, _script, _selector=None):
        # Simula il page.evaluate usato da _collect_options_fast: ritorna
        # [[value, text], ...] in modo coerente con i fake options.
        out = []
        for opt in self._options:
            v = await opt.get_attribute("value")
            t = await opt.inner_text()
            out.append([v or "", (t or "").strip()])
        return out


class _FakePageClosed:
    url = "https://example.test"

    def is_closed(self):
        return True


class _FakePageOpen:
    url = "https://example.test/path"

    def is_closed(self):
        return False

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    async def content(self):
        return "<html><body>ok</body></html>"


def test_find_best_option_match_exact_value_match_wins():
    page = _FakePageForMatch([_FakeOption("P", "SEZIONE P")])
    result = asyncio.run(utils.find_best_option_match(page, "select[name='sezione']", "P"))
    assert result == "P"


def test_find_best_option_match_exact_text_match_wins():
    page = _FakePageForMatch([_FakeOption("TRIESTE", "TRIESTE")])
    result = asyncio.run(utils.find_best_option_match(page, "select[name='denomComune']", "TRIESTE"))
    assert result == "TRIESTE"


def test_find_best_option_match_startswith_candidate_is_selected():
    page = _FakePageForMatch([_FakeOption("TS", "TRIESTE CENTRO"), _FakeOption("RO", "ROMA")])
    result = asyncio.run(utils.find_best_option_match(page, "select[name='denomComune']", "TRIESTE"))
    assert result == "TS"


def test_find_best_option_match_returns_none_when_no_match():
    page = _FakePageForMatch([_FakeOption("RO", "ROMA")])
    result = asyncio.run(utils.find_best_option_match(page, "select[name='denomComune']", "MILANO"))
    assert result is None


# --- F-NEW1/F-NEW2: normalizzazione provincia/comune SISTER -----------------


def test_normalize_strips_accents_and_apostrophes():
    assert utils._normalize_for_match("L'Aquila") == "l aquila"
    assert utils._normalize_for_match("L\u2019AQUILA Territorio") == "l aquila"
    assert utils._normalize_for_match("Alì") == "ali"
    assert utils._normalize_for_match("Forlì-Cesena") == "forli cesena"


def test_normalize_strips_territorio_suffix():
    assert utils._normalize_for_match("ALESSANDRIA Territorio") == "alessandria"
    # Anche con doppio spazio / case mista
    assert utils._normalize_for_match("Reggio nell'Emilia Territorio") == "reggio nell emilia"


def test_find_best_option_match_handles_aquila_with_apostrophe():
    """Caso F-NEW1: input ISTAT `L'Aquila`, option SISTER `L'AQUILA Territorio`."""
    page = _FakePageForMatch(
        [
            _FakeOption("ALESSANDRIA", "ALESSANDRIA Territorio"),
            _FakeOption("L'AQUILA", "L'AQUILA Territorio"),
            _FakeOption("AOSTA", "AOSTA Territorio"),
        ]
    )
    result = asyncio.run(utils.find_best_option_match(page, "select[name='listacom']", "L'Aquila"))
    assert result == "L'AQUILA"


def test_find_best_option_match_handles_typographic_apostrophe():
    """Caso F-NEW1: SISTER usa apostrofo tipografico ’ invece dell'ASCII '."""
    page = _FakePageForMatch([_FakeOption("L'AQUILA", "L\u2019AQUILA Territorio")])
    result = asyncio.run(utils.find_best_option_match(page, "select[name='listacom']", "L'Aquila"))
    assert result == "L'AQUILA"


def test_find_best_option_match_handles_reggio_emilia():
    """Caso live: ISTAT espone `Reggio nell'Emilia` ma SISTER mostra solo
    `REGGIO EMILIA Territorio`. Risolto tramite alias catastale."""
    page = _FakePageForMatch(
        [
            _FakeOption("REGGIO EMILIA", "REGGIO EMILIA Territorio"),
            _FakeOption("REGGIO CALABRIA", "REGGIO CALABRIA Territorio"),
        ]
    )
    result = asyncio.run(utils.find_best_option_match(page, "select[name='listacom']", "Reggio nell'Emilia"))
    assert result == "REGGIO EMILIA"


def test_find_best_option_match_catastal_alias_verbano_to_verbania():
    """Caso F-NEW1: ISTAT `Verbano-Cusio-Ossola` → catasto `Verbania`."""
    page = _FakePageForMatch(
        [
            _FakeOption("VARESE", "VARESE Territorio"),
            _FakeOption("VERBANIA", "VERBANIA Territorio"),
            _FakeOption("VERCELLI", "VERCELLI Territorio"),
        ]
    )
    result = asyncio.run(utils.find_best_option_match(page, "select[name='listacom']", "Verbano-Cusio-Ossola"))
    assert result == "VERBANIA"


def test_find_best_option_match_catastal_alias_sud_sardegna_to_cagliari():
    """Sud Sardegna (istituita 2016) → SISTER la mappa sotto CAGLIARI."""
    page = _FakePageForMatch(
        [
            _FakeOption("CAGLIARI", "CAGLIARI Territorio"),
            _FakeOption("CALTANISSETTA", "CALTANISSETTA Territorio"),
        ]
    )
    result = asyncio.run(utils.find_best_option_match(page, "select[name='listacom']", "Sud Sardegna"))
    assert result == "CAGLIARI"


def test_find_best_option_match_catastal_alias_pesaro_e_urbino_to_pesaro():
    """ISTAT `Pesaro e Urbino` → SISTER `PESARO Territorio`."""
    page = _FakePageForMatch(
        [
            _FakeOption("PESARO", "PESARO Territorio"),
            _FakeOption("PERUGIA", "PERUGIA Territorio"),
        ]
    )
    result = asyncio.run(utils.find_best_option_match(page, "select[name='listacom']", "Pesaro e Urbino"))
    assert result == "PESARO"


def test_find_best_option_match_catastal_alias_valle_aosta_bilingue_to_aosta():
    """ISTAT `Valle d'Aosta/Vallée d'Aoste` (bilingue) → SISTER `AOSTA Territorio`."""
    page = _FakePageForMatch(
        [
            _FakeOption("AOSTA", "AOSTA Territorio"),
            _FakeOption("ALESSANDRIA", "ALESSANDRIA Territorio"),
        ]
    )
    result = asyncio.run(utils.find_best_option_match(page, "select[name='listacom']", "Valle d'Aosta/Vallée d'Aoste"))
    assert result == "AOSTA"


def test_find_best_option_match_catastal_alias_monza_to_milano():
    """ISTAT `Monza e della Brianza`: SISTER non la espone, i suoi comuni sono
    catastalmente sotto MILANO Territorio (storico pre-2009)."""
    page = _FakePageForMatch(
        [
            _FakeOption("MILANO", "MILANO Territorio"),
            _FakeOption("MANTOVA", "MANTOVA Territorio"),
        ]
    )
    result = asyncio.run(utils.find_best_option_match(page, "select[name='listacom']", "Monza e della Brianza"))
    assert result == "MILANO"


def test_find_best_option_match_comune_with_grave_accent():
    """Caso F-NEW2: comune `Alì` (con accento grave) deve matchare `ALI'` / `ALI`."""
    page = _FakePageForMatch(
        [
            _FakeOption("ALI'", "ALI'"),
            _FakeOption("ALI TERME", "ALI' TERME"),
        ]
    )
    result = asyncio.run(utils.find_best_option_match(page, "select[name='denomComune']", "Alì"))
    assert result == "ALI'"


def test_unsupported_provinces_sister_trento_bolzano_keys_present():
    """Trento e Bolzano sono province autonome con Catasto Tavolare, non
    disponibili su SISTER. Le chiavi devono essere normalizzate con
    _normalize_for_match per essere risolte a tempo di run_visura."""
    assert "trento" in utils._UNSUPPORTED_PROVINCES_SISTER
    assert "bolzano" in utils._UNSUPPORTED_PROVINCES_SISTER
    # Le ragioni contengono "Tavolare" → utile per messaggi diagnostici.
    assert "Tavolare" in utils._UNSUPPORTED_PROVINCES_SISTER["trento"]
    assert "Tavolare" in utils._UNSUPPORTED_PROVINCES_SISTER["bolzano"]


def test_login_raises_when_missing_required_env(monkeypatch):
    monkeypatch.delenv("SPID_PROVIDER", raising=False)
    monkeypatch.delenv("ADE_USERNAME", raising=False)
    monkeypatch.delenv("ADE_PASSWORD", raising=False)

    with pytest.raises(ValueError):
        asyncio.run(utils.login(object()))


def test_login_default_provider_is_sielte_when_env_missing(monkeypatch):
    """SPID_PROVIDER non settato → default 'sielte' (richiede ADE_*)."""
    monkeypatch.delenv("SPID_PROVIDER", raising=False)
    monkeypatch.delenv("ADE_USERNAME", raising=False)
    monkeypatch.delenv("ADE_PASSWORD", raising=False)

    with pytest.raises(ValueError, match="ADE_USERNAME"):
        asyncio.run(utils.login(object()))


def test_login_poste_provider_requires_poste_credentials(monkeypatch):
    """SPID_PROVIDER='poste' senza POSTE_USERNAME/PASSWORD → ValueError."""
    monkeypatch.setenv("SPID_PROVIDER", "poste")
    monkeypatch.delenv("POSTE_USERNAME", raising=False)
    monkeypatch.delenv("POSTE_PASSWORD", raising=False)

    with pytest.raises(ValueError, match="POSTE_USERNAME"):
        asyncio.run(utils.login(object()))


def test_login_rejects_unknown_provider(monkeypatch):
    """SPID_PROVIDER con valore non supportato → ValueError con lista valida."""
    monkeypatch.setenv("SPID_PROVIDER", "lepida")

    with pytest.raises(ValueError, match="sielte.*poste|poste.*sielte"):
        asyncio.run(utils.login(object()))


def test_login_provider_value_is_case_insensitive(monkeypatch):
    """SPID_PROVIDER='POSTE' (maiuscolo) deve essere normalizzato a 'poste'."""
    monkeypatch.setenv("SPID_PROVIDER", "POSTE")
    monkeypatch.delenv("POSTE_USERNAME", raising=False)
    monkeypatch.delenv("POSTE_PASSWORD", raising=False)

    # Se la normalizzazione case-insensitive funziona, deve arrivare al
    # controllo POSTE_USERNAME e sollevare il ValueError specifico.
    with pytest.raises(ValueError, match="POSTE_USERNAME"):
        asyncio.run(utils.login(object()))


def test_login_sister_provider_requires_sister_credentials(monkeypatch):
    """SPID_PROVIDER='sister' senza SISTER_USERNAME/PASSWORD → ValueError."""
    monkeypatch.setenv("SPID_PROVIDER", "sister")
    monkeypatch.delenv("SISTER_USERNAME", raising=False)
    monkeypatch.delenv("SISTER_PASSWORD", raising=False)

    with pytest.raises(ValueError, match="SISTER_USERNAME"):
        asyncio.run(utils.login(object()))


def test_login_sister_provider_case_insensitive(monkeypatch):
    """SPID_PROVIDER='SISTER' (maiuscolo) deve essere normalizzato a 'sister'."""
    monkeypatch.setenv("SPID_PROVIDER", "SISTER")
    monkeypatch.delenv("SISTER_USERNAME", raising=False)
    monkeypatch.delenv("SISTER_PASSWORD", raising=False)

    with pytest.raises(ValueError, match="SISTER_USERNAME"):
        asyncio.run(utils.login(object()))


def test_login_unknown_provider_error_lists_sister(monkeypatch):
    """Il messaggio di errore deve includere 'sister' tra i valori validi."""
    monkeypatch.setenv("SPID_PROVIDER", "lepida")

    with pytest.raises(ValueError, match="sister"):
        asyncio.run(utils.login(object()))


def test_run_visura_immobile_requires_subalterno(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "PAGES_LOG_DIR", str(tmp_path))

    with pytest.raises(ValueError):
        asyncio.run(utils.run_visura_immobile(page=None, subalterno=None))


def test_page_logger_reset_session_resets_counters():
    utils.PageLogger._session_id = "old"
    utils.PageLogger._flow_counters = {"visura": 3}

    utils.PageLogger.reset_session()

    assert utils.PageLogger._session_id is not None
    assert utils.PageLogger._flow_counters == {}


def test_page_logger_increments_flow_directory_suffix(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "PAGES_LOG_DIR", str(tmp_path))
    utils.PageLogger.reset_session()

    logger1 = utils.PageLogger("visura")
    logger2 = utils.PageLogger("visura")

    assert os.path.basename(logger1.base_dir) == "visura"
    assert os.path.basename(logger2.base_dir) == "visura_002"


def test_page_logger_log_skips_closed_page(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "PAGES_LOG_DIR", str(tmp_path))
    utils.PageLogger.reset_session()
    logger = utils.PageLogger("closed")

    asyncio.run(logger.log(_FakePageClosed(), "step"))

    assert logger.step == 1
    assert os.listdir(logger.base_dir) == []


def test_page_logger_log_writes_html_file(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_PAGES", "1")
    monkeypatch.setattr(utils, "PAGES_LOG_DIR", str(tmp_path))
    utils.PageLogger.reset_session()
    logger = utils.PageLogger("open")

    asyncio.run(logger.log(_FakePageOpen(), "step with spaces"))

    files = os.listdir(logger.base_dir)
    assert len(files) == 1
    assert files[0].startswith("01_step_with_spaces")


def test_page_logger_log_disabled_by_env(monkeypatch, tmp_path):
    """LOG_PAGES=0 deve trasformare log() in no-op (gating audit 2026-05-15)."""
    monkeypatch.setenv("LOG_PAGES", "0")
    monkeypatch.setattr(utils, "PAGES_LOG_DIR", str(tmp_path))
    utils.PageLogger.reset_session()
    logger = utils.PageLogger("open")

    asyncio.run(logger.log(_FakePageOpen(), "step"))

    assert os.listdir(logger.base_dir) == []


def test_resolve_pages_log_dir_uses_env_var_when_set(monkeypatch, tmp_path):
    target = tmp_path / "from-env"
    monkeypatch.setenv("PAGES_LOG_DIR", str(target))
    monkeypatch.setattr(utils, "PAGES_LOG_DIR", "/this/should/be/ignored")

    resolved = utils._resolve_pages_log_dir()

    assert resolved == str(target)
    assert target.is_dir()


def test_resolve_pages_log_dir_falls_back_when_preferred_unwritable(monkeypatch, tmp_path):
    monkeypatch.delenv("PAGES_LOG_DIR", raising=False)
    monkeypatch.setattr(utils, "PAGES_LOG_DIR", "/proc/1/no-write-here")
    fallback = tmp_path / "fallback-pages"
    monkeypatch.setattr(utils, "FALLBACK_PAGES_LOG_DIR", str(fallback))

    resolved = utils._resolve_pages_log_dir()

    assert resolved == str(fallback)
    assert fallback.is_dir()


def test_resolve_pages_log_dir_returns_none_when_no_writable_dir(monkeypatch):
    monkeypatch.delenv("PAGES_LOG_DIR", raising=False)
    monkeypatch.setattr(utils, "PAGES_LOG_DIR", "/proc/1/preferred-bad")
    monkeypatch.setattr(utils, "FALLBACK_PAGES_LOG_DIR", "/proc/1/fallback-bad")

    resolved = utils._resolve_pages_log_dir()

    assert resolved is None


def test_page_logger_disabled_when_no_writable_dir(monkeypatch):
    monkeypatch.delenv("PAGES_LOG_DIR", raising=False)
    monkeypatch.setattr(utils, "PAGES_LOG_DIR", "/proc/1/preferred-bad")
    monkeypatch.setattr(utils, "FALLBACK_PAGES_LOG_DIR", "/proc/1/fallback-bad")
    utils.PageLogger.reset_session()

    logger = utils.PageLogger("disabled-flow")

    assert logger.base_dir is None
    # ``log()`` deve essere no-op senza sollevare eccezioni
    asyncio.run(logger.log(_FakePageOpen(), "any"))
    assert logger.step == 1


# -------- P1 #6: find_option_by_codice_belfiore --------


def test_find_option_by_codice_belfiore_returns_value_with_prefix():
    """SISTER option value format: 'CODICE#NOME#0#0' (es. H501#ROMA#0#0)."""
    page = _FakePageForMatch(
        [
            _FakeOption("A737#BELFIORE#0#0", "BELFIORE"),
            _FakeOption("H501#ROMA#0#0", "ROMA"),
            _FakeOption("F839#CASANDRINO#0#0", "CASANDRINO"),
        ]
    )
    result = asyncio.run(utils.find_option_by_codice_belfiore(page, "select[name='denomComune']", "H501"))
    assert result == "H501#ROMA#0#0"


def test_find_option_by_codice_belfiore_is_case_insensitive():
    page = _FakePageForMatch([_FakeOption("H501#ROMA#0#0", "ROMA")])
    result = asyncio.run(utils.find_option_by_codice_belfiore(page, "sel", "h501"))
    assert result == "H501#ROMA#0#0"


def test_find_option_by_codice_belfiore_returns_none_on_missing():
    page = _FakePageForMatch([_FakeOption("A737#BELFIORE#0#0", "BELFIORE")])
    result = asyncio.run(utils.find_option_by_codice_belfiore(page, "sel", "Z999"))
    assert result is None


def test_find_option_by_codice_belfiore_returns_none_on_empty_input():
    page = _FakePageForMatch([_FakeOption("H501#ROMA#0#0", "ROMA")])
    assert asyncio.run(utils.find_option_by_codice_belfiore(page, "sel", "")) is None
    assert asyncio.run(utils.find_option_by_codice_belfiore(page, "sel", "   ")) is None
    assert asyncio.run(utils.find_option_by_codice_belfiore(page, "sel", None)) is None


# -------- MVP-2: dropdown cache + _collect_options_fast --------


def test_collect_options_fast_returns_value_text_tuples():
    """Single page.evaluate path: ritorna lista di (value, text) normalizzata."""
    page = _FakePageForMatch(
        [
            _FakeOption("V1", "  T1  "),
            _FakeOption("V2", "T2"),
            _FakeOption("", "skipped_text_only"),
        ]
    )
    items = asyncio.run(utils._collect_options_fast(page, "any"))
    # Il fake evaluate fa strip() su text; value vuoto resta vuoto
    assert items == [("V1", "T1"), ("V2", "T2"), ("", "skipped_text_only")]


def test_format_options_for_debug_filters_empty():
    items = [("V1", "T1"), ("", "T_no_value"), ("V3", ""), ("V4", "T4")]
    assert utils._format_options_for_debug(items) == ["T1 (V1)", "T4 (V4)"]


def test_dropdown_cache_province_hit_avoids_second_evaluate(monkeypatch):
    """Seconda lookup sulla stessa provincia deve usare la cache (no re-evaluate)."""
    page = _FakePageForMatch([_FakeOption("RM", "ROMA"), _FakeOption("MI", "MILANO")])
    monkeypatch.setenv("DROPDOWN_CACHE", "1")
    utils.invalidate_dropdown_cache(reason="test_setup")

    # Conta quante volte _collect_options_fast viene chiamato
    calls = {"n": 0}
    real = utils._collect_options_fast

    async def counting(p, s):
        calls["n"] += 1
        return await real(p, s)

    monkeypatch.setattr(utils, "_collect_options_fast", counting)

    r1 = asyncio.run(utils.find_best_option_match(page, "select[name='listacom']", "ROMA"))
    r2 = asyncio.run(utils.find_best_option_match(page, "select[name='listacom']", "ROMA"))
    assert r1 == "RM" and r2 == "RM"
    # Solo una chiamata: la seconda risolve via cache by_norm
    assert calls["n"] == 1, f"expected 1 evaluate call, got {calls['n']}"


def test_dropdown_cache_disabled_refetches_each_time(monkeypatch):
    page = _FakePageForMatch([_FakeOption("RM", "ROMA")])
    monkeypatch.setenv("DROPDOWN_CACHE", "0")
    utils.invalidate_dropdown_cache(reason="test_setup")

    calls = {"n": 0}
    real = utils._collect_options_fast

    async def counting(p, s):
        calls["n"] += 1
        return await real(p, s)

    monkeypatch.setattr(utils, "_collect_options_fast", counting)

    asyncio.run(utils.find_best_option_match(page, "select[name='listacom']", "ROMA"))
    asyncio.run(utils.find_best_option_match(page, "select[name='listacom']", "ROMA"))
    assert calls["n"] == 2, f"expected 2 evaluate calls (cache off), got {calls['n']}"


def test_find_option_by_codice_belfiore_no_cache_when_provincia_unknown(monkeypatch):
    """Il path comune con provincia_value=None NON deve cachare (evita cross-province
    poisoning, regressione 2026-05-15: Mantova comuni riusati per Savona/Pesaro).
    Aspettato: una evaluate per chiamata."""
    page = _FakePageForMatch(
        [
            _FakeOption("H501#ROMA#0#0", "ROMA"),
            _FakeOption("F205#MILANO#0#0", "MILANO"),
        ]
    )
    monkeypatch.setenv("DROPDOWN_CACHE", "1")
    utils.invalidate_dropdown_cache(reason="test_setup")

    calls = {"n": 0}
    real = utils._collect_options_fast

    async def counting(p, s):
        calls["n"] += 1
        return await real(p, s)

    monkeypatch.setattr(utils, "_collect_options_fast", counting)

    r1 = asyncio.run(utils.find_option_by_codice_belfiore(page, "select[name='denomComune']", "H501"))
    r2 = asyncio.run(utils.find_option_by_codice_belfiore(page, "select[name='denomComune']", "F205"))
    assert r1 == "H501#ROMA#0#0"
    assert r2 == "F205#MILANO#0#0"
    # Due evaluate: nessuna cache senza provincia_value
    assert calls["n"] == 2, f"expected 2 evaluate calls (no provincia_value), got {calls['n']}"
    # Nessuna entry comune cachata
    assert not any(k[0] == "comune" for k in utils._DROPDOWN_CACHE.keys())


def test_comune_cache_scoped_by_provincia(monkeypatch):
    """Con provincia_value valorizzato la cache comune deve essere safe E attiva:
    - stessa provincia → 1 evaluate per 2 lookup (cache hit)
    - provincia diversa → 2 evaluate per 2 lookup (chiave diversa, no poisoning)
    """
    page = _FakePageForMatch(
        [
            _FakeOption("H501#ROMA#0#0", "ROMA"),
            _FakeOption("F205#MILANO#0#0", "MILANO"),
        ]
    )
    monkeypatch.setenv("DROPDOWN_CACHE", "1")
    utils.invalidate_dropdown_cache(reason="test_setup")

    calls = {"n": 0}
    real = utils._collect_options_fast

    async def counting(p, s):
        calls["n"] += 1
        return await real(p, s)

    monkeypatch.setattr(utils, "_collect_options_fast", counting)

    # Stessa provincia → seconda chiamata cache hit
    r1 = asyncio.run(
        utils.find_option_by_codice_belfiore(
            page, "select[name='denomComune']", "H501", provincia_value="ROMA Territorio-RM"
        )
    )
    r2 = asyncio.run(
        utils.find_option_by_codice_belfiore(
            page, "select[name='denomComune']", "F205", provincia_value="ROMA Territorio-RM"
        )
    )
    assert r1 == "H501#ROMA#0#0"
    assert r2 == "F205#MILANO#0#0"
    assert calls["n"] == 1, f"expected 1 evaluate (same provincia), got {calls['n']}"

    # Provincia diversa → cache miss separata (no poisoning)
    r3 = asyncio.run(
        utils.find_option_by_codice_belfiore(
            page, "select[name='denomComune']", "H501", provincia_value="MILANO Territorio-MI"
        )
    )
    assert r3 == "H501#ROMA#0#0"
    assert calls["n"] == 2, f"expected 2 evaluate (diff provincia), got {calls['n']}"

    # Chiavi cache distinte per provincia
    keys = {k for k in utils._DROPDOWN_CACHE.keys() if k[0] == "comune"}
    assert keys == {("comune", "ROMA Territorio-RM"), ("comune", "MILANO Territorio-MI")}


def test_find_best_option_match_comune_uses_provincia_scoped_cache(monkeypatch):
    """find_best_option_match con provincia_value valorizzato sfrutta la cache
    comune scoped, senza cross-province poisoning."""
    page = _FakePageForMatch(
        [
            _FakeOption("H501#ROMA#0#0", "ROMA"),
            _FakeOption("F205#MILANO#0#0", "MILANO"),
        ]
    )
    monkeypatch.setenv("DROPDOWN_CACHE", "1")
    utils.invalidate_dropdown_cache(reason="test_setup")

    calls = {"n": 0}
    real = utils._collect_options_fast

    async def counting(p, s):
        calls["n"] += 1
        return await real(p, s)

    monkeypatch.setattr(utils, "_collect_options_fast", counting)

    r1 = asyncio.run(
        utils.find_best_option_match(page, "select[name='denomComune']", "ROMA", provincia_value="ROMA Territorio-RM")
    )
    r2 = asyncio.run(
        utils.find_best_option_match(page, "select[name='denomComune']", "MILANO", provincia_value="ROMA Territorio-RM")
    )
    assert r1 == "H501#ROMA#0#0"
    assert r2 == "F205#MILANO#0#0"
    assert calls["n"] == 1, f"expected 1 evaluate (cache hit by provincia), got {calls['n']}"


def test_invalidate_dropdown_cache_clears_state():
    utils._DROPDOWN_CACHE[("province",)] = {"items": [], "by_norm": {}}
    utils._DROPDOWN_CACHE[("comune", "")] = {"items": [], "by_norm": {}, "by_belfiore": {}}
    utils.invalidate_dropdown_cache(reason="unit_test")
    assert utils._DROPDOWN_CACHE == {}
