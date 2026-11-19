"""Open data sources for building an account universe.

OpenStreetMap Overpass  https://overpass-api.de/api/interpreter   (free; fair use, one query at a time)
Wikidata SPARQL         https://query.wikidata.org/sparql         (free; descriptive User-Agent required)
US Census CBP           https://api.census.gov/data/{year}/cbp    (free; key optional)
"""
from __future__ import annotations

import os

from .common import Http, normalise_domain

OVERPASS = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
WIKIDATA = "https://query.wikidata.org/sparql"
CENSUS = "https://api.census.gov/data/{year}/cbp"

# websites that are listings / social profiles, not the company's own domain
NOT_OWN_DOMAINS = {"facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com", "booking.com",
                   "tripadvisor.com", "expedia.com", "hotels.com", "yelp.com", "google.com", "sites.google.com",
                   "linktr.ee", "wix.com", "squarespace.com", "business.site", "godaddysites.com", "opentable.com",
                   "doordash.com", "ubereats.com", "zocdoc.com", "marriott.com", "hilton.com", "ihg.com", "hyatt.com",
                   "wyndhamhotels.com", "choicehotels.com", "bestwestern.com", "accor.com"}

FIELDS = ["domain", "company", "brand", "operator", "city", "state", "country", "industry", "employees",
          "source", "source_id", "source_url", "website", "note"]


def client() -> Http:
    return Http("abm-tam-builder/0.1 (+https://github.com/Rahul-peacenik159/abm-tam-builder)", min_interval=1.0,
                timeout=180)


def own_domain(website: str) -> tuple[str, str]:
    """(domain, note). Chain-brand and listing domains are kept but flagged."""
    d = normalise_domain(website)
    if not d:
        return "", "no website listed"
    root = ".".join(d.split(".")[-2:])
    if d in NOT_OWN_DOMAINS or root in NOT_OWN_DOMAINS:
        return d, "website is a listing, social profile or chain brand site (property-level domain unknown)"
    return d, ""


# ------------------------------------------------------------------ OpenStreetMap
def overpass_query(area: str, tags: list[str], admin_level: str | None = None) -> str:
    area_filter = f'["name"="{area}"]["boundary"="administrative"]' + (f'["admin_level"="{admin_level}"]' if admin_level else "")
    parts = []
    for t in tags:
        k, _, v = t.partition("=")
        parts.append(f'  nwr["{k}"="{v}"](area.a);' if v else f'  nwr["{k}"](area.a);')
    return f'[out:json][timeout:180];\narea{area_filter}->.a;\n(\n' + "\n".join(parts) + "\n);\nout center tags;"


def osm_places(http: Http, area: str, tags: list[str], admin_level: str | None = None) -> list[dict]:
    q = overpass_query(area, tags, admin_level)
    data = http.request("POST", OVERPASS, data={"data": q})
    data.raise_for_status()
    return [osm_to_account(e, tags) for e in data.json().get("elements", []) if (e.get("tags") or {}).get("name")]


def osm_to_account(e: dict, tags: list[str]) -> dict:
    t = e.get("tags", {})
    website = t.get("website") or t.get("contact:website") or t.get("url") or ""
    domain, note = own_domain(website)
    return {"domain": domain, "company": t.get("name", ""), "brand": t.get("brand", ""), "operator": t.get("operator", ""),
            "city": t.get("addr:city", ""), "state": t.get("addr:state", ""), "country": t.get("addr:country", ""),
            "industry": ";".join(tags), "employees": "", "source": "openstreetmap", "source_id": f"{e['type']}/{e['id']}",
            "source_url": f"https://www.openstreetmap.org/{e['type']}/{e['id']}", "website": website, "note": note}


# ------------------------------------------------------------------ Wikidata
def wikidata_query(industries: list[str], instance_of: list[str], country: str | None, limit: int) -> str:
    match = []
    if industries:
        match.append("VALUES ?industry { " + " ".join(f"wd:{q}" for q in industries) + " } ?item wdt:P452 ?industry .")
    if instance_of:
        match.append("VALUES ?kind { " + " ".join(f"wd:{q}" for q in instance_of) + " } ?item wdt:P31/wdt:P279* ?kind .")
    country_line = f"?item wdt:P17 wd:{country} ." if country else "OPTIONAL { ?item wdt:P17 ?country . }"
    return f"""SELECT ?item ?itemLabel ?website ?employees ?countryLabel ?hqLabel WHERE {{
  {' '.join(match)}
  ?item wdt:P856 ?website .
  {country_line}
  OPTIONAL {{ ?item wdt:P1128 ?employees . }}
  OPTIONAL {{ ?item wdt:P159 ?hq . }}
  FILTER NOT EXISTS {{ ?item wdt:P576 ?dissolved . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}} LIMIT {int(limit)}"""


def wikidata_companies(http: Http, industries: list[str], instance_of: list[str], country: str | None,
                       limit: int = 2000, min_employees: int = 0, label: str = "") -> list[dict]:
    q = wikidata_query(industries, instance_of, country, limit)
    j = http.get_json(WIKIDATA, params={"query": q, "format": "json"},
                      headers={"Accept": "application/sparql-results+json"})
    return wikidata_rows(j, min_employees, label or ";".join(industries + instance_of))


def wikidata_rows(j: dict, min_employees: int, industry_label: str) -> list[dict]:
    best: dict[str, dict] = {}
    for b in j.get("results", {}).get("bindings", []):
        v = {k: x.get("value", "") for k, x in b.items()}
        qid = v["item"].rsplit("/", 1)[-1]
        emp = int(float(v["employees"])) if v.get("employees") else 0
        domain, note = own_domain(v.get("website", ""))
        row = {"domain": domain, "company": v.get("itemLabel", ""), "brand": "", "operator": "",
               "city": v.get("hqLabel", ""), "state": "", "country": v.get("countryLabel", ""),
               "industry": industry_label, "employees": emp or "", "source": "wikidata", "source_id": qid,
               "source_url": v["item"], "website": v.get("website", ""), "note": note}
        cur = best.get(qid)
        if cur is None or (emp or 0) > (cur["employees"] or 0):
            best[qid] = row
    return [r for r in best.values() if not min_employees or (r["employees"] or 0) >= min_employees]


# ------------------------------------------------------------------ US Census County Business Patterns
def census_cbp(http: Http, year: int, naics: str, state: str, geo: str = "county", naics_var: str = "NAICS2017") -> list[dict]:
    params = {"get": f"NAME,{naics_var}_LABEL,ESTAB,EMP,PAYANN", naics_var: naics}
    if geo == "state":
        params["for"] = f"state:{state}"
    else:
        params.update({"for": f"{geo}:*", "in": f"state:{state}"})
    if os.getenv("CENSUS_API_KEY"):
        params["key"] = os.environ["CENSUS_API_KEY"]
    rows = http.get_json(CENSUS.format(year=year), params=params)
    return census_rows(rows, naics_var)


def census_rows(rows: list[list], naics_var: str = "NAICS2017") -> list[dict]:
    header, data = rows[0], rows[1:]
    out = []
    for r in data:
        d = dict(zip(header, r))
        out.append({"geography": d.get("NAME", ""), "naics": d.get(naics_var, ""), "industry": d.get(f"{naics_var}_LABEL", ""),
                    "establishments": int(d.get("ESTAB") or 0), "employees": int(d.get("EMP") or 0),
                    "annual_payroll_k": int(d.get("PAYANN") or 0)})
    return sorted(out, key=lambda x: -x["establishments"])
