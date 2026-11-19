# abm-tam-builder

**Build your account universe from open data: every hotel in a city, every software company over 50 people in a country, and the size of the market behind them. Free: OpenStreetMap, Wikidata and US Census data.**

ABM starts with the list. This repo turns three open sources into a clean `accounts.csv` (deduplicated by domain) and a `market_size.csv`, ready to feed every other repo in the set.

| Source | Best for | What you get |
|---|---|---|
| **OpenStreetMap** (Overpass API) | Local and physical businesses: hotels, restaurants, clinics, dealers, gyms | Name, website/domain, brand, operator, city |
| **Wikidata** (SPARQL) | Companies by industry or type and country | Name, website/domain, employees, HQ, country |
| **US Census County Business Patterns** | Market sizing by industry (NAICS) and county/state | Establishments, employees, payroll per geography |

## Quickstart (no keys)

```bash
git clone https://github.com/Rahul-peacenik159/abm-tam-builder.git
cd abm-tam-builder
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m tam_builder build --preset hotels-dallas              # every hotel in Dallas from OpenStreetMap
python -m tam_builder build --preset software-companies-us      # US software companies with 50+ employees
python -m tam_builder build --preset hotels-texas-market-size   # hotel establishments per Texas county
```

Presets live in [`config/presets.yaml`](config/presets.yaml). Or go custom:

```bash
python -m tam_builder build --source osm --area "Austin" --tag amenity=restaurant
python -m tam_builder build --source wikidata --industry Q880371 --country Q30 --min-employees 200
python -m tam_builder build --source census --naics 722511 --state 48
```

Then combine lists and remove customers or open deals:

```bash
python -m tam_builder merge out/accounts.csv more_accounts.csv --exclude customers.csv --out out/accounts_merged.csv
```

The output columns (`domain`, `company`, `city`...) work directly as `--accounts` input for abm-tech-footprint, abm-hiring-signals, abm-news-pulse and the rest.

### About domains

Many local businesses list a chain-brand page (`hilton.com/...`), a booking site or a social profile as their website. Those rows are kept but flagged in the `note` column, because the property-level domain is unknown. Rows without a website are kept too: they're still real accounts for phone, mail or field plays.

## Run it on GitHub

**Actions > run > Run workflow**: pick a preset, or type an OpenStreetMap area and tag. The first 40 accounts (or the market-size table) appear on the run's Summary page; the full CSV is in the **results** artifact.

## BigQuery

[`bigquery/10_load_accounts.sql`](bigquery/10_load_accounts.sql) merges a new universe into the shared `abm.accounts` table without duplicates.

## Limits and good manners

- OpenStreetMap coverage is excellent for hotels and restaurants in most cities and patchier for offices. Check a few known accounts.
- Wikidata covers notable companies (thousands per industry, not every small business); employee counts may be dated.
- The public Overpass server is shared: run one query at a time. Set `OVERPASS_URL` to another instance for heavy use.
- Census CBP data lags about two years. Newer years may code industries in NAICS 2022 (`--naics-var NAICS2022`).
- OpenStreetMap data is (c) OpenStreetMap contributors, ODbL: credit it if you publish derived lists. Wikidata is CC0. Census data is public domain.

## Part of the open ABM bundles

| Repo | What it adds |
|---|---|
| [abm-tech-footprint](https://github.com/Rahul-peacenik159/abm-tech-footprint) | what accounts run on their website, DNS and email; security and email posture |
| [abm-hiring-signals](https://github.com/Rahul-peacenik159/abm-hiring-signals) | who is hiring for what (Greenhouse, Lever, Ashby public job boards) |
| [abm-public-company-triggers](https://github.com/Rahul-peacenik159/abm-public-company-triggers) | SEC filings: exec changes, M&A, restructuring, keyword mentions, revenue trend |
| [abm-news-pulse](https://github.com/Rahul-peacenik159/abm-news-pulse) | news events per account from GDELT (funding, M&A, launches, leadership, layoffs) |
| **abm-tam-builder** (this repo) | build the account universe from Wikidata, OpenStreetMap and US Census data |
| [abm-dev-community-signals](https://github.com/Rahul-peacenik159/abm-dev-community-signals) | which companies' engineers engage with your or competitors' GitHub repos; HN mentions |
| [abm-market-timing](https://github.com/Rahul-peacenik159/abm-market-timing) | topic and region demand: Wikipedia attention, news volume, Google Trends in BigQuery |
| [abm-public-sector-buyers](https://github.com/Rahul-peacenik159/abm-public-sector-buyers) | government buyers: USAspending awards, incumbents, SAM.gov open opportunities |
| [abm-activation-kit](https://github.com/Rahul-peacenik159/abm-activation-kit) | combine all signals, score, and push to HubSpot, LinkedIn audiences and 1:1 pages |

## License

Apache-2.0 for the code. Data licenses: OpenStreetMap ODbL, Wikidata CC0, U.S. Census public domain.
