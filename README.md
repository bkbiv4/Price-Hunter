# Price Hunter

Price Hunter is a local Python app for organizing sports-card inventory and preparing consistent eBay listings.

## Current features

- SportsCardsPro card search with separate raw, generic graded, PSA, BGS, SGC, and CGC values
- Set and parallel price search with CSV export (up to the API's 20-result search limit)
- SportsCardsPro collection CSV import with price conversion and duplicate protection
- Inventory columns for Graded 8/8.5, Graded 9, and PSA 10 with selective API refresh
- Manual entry when the API is unavailable
- Local SQLite inventory with generated SKUs
- Cost and estimated-value dashboard
- eBay title, description, and Buy It Now price generator
- Separate grader, numeric grade, and certification-number tracking for slabs
- Draft, ready, listed, and sold statuses
- CSV inventory export

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Open `.env` and place your private SportsCardsPro token after `SPORTSCARDSPRO_TOKEN=`. Do not share or commit that file.

Run the app:

```powershell
streamlit run app.py
```

The database is stored locally as `price_hunter.db`.

## Planned eBay connection

Direct eBay publishing will require an eBay Developers Program account, OAuth authorization, an inventory location, unique SKUs, and payment, fulfillment, and return business policies. Until that is configured, Price Hunter creates and tracks listing drafts without making changes to your eBay account.
