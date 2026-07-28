# Price Hunter

Price Hunter is a local Python app for organizing sports-card inventory and preparing consistent eBay listings.

## Current features

- SportsCardsPro card search with separate raw, generic graded, PSA, BGS, SGC, and CGC values
- Set and parallel price search with CSV export (up to the API's 20-result search limit)
- SportsCardsPro collection CSV import with price conversion and duplicate protection
- Inventory columns for Graded 8/8.5, Graded 9, and PSA 10 with selective API refresh
- Inventory filters for player, year, brand, set, parallel, card number, type, location, status, price, and refresh coverage
- Background grading-price refresh with live progress, pause/resume, retry, and last-refreshed timestamps
- Purchase-lot import from CardBusiness Starter Tracker workbooks
- Equal per-card cost allocation with two-decimal exact reconciliation
- Business expense, eBay sales, receipt indexing, and reporting pages
- Row-based inventory editing, deletion, and bulk location/status/set/quantity changes
- Multi-card listing queues, bulk draft generation, and selected-card background price refresh
- Sales-to-inventory matching with cost of goods, monthly reporting, and profit
- Automatic high-confidence eBay sale matching by item ID or Custom label / Price Hunter SKU
- Filterable sales dashboard with revenue, refunds, selling costs, COGS, margin, marketplace performance, and profit rankings
- Multi-card grading submissions with fee allocation, progress tracking, returned grades, and certification numbers
- Grading opportunity rankings with break-even grade, PSA 8/9/10 profit, expected profit, and CSV export
- Completed-sale workflow with automatic quantity reduction, net proceeds, COGS, and profit
- Multi-card lot sales, partial refunds, returns, cancellations, one-time inventory restoration, and packing slips
- Inventory activity history for acquisitions, edits, grading, sales, and deletions
- Sandbox-first eBay OAuth/Inventory API connection, offer creation, and confirmed publishing
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
