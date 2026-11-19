"""Shared helpers for the open ABM bundles (an identical copy lives in every abm-* repo).

The signal row format is the same as abm-tech-footprint, so outputs from all
repos can be loaded into one BigQuery `abm.signals` table or combined by
abm-activation-kit.
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

SIGNAL_FIELDS = [
    "account_domain", "signal_type", "signal_value", "tool_name", "category", "role",
    "weight", "play", "source", "evidence", "evidence_url", "pack", "observed_at",
]


# ------------------------------------------------------------------ time
def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def now_iso() -> str:
    return now().isoformat()


def days_ago(days: int) -> datetime:
    return now() - timedelta(days=days)


def parse_dt(value) -> datetime | None:
    """ISO strings, 'YYYYMMDDTHHMMSSZ' (GDELT), epoch milliseconds (Lever)."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
    s = str(value).strip()
    m = re.fullmatch(r"(\d{8})T(\d{6})Z", s)
    if m:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ http
class Http:
    """requests.Session with a polite User-Agent, retries and a per-host minimum interval."""

    def __init__(self, user_agent: str, min_interval: float = 0.0, timeout: float = 30.0, retries: int = 3):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": os.getenv("ABM_USER_AGENT", user_agent)})
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self._last: dict[str, float] = {}

    def _wait(self, url: str, interval: float | None):
        host = urlparse(url).hostname or ""
        gap = self.min_interval if interval is None else interval
        elapsed = time.monotonic() - self._last.get(host, 0)
        if gap and elapsed < gap:
            time.sleep(gap - elapsed)
        self._last[host] = time.monotonic()

    def request(self, method: str, url: str, interval: float | None = None, **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        conn_failures = 0
        for attempt in range(self.retries + 1):
            self._wait(url, interval)
            try:
                r = self.session.request(method, url, **kw)
            except requests.RequestException:
                conn_failures += 1
                if conn_failures > 1:  # network problems: one retry, then give up quickly
                    raise
                time.sleep(2)
                continue
            if r.status_code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                return r
            retry_after = r.headers.get("Retry-After", "")
            time.sleep(float(retry_after) if retry_after.isdigit() else 2 ** attempt * 2)
        raise requests.RequestException(f"giving up on {url}")

    def get_json(self, url: str, **kw):
        r = self.request("GET", url, **kw)
        r.raise_for_status()
        return r.json()

    def post_json(self, url: str, payload: dict, **kw):
        r = self.request("POST", url, json=payload, **kw)
        r.raise_for_status()
        return r.json()

    def get_text(self, url: str, **kw) -> str:
        r = self.request("GET", url, **kw)
        r.raise_for_status()
        return r.text


# ------------------------------------------------------------------ accounts
def normalise_domain(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = "http://" + value
    host = urlparse(value).hostname or ""
    return host[4:] if host.startswith("www.") else host


def read_accounts(path: str | None = None, domains: list[str] | None = None) -> list[dict]:
    """Rows from a CSV (needs a `domain` column; other columns are kept) and/or bare domains."""
    rows: list[dict] = []
    if path:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
                r["domain"] = normalise_domain(r.get("domain") or r.get("website") or "")
                rows.append(r)
    for d in domains or []:
        rows.append({"domain": normalise_domain(d)})
    seen, out = set(), []
    for r in rows:
        key = r["domain"] or r.get("company", "").lower()
        if key and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def load_yaml(path: str | Path) -> dict:
    import yaml

    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ------------------------------------------------------------------ signals
def signal(account_domain: str, signal_type: str, signal_value: str = "", *, pack: str, tool_name: str = "",
           category: str = "", role: str = "neutral", weight: int = 0, play: str = "", source: str = "",
           evidence: str = "", evidence_url: str = "", observed_at: str | None = None) -> dict:
    return {
        "account_domain": account_domain, "signal_type": signal_type, "signal_value": str(signal_value)[:300],
        "tool_name": tool_name, "category": category, "role": role, "weight": int(weight), "play": play,
        "source": source, "evidence": str(evidence)[:500], "evidence_url": evidence_url, "pack": pack,
        "observed_at": observed_at or now_iso(),
    }


def score_rows(rows: list[dict]) -> tuple[int, str]:
    """Sum of weights capped at 100, and the play of the strongest signal."""
    scored = sorted((r for r in rows if int(r["weight"]) > 0), key=lambda r: -int(r["weight"]))
    return min(100, sum(int(r["weight"]) for r in scored)), (scored[0]["play"] if scored else "")


# ------------------------------------------------------------------ snapshots (what changed since last run)
class Snapshots:
    """Per-pack state files: out/snapshots/<name>/<timestamp>.json  ->  {account: [keys]}"""

    def __init__(self, out_dir: str | Path, name: str):
        self.dir = Path(out_dir) / "snapshots" / name
        self.dir.mkdir(parents=True, exist_ok=True)
        files = sorted(self.dir.glob("*.json"))
        self.previous_name = files[-1].stem if files else None
        self.previous: dict[str, set] | None = (
            {k: set(v) for k, v in json.loads(files[-1].read_text(encoding="utf-8")).items()} if files else None)
        self.current: dict[str, list] = {}

    def seen_before(self, account: str) -> set | None:
        if self.previous is None:
            return None
        return self.previous.get(account, set())

    def record(self, account: str, keys) -> None:
        self.current[account] = sorted(set(keys))

    def save(self) -> str:
        stamp = now().strftime("%Y-%m-%dT%H%M%SZ")
        (self.dir / f"{stamp}.json").write_text(json.dumps(self.current), encoding="utf-8")
        return stamp


# ------------------------------------------------------------------ outputs
def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_outputs(out_dir: str | Path, rows: list[dict], summaries: list[dict], summary_fields: list[str],
                  run_info: dict, extra_files: dict[str, tuple[list[dict], list[str]]] | None = None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "signals.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    write_csv(out / "signals.csv", rows, SIGNAL_FIELDS)
    summaries = sorted(summaries, key=lambda s: -int(s.get("score", 0) or 0))
    write_csv(out / "accounts_summary.csv", summaries, summary_fields)
    for name, (data, fields) in (extra_files or {}).items():
        write_csv(out / name, data, fields)
    (out / "run_info.json").write_text(json.dumps(run_info, indent=2, default=str), encoding="utf-8")
    return out


def summary_markdown(csv_path: str | Path, columns: list[tuple[str, str]], title: str, top: int = 50) -> str:
    path = Path(csv_path)
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    cols = [(k, label) for k, label in columns if rows and k in rows[0] and any(r.get(k) for r in rows)]
    lines = [f"### {title}", "", f"{len(rows)} rows.", ""]
    if cols:
        lines.append("| " + " | ".join(label for _, label in cols) + " |")
        lines.append("|" + "---|" * len(cols))
        for r in rows[:top]:
            cells = []
            for k, _ in cols:
                c = (r.get(k) or "").replace("|", "/").replace("\n", " ")
                cells.append(c if len(c) <= 140 else c[:137] + "...")
            lines.append("| " + " | ".join(cells) + " |")
    info_file = path.parent / "run_info.json"
    if info_file.exists():
        info = json.loads(info_file.read_text(encoding="utf-8"))
        lines.append("")
        if info.get("previous_run"):
            lines.append(f"Compared with previous run `{info['previous_run']}`: {info.get('new_items', 0)} new item(s).")
        else:
            lines.append("First run: the next run will report what is new since this one.")
        if info.get("errors"):
            lines.append(f"{info['errors']} account(s) had errors; see the `errors` column in accounts_summary.csv.")
    return "\n".join(lines) + "\n"


def strip_html(html: str) -> str:
    import html as h

    text = re.sub(r"<[^>]+>", " ", h.unescape(h.unescape(html or "")))
    return re.sub(r"\s+", " ", text).strip()


def summary_or_note(csv_path: str | Path, columns: list[tuple[str, str]], title: str, top: int = 50) -> str:
    """summary_markdown(), or a short note when the run produced no table."""
    if not Path(csv_path).exists():
        return f"### {title}\n\nNo `{Path(csv_path).name}` in this run.\n"
    return summary_markdown(csv_path, columns, title, top)
