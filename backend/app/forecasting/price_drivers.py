"""v8 driver-series builders: outage capacity + neighbor price."""


NEIGHBOR_ZONE = {  # gate-closure/coupling neighbor used as a driver
    "10Y1001A1001A73I": "10YFR-RTE------C",   # IT-North <- FR
    "10YFR-RTE------C": "10Y1001A1001A82H",   # FR <- DE-LU
    "10Y1001A1001A82H": "10YFR-RTE------C",   # DE-LU <- FR
    "10YCH-SWISSGRIDZ": "10Y1001A1001A82H",   # CH <- DE-LU
}


def outage_series(db, zone_eic: str) -> "pd.Series":
    """Hourly MW unavailable in the zone: sum of overlapping outages (v8)."""
    import pandas as pd
    from sqlalchemy import select
    from app.db.models import Outage
    rows = db.execute(select(Outage.start_utc, Outage.end_utc,
                             Outage.unavailable_mw)
                      .where(
                          Outage.area_eic == zone_eic,
                          Outage.kind == "generation",
                          Outage.unavailable_mw >= 0,
                          Outage.unavailable_mw < 10_000,
                      )).all()
    if not rows:
        return pd.Series(dtype=float)
    idx_min = min(r[0] for r in rows); idx_max = max(r[1] for r in rows)
    idx = pd.date_range(idx_min, idx_max, freq="h", tz="UTC")
    tot = pd.Series(0.0, index=idx)
    for s, e, mw in rows:
        if mw:
            tot.loc[(tot.index >= pd.Timestamp(s, tz="UTC") if s.tzinfo is None else s) &
                    (tot.index < (pd.Timestamp(e, tz="UTC") if e.tzinfo is None else e))] += float(mw)
    return tot


def neighbor_price_series(db, zone_eic: str) -> "pd.Series":
    import pandas as pd
    from sqlalchemy import select
    from app.db.models import PriceDayAhead
    nb = NEIGHBOR_ZONE.get(zone_eic)
    if not nb:
        return pd.Series(dtype=float)
    rows = db.execute(select(PriceDayAhead.ts_utc, PriceDayAhead.price_eur_mwh)
                      .where(PriceDayAhead.area_eic == nb)
                      .order_by(PriceDayAhead.ts_utc)).all()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series([r[1] for r in rows],
                  index=pd.DatetimeIndex([r[0] for r in rows]))
    if s.index.tz is None:
        s.index = s.index.tz_localize("UTC")
    return s[~s.index.duplicated()]
