import asyncio
import os

import pytest

import utils
from utils import parse_table


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


def test_login_raises_when_missing_required_env(monkeypatch):
    monkeypatch.delenv("ADE_USERNAME", raising=False)
    monkeypatch.delenv("ADE_PASSWORD", raising=False)

    with pytest.raises(ValueError):
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
    monkeypatch.setattr(utils, "PAGES_LOG_DIR", str(tmp_path))
    utils.PageLogger.reset_session()
    logger = utils.PageLogger("open")

    asyncio.run(logger.log(_FakePageOpen(), "step with spaces"))

    files = os.listdir(logger.base_dir)
    assert len(files) == 1
    assert files[0].startswith("01_step_with_spaces")
