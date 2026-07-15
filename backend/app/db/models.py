"""Canonical warehouse tables (section 10 of the build plan).

Every row carries lineage: source, document id, retrieval timestamp, parser version.
Works on SQLite out of the box; point DATABASE_URL at Postgres/Timescale in production.
"""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from ..config import get_settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PriceDayAhead(Base):
    __tablename__ = "prices_dayahead"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_eic: Mapped[str] = mapped_column(String(24), index=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    market_day: Mapped[str] = mapped_column(String(10))
    price_eur_mwh: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    resolution: Mapped[str] = mapped_column(String(8), default="PT60M")
    source: Mapped[str] = mapped_column(String(64), default="ENTSO-E Transparency Platform")
    source_doc_id: Mapped[str] = mapped_column(String(64), default="")
    retrieved_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (Index("ix_price_area_ts", "area_eic", "ts_utc", unique=True),)


class LoadActual(Base):
    __tablename__ = "load_actual"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_eic: Mapped[str] = mapped_column(String(24), index=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    load_mw: Mapped[float] = mapped_column(Float)
    resolution: Mapped[str] = mapped_column(String(8), default="PT60M")
    source: Mapped[str] = mapped_column(String(64), default="ENTSO-E Transparency Platform")
    source_doc_id: Mapped[str] = mapped_column(String(64), default="")
    retrieved_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    quality_flag: Mapped[str] = mapped_column(String(16), default="ok")  # ok | gap_filled | suspect
    __table_args__ = (Index("ix_load_area_ts", "area_eic", "ts_utc", unique=True),)


class LoadForecastTso(Base):
    __tablename__ = "load_forecast_tso"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_eic: Mapped[str] = mapped_column(String(24), index=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    forecast_mw: Mapped[float] = mapped_column(Float)
    issued_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_doc_id: Mapped[str] = mapped_column(String(64), default="")
    __table_args__ = (Index("ix_tsofc_area_ts", "area_eic", "ts_utc", unique=True),)


class FlowPhysical(Base):
    __tablename__ = "flows_physical"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_area_eic: Mapped[str] = mapped_column(String(24), index=True)
    to_area_eic: Mapped[str] = mapped_column(String(24), index=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    mw: Mapped[float] = mapped_column(Float)
    source_doc_id: Mapped[str] = mapped_column(String(64), default="")
    retrieved_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_flow_border_ts", "from_area_eic", "to_area_eic", "ts_utc", unique=True),)


class Outage(Base):
    __tablename__ = "outages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outage_id: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    kind: Mapped[str] = mapped_column(String(24))          # generation | transmission
    planned: Mapped[bool] = mapped_column(Boolean, default=True)
    area_eic: Mapped[str] = mapped_column(String(24), index=True)
    asset_name: Mapped[str] = mapped_column(String(128))
    asset_eic: Mapped[str] = mapped_column(String(24), default="")
    fuel: Mapped[str] = mapped_column(String(32), default="")
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    unavailable_mw: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, default="")
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EicCode(Base):
    __tablename__ = "eic_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eic_code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    code_type: Mapped[str] = mapped_column(String(16))     # area | party | resource | border
    display_name: Mapped[str] = mapped_column(String(128), index=True)
    aliases: Mapped[str] = mapped_column(Text, default="")  # comma separated
    country: Mapped[str] = mapped_column(String(2), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")


class ForecastLoad(Base):
    __tablename__ = "forecast_load"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_eic: Mapped[str] = mapped_column(String(24), index=True)
    issued_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    target_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    horizon_h: Mapped[int] = mapped_column(Integer)
    p10_mw: Mapped[float] = mapped_column(Float)
    p50_mw: Mapped[float] = mapped_column(Float)
    p90_mw: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(32), default="0.1.0")


class ForecastExplanation(Base):
    __tablename__ = "forecast_explanations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    forecast_id: Mapped[int] = mapped_column(ForeignKey("forecast_load.id"), index=True)
    feature_name: Mapped[str] = mapped_column(String(64))
    feature_value: Mapped[float] = mapped_column(Float)
    impact_mw: Mapped[float] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(24))
    method: Mapped[str] = mapped_column(String(24), default="shap")


class DataQualityEvent(Base):
    __tablename__ = "data_quality_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset: Mapped[str] = mapped_column(String(48), index=True)
    area_eic: Mapped[str] = mapped_column(String(24), default="")
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    severity: Mapped[str] = mapped_column(String(8), default="info")  # info | warn | error
    issue_type: Mapped[str] = mapped_column(String(32))               # gap | stale | outlier | revision
    details: Mapped[str] = mapped_column(Text, default="")
    detected_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Webhook(Base):
    __tablename__ = "webhooks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(512))
    events: Mapped[str] = mapped_column(String(256))  # comma separated: price.published, outage.updated, ...
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class RawDocument(Base):
    """Raw-first storage: untouched source payloads for audit & reprocessing."""
    __tablename__ = "raw_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    query: Mapped[str] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(String(16), default="")
    payload: Mapped[str] = mapped_column(Text)
    retrieved_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    parser_version: Mapped[str] = mapped_column(String(16), default="1.0.0")


class ForecastPrice(Base):
    """Day-ahead price forecast (p10/p50/p90) per area and issue time."""
    __tablename__ = "forecast_price"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_eic: Mapped[str] = mapped_column(String(24), index=True)
    issued_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    target_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    horizon_h: Mapped[int] = mapped_column(Integer)
    p10_eur: Mapped[float] = mapped_column(Float)
    p50_eur: Mapped[float] = mapped_column(Float)
    p90_eur: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(16), default="0.1.0")


class ApiClient(Base):
    """A customer of the NRG-Flux API. Keys are stored HASHED (sha256) —
    the plaintext key is shown exactly once at creation time."""
    __tablename__ = "api_clients"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(256), default="")
    key_prefix: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64))       # sha256 hex of full key
    plan: Mapped[str] = mapped_column(String(24), default="free")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class ApiUsage(Base):
    """Daily usage counters per client per endpoint (metering + quotas)."""
    __tablename__ = "api_usage_daily"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("api_clients.id"), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD (UTC)
    endpoint: Mapped[str] = mapped_column(String(96))
    count: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (Index("ix_usage_client_day_ep", "client_id", "day", "endpoint",
                            unique=True),)


# ---------------------------------------------------------------- engine

_settings = get_settings()
engine = create_engine(_settings.database_url, connect_args={"check_same_thread": False}
                       if _settings.database_url.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
