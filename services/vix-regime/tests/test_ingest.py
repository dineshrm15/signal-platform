import json
from pathlib import Path

from vix_regime.ingest.cboe_futures import parse_futures_curve
from vix_regime.ingest.cboe_indices import INDEX_SYMBOLS, parse_index_quote
from vix_regime.ingest.history import parse_history_csv

FIXTURES = Path(__file__).parent / "fixtures"


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
