import csv
import json
from pathlib import Path

from tam_builder import cli, sources

FX = Path(__file__).resolve().parent / "fixtures"


def load(n):
    return json.loads((FX / n).read_text())


def test_overpass_query():
    q = sources.overpass_query("Dallas", ["tourism=hotel", "amenity"])
    assert 'area["name"="Dallas"]["boundary"="administrative"]->.a;' in q
    assert 'nwr["tourism"="hotel"](area.a);' in q and 'nwr["amenity"](area.a);' in q


def test_osm_rows_and_dedupe():
    rows = [sources.osm_to_account(e, ["tourism=hotel"]) for e in load("overpass.json")["elements"] if e["tags"].get("name")]
    assert rows[0]["domain"] == "adolphus.com" and rows[0]["city"] == "Dallas"
    assert rows[1]["domain"] == "hilton.com" and "chain brand" in rows[1]["note"]
    assert rows[2]["note"] == "no website listed"
    deduped = cli.dedupe(rows)
    assert len(deduped) == 3 and "also Adolphus Annex" in deduped[0]["note"]


def test_wikidata_rows():
    rows = sources.wikidata_rows(load("wikidata.json"), min_employees=50, industry_label="software")
    assert len(rows) == 1 and rows[0]["employees"] == 1200 and rows[0]["source_id"] == "Q1"
    q = sources.wikidata_query(["Q880371"], [], "Q30", 100)
    assert "wdt:P452" in q and "wd:Q30" in q and "LIMIT 100" in q


def test_census_rows():
    rows = sources.census_rows(load("census.json"))
    assert rows[0]["geography"] == "Dallas County, Texas" and rows[0]["establishments"] == 312


def test_cli_build_and_merge(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "client", lambda: None)
    monkeypatch.setattr(sources, "osm_places", lambda http, area, tags, lvl=None: [
        sources.osm_to_account(e, tags) for e in load("overpass.json")["elements"] if e["tags"].get("name")])
    monkeypatch.setattr(sources, "census_cbp", lambda http, y, n, s, g="county", v="NAICS2017": sources.census_rows(load("census.json")))
    out = tmp_path / "out"
    cli.main(["build", "--preset", "hotels-dallas", "--out", str(out)])
    rows = list(csv.DictReader(open(out / "accounts.csv")))
    assert len(rows) == 3
    cli.main(["build", "--preset", "hotels-texas-market-size", "--out", str(out)])
    assert json.loads((out / "run_info.json").read_text())["establishments"] == 572
    excl = tmp_path / "customers.csv"
    excl.write_text("domain\nadolphus.com\n")
    cli.main(["merge", str(out / "accounts.csv"), "--exclude", str(excl), "--out", str(tmp_path / "m.csv")])
    merged = list(csv.DictReader(open(tmp_path / "m.csv")))
    assert all(r["domain"] != "adolphus.com" for r in merged) and len(merged) == 2
