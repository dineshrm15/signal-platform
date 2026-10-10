import json
from pathlib import Path

import httpx
import pytest
import respx
from trading_vol_regime.config import data_client, push_client
from trading_vol_regime.ingest.cboe_futures import parse_futures_curve
from trading_vol_regime.ingest.cboe_indices import (
    INDEX_SYMBOLS,
    fetch_index_quotes,
    parse_index_quote,
)
from trading_vol_regime.ingest.history import parse_history_csv

FIXTURES = Path(__file__).parent / "fixtures"


@respx.mock
def test_data_client_survives_upstream_host_move(caplog):
    raw = json.loads((FIXTURES / "index_VIX.json").read_text())
    respx.get(url__regex=r"https://cdn-api\.cboe\.com/.*_VIX\.json").mock(
        return_value=httpx.Response(307, headers={"location": "https://moved.example.org/vix"})
    )
    respx.get("https://moved.example.org/vix").mock(return_value=httpx.Response(200, json=raw))
    with data_client() as client:
        quotes = fetch_index_quotes(client, ["VIX"])
    assert quotes["VIX"].price > 0
    assert "upstream endpoint moved" in caplog.text


@respx.mock
def test_push_client_never_follows_redirects():
    respx.post("https://hooks.example.org/x").mock(
        return_value=httpx.Response(307, headers={"location": "https://169.254.169.254/"})
    )
    internal = respx.post("https://169.254.169.254/").mock(return_value=httpx.Response(200))
    with push_client() as client, pytest.raises(httpx.HTTPStatusError):
        client.post("https://hooks.example.org/x", json={}).raise_for_status()
    assert not internal.called


def test_parse_all_index_fixtures():
    for symbol in INDEX_SYMBOLS:
        raw = json.loads((FIXTURES / f"index_{symbol}.json").read_text())
        quote = parse_index_quote(symbol, raw)
        assert quote.symbol == symbol
        assert quote.price > 0
        assert quote.prev_close > 0


def test_parse_futures_curve_fixture():
    raw = json.loads((FIXTURES / "futures_VX.json").read_text())
    curve = parse_futures_curve(raw)
    assert len(curve.contracts) >= 10
    by_symbol = {c.symbol: c for c in curve.contracts}
    assert by_symbol["VX/Q6"].is_weekly is False
    assert by_symbol["VX32/Q6"].is_weekly is True
    # illiquid weekly with last_price 0 falls back to settlement
    assert by_symbol["VX32/Q6"].price == by_symbol["VX32/Q6"].settlement
    monthlies = curve.monthlies
    assert all(not c.is_weekly for c in monthlies)
    assert monthlies == sorted(monthlies, key=lambda c: c.expiration)


def test_parse_history_csv_fixture():
    text = (FIXTURES / "history_VIX_sample.csv").read_text()
    rows = list(parse_history_csv(text))
    assert len(rows) > 300
    day, close = rows[0]
    assert day.year == 1990
    assert close == 17.24
    # the "..." separator line in the sampled fixture is skipped, not fatal
    assert all(c > 0 for _, c in rows)
