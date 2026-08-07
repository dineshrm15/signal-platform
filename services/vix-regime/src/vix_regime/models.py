"""Domain models for the volatility regime service."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel


class SlopeState(StrEnum):
    STEEP_CONTANGO = "STEEP_CONTANGO"
    CONTANGO = "CONTANGO"
    FLAT = "FLAT"
    BACKWARDATION = "BACKWARDATION"
    INVERTED_CRISIS = "INVERTED_CRISIS"


class Regime(StrEnum):
    CALM_CARRY = "CALM_CARRY"
    COMPRESSED_SPRING = "COMPRESSED_SPRING"
    STRESS_BUILDING = "STRESS_BUILDING"
    RISK_OFF = "RISK_OFF"
    CAPITULATION = "CAPITULATION"


class IndexQuote(BaseModel):
    symbol: str
    price: float
    prev_close: float
    change_pct: float
    last_trade_time: datetime | None = None
    source_timestamp: datetime | None = None


class FuturesContract(BaseModel):
    symbol: str
    expiration: date
    price: float  # last trade if available, else settlement
    settlement: float
    prev_settlement: float
    volume: int
    open_interest: int
    is_weekly: bool


class FuturesCurve(BaseModel):
    contracts: list[FuturesContract]

    @property
    def monthlies(self) -> list[FuturesContract]:
        return sorted(
            (c for c in self.contracts if not c.is_weekly), key=lambda c: c.expiration
        )


class CashTermStructure(BaseModel):
    values: dict[str, float]  # symbol -> level
    ratio_vix9d_vix: float | None
    ratio_vix_vix3m: float | None
    ratio_vix3m_vix6m: float | None
    slope: SlopeState


class FuturesTermStructure(BaseModel):
    m1_symbol: str
    m2_symbol: str
    m1_price: float
    m2_price: float
    front_basis_pct: float  # (M1 - VIX) / VIX
    m1_m2_roll_yield_pct: float  # (M2 - M1) / M1
    curve_twist_pct: float  # today's M1/M2 spread vs prior settlement spread, in vol points
    slope: SlopeState


class RegimeAssessment(BaseModel):
    label: Regime
    confidence: float  # 0..1
    rationale: str
    fired_rules: list[str]


class Snapshot(BaseModel):
    as_of: datetime
    indices: dict[str, IndexQuote]
    cash: CashTermStructure
    futures: FuturesTermStructure | None  # None if curve unavailable
    futures_curve: list[FuturesContract] = []  # full curve incl. weeklies
    percentiles: dict[str, dict[str, float | None]]  # symbol -> {"1y": p, "5y": p}
    regime: RegimeAssessment
    narrative: str
