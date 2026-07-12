# Excel & Power BI connectors

The plan's GTM is Excel-first: traders and analysts live in Excel. Both Excel
(Get Data → From Web / Power Query) and Power BI use the same Power Query M
code below against the NRG-Flux API.

## Excel (Power Query)

1. Data → Get Data → From Other Sources → Blank Query
2. Open the Advanced Editor and paste:

```powerquery
let
    ApiKey  = "demo-key",
    BaseUrl = "http://localhost:8000",
    Area    = "10Y1001A1001A73I",   // IT-North; use /v1/eic/resolve to find codes

    Source  = Json.Document(Web.Contents(BaseUrl, [
        RelativePath = "v1/prices/dayahead",
        Query = [area = Area],
        Headers = [#"X-Api-Key" = ApiKey]
    ])),
    Series  = Source[series],
    Table   = Table.FromList(Series, Splitter.SplitByNothing()),
    Expand  = Table.ExpandRecordColumn(Table, "Column1",
        {"ts_utc", "market_day", "area_eic", "value", "unit"},
        {"ts_utc", "market_day", "area_eic", "price_eur_mwh", "unit"}),
    Typed   = Table.TransformColumnTypes(Expand, {
        {"ts_utc", type datetimezone}, {"price_eur_mwh", type number}})
in
    Typed
```

3. Close & Load. Data → Refresh All re-pulls live data (target: < 10 s refresh,
   per the pilot success metrics).

Repeat with `v1/load/actual`, `v1/flows/physical`, `v1/outages`, and
`v1/forecast/load` for the full workbook. For the forecast endpoint expand
`forecast` instead of `series` and include `mw_p10`, `mw_p50`, `mw_p90`.

## Power BI

Home → Get Data → Web → Advanced:
- URL parts: `http://localhost:8000/v1/prices/dayahead?area=10Y1001A1001A73I`
- HTTP request header: `X-Api-Key` = your key

Or paste the same M code into a Blank Query. Schedule refresh on the service
with the gateway if the API is inside your network.

## Sheet layout that wins demos (from the plan)

| Tab | Content |
|---|---|
| PUN & spreads | avg zonal price, NORD-SICI spread, sparkline per zone |
| Tomorrow load | p10/p50/p90 forecast vs TSO day-ahead, with driver notes |
| Flows | border imports vs NTC, congestion flags |
| Outages | active unavailabilities sorted by MW |
| Data status | freshness lag per dataset from `/v1/status` |
