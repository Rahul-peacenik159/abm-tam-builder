"""python -m tam_builder build --preset hotels-dallas
python -m tam_builder build --source osm --area "Austin" --tag amenity=restaurant
python -m tam_builder build --source wikidata --industry Q880371 --country Q30 --min-employees 50
python -m tam_builder build --source census --naics 721110 --state 48
python -m tam_builder merge out/accounts.csv other.csv --exclude customers.csv
python -m tam_builder summary out"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from . import sources
from .common import load_yaml, normalise_domain, now, summary_or_note, write_csv

ROOT = Path(__file__).resolve().parent.parent
MARKET_FIELDS = ["geography", "naics", "industry", "establishments", "employees", "annual_payroll_k"]


def _resolve(args) -> dict:
    spec = {}
    if args.preset:
        presets = load_yaml(ROOT / "config" / "presets.yaml")
        if args.preset not in presets:
            sys.exit(f"Unknown preset '{args.preset}'. Available: {', '.join(presets)}")
        spec = dict(presets[args.preset])
    for k in ("source", "area", "admin_level", "country", "naics", "state", "year", "limit", "min_employees", "naics_var"):
        v = getattr(args, k, None)
        if v not in (None, "", []):
            spec[k] = v
    if args.tag:
        spec["tags"] = args.tag
    if args.industry:
        spec["industries"] = args.industry
    if args.instance_of:
        spec["instance_of"] = args.instance_of
    if not spec.get("source"):
        sys.exit("Pass --preset or --source osm|wikidata|census")
    return spec


def dedupe(rows: list[dict]) -> list[dict]:
    """One row per own domain; rows without an own domain are kept (flagged in `note`)."""
    out, seen = [], {}
    for r in rows:
        key = r["domain"] if r["domain"] and not r.get("note") else f"{r['source']}:{r['source_id']}"
        if key in seen:
            seen[key]["note"] = (seen[key]["note"] + f"; also {r['company']}").strip("; ")[:300]
            continue
        seen[key] = r
        out.append(r)
    return out


def cmd_build(args) -> None:
    spec = _resolve(args)
    http = sources.client()
    out = Path(args.out)
    (out / "snapshots").mkdir(parents=True, exist_ok=True)  # keeps the GitHub cache step quiet
    info = {"repo": "abm-tam-builder", "run_at": now().isoformat(), "spec": spec}
    if spec["source"] == "census":
        rows = sources.census_cbp(http, int(spec.get("year", 2021)), str(spec["naics"]), str(spec["state"]),
                                  spec.get("geo", "county"), spec.get("naics_var", "NAICS2017"))
        write_csv(out / "market_size.csv", rows, MARKET_FIELDS)
        info.update(establishments=sum(r["establishments"] for r in rows), employees=sum(r["employees"] for r in rows),
                    geographies=len(rows))
        print(f"{info['establishments']:,} establishments, {info['employees']:,} employees across {len(rows)} "
              f"geographies -> {out}/market_size.csv")
    else:
        if spec["source"] == "osm":
            if not spec.get("area") or not spec.get("tags"):
                sys.exit("OSM needs --area and at least one --tag key=value")
            rows = sources.osm_places(http, spec["area"], spec["tags"], spec.get("admin_level"))
        elif spec["source"] == "wikidata":
            if not spec.get("industries") and not spec.get("instance_of"):
                sys.exit("Wikidata needs --industry Q... and/or --instance-of Q...")
            rows = sources.wikidata_companies(http, spec.get("industries", []), spec.get("instance_of", []),
                                              spec.get("country"), int(spec.get("limit", 2000)),
                                              int(spec.get("min_employees", 0)), args.label or args.preset or "")
        else:
            sys.exit(f"Unknown source {spec['source']}")
        rows = dedupe(rows)
        write_csv(out / "accounts.csv", rows, sources.FIELDS)
        with_domain = sum(1 for r in rows if r["domain"] and not r["note"])
        info.update(accounts=len(rows), with_own_domain=with_domain,
                    top_brands=Counter(r["brand"] for r in rows if r["brand"]).most_common(10))
        print(f"{len(rows)} accounts ({with_domain} with their own website domain) -> {out}/accounts.csv")
    (out / "run_info.json").write_text(json.dumps(info, indent=2, default=str))


def cmd_merge(args) -> None:
    exclude = set()
    for f in args.exclude or []:
        with open(f, newline="", encoding="utf-8-sig") as fh:
            exclude |= {normalise_domain(r.get("domain") or r.get("website") or "") for r in csv.DictReader(fh)}
    rows = []
    for f in args.files:
        with open(f, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                r = {k: r.get(k, "") for k in sources.FIELDS}
                r["domain"] = normalise_domain(r["domain"])
                rows.append(r)
    before = len(rows)
    rows = [r for r in dedupe(rows) if not (r["domain"] and r["domain"] in exclude)]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    write_csv(Path(args.out), rows, sources.FIELDS)
    print(f"{before} rows in, {len(rows)} accounts out (duplicates and {len(exclude)} excluded domains removed) -> {args.out}")


def cmd_summary(args) -> None:
    out = Path(args.out)
    info = json.loads((out / "run_info.json").read_text()) if (out / "run_info.json").exists() else {}
    if (out / "market_size.csv").exists():
        print(summary_or_note(out / "market_size.csv", [("geography", "Geography"), ("industry", "Industry"),
                                                        ("establishments", "Establishments"), ("employees", "Employees")],
                              f"Market size: {info.get('establishments', 0):,} establishments", top=25))
    if (out / "accounts.csv").exists():
        print(summary_or_note(out / "accounts.csv", [("company", "Company"), ("domain", "Domain"), ("city", "City"),
                                                     ("brand", "Brand"), ("employees", "Employees"), ("note", "Note")],
                              f"Account universe: {info.get('accounts', 0)} accounts, "
                              f"{info.get('with_own_domain', 0)} with their own domain", top=40))


def _safe_build(args) -> None:
    try:
        cmd_build(args)
    except SystemExit:
        raise
    except Exception as exc:
        sys.exit(f"Source unavailable ({type(exc).__name__}: {str(exc)[:160]}). "
                 "Public Overpass/Wikidata/Census servers are sometimes busy: try again in a few minutes.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="tam_builder", description="Build an account universe from open data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--preset", help="name from config/presets.yaml")
    b.add_argument("--source", choices=["osm", "wikidata", "census"])
    b.add_argument("--area", help="OSM: administrative area name, e.g. Dallas")
    b.add_argument("--admin-level", dest="admin_level", help="OSM: admin_level to disambiguate the area")
    b.add_argument("--tag", action="append", help="OSM: key=value, repeatable (tourism=hotel)")
    b.add_argument("--industry", action="append", help="Wikidata: industry Q-id (P452), repeatable")
    b.add_argument("--instance-of", dest="instance_of", action="append", help="Wikidata: type Q-id (P31), repeatable")
    b.add_argument("--country", help="Wikidata: country Q-id, e.g. Q30 (USA)")
    b.add_argument("--min-employees", dest="min_employees", type=int)
    b.add_argument("--limit", type=int)
    b.add_argument("--naics", help="Census: NAICS code")
    b.add_argument("--state", help="Census: state FIPS code, e.g. 48 for Texas")
    b.add_argument("--year", type=int)
    b.add_argument("--naics-var", dest="naics_var", help="Census: NAICS2017 (default) or NAICS2022")
    b.add_argument("--label", help="industry label written to the accounts file")
    b.add_argument("--out", default="out")
    b.set_defaults(func=_safe_build)
    m = sub.add_parser("merge", help="combine account files, dedupe by domain, drop excluded domains")
    m.add_argument("files", nargs="+")
    m.add_argument("--exclude", action="append", help="CSV of domains to drop (customers, open deals)")
    m.add_argument("--out", default="out/accounts_merged.csv")
    m.set_defaults(func=cmd_merge)
    s = sub.add_parser("summary")
    s.add_argument("out", nargs="?", default="out")
    s.set_defaults(func=cmd_summary)
    args = ap.parse_args(argv)
    args.func(args)
