# BOM Forecast ICS

Automatically fetches the Perth weather forecast from the Bureau of Meteorology
and publishes it as an ICS calendar feed every 6 hours via GitHub Actions.

**Subscribe:** `https://weather.dhsb.au/IDW12300.ics`

## Files

| File | Purpose |
|------|---------|
| `bom_forecast_to_ics.py` | Parses BOM XML and builds the ICS |
| `update.py` | Called by GitHub Actions — fetches XML and runs the converter |
| `.github/workflows/update.yml` | GitHub Actions schedule and steps |
| `docs/` | Published via GitHub Pages at weather.dhsb.au |
