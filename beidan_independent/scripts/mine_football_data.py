"""Discover and archive linked current/previous season Football-Data CSVs."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen

from beidan_bd1.football_data_csv import audit_csv

ROOT = Path(__file__).resolve().parents[1]
CODES = {"E0", "E1", "E2", "E3", "D1", "D2", "F1", "F2", "SP1", "SP2",
         "I1", "I2", "N1", "P1", "B1", "SC0", "SC1"}
SEASONS = {"2526", "2627"}


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.urls.append(href)


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = ROOT / "data_sample/football_data_csv" / stamp
    folder.mkdir(parents=True)
    receipts, failures = [], []

    def fetch(url, relative):
        start = datetime.now(timezone.utc).isoformat()
        with urlopen(url, timeout=35) as response:
            raw = response.read()
            receipt = {"requested_url": url, "resolved_url": response.geturl(),
                "start_utc": start, "end_utc": datetime.now(timezone.utc).isoformat(),
                "http_status": response.status, "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(), "file": relative,
                "last_modified_header": response.headers.get("Last-Modified")}
        if receipt["http_status"] != 200:
            raise ValueError("HTTP status not 200")
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return raw, receipt

    base = "https://www.football-data.co.uk/data.php"
    raw, receipt = fetch(base, "discovery/data.html")
    receipts.append(receipt)
    links = Links(); links.feed(raw.decode("utf-8", errors="strict"))
    pages = sorted({urljoin(receipt["resolved_url"], u) for u in links.urls
                    if re.fullmatch(r"(?:https?://(?:www\.)?football-data\.co\.uk/)?[a-z]+m\.php", u)
                    and u.split("/")[-1] not in {"downloadm.php"}})
    csvs = {}
    for page in pages:
        try:
            raw, receipt = fetch(page, "discovery/" + urlparse(page).path.rsplit("/", 1)[-1] + ".html")
            receipts.append(receipt)
            parsed = Links(); parsed.feed(raw.decode("utf-8", errors="strict"))
            for href in parsed.urls:
                url = urljoin(receipt["resolved_url"], href)
                match = re.search(r"/mmz4281/([0-9]{4})/([A-Z]+[0-9]?)\.csv$", url)
                if match and match[1] in SEASONS and match[2] in CODES:
                    csvs[url] = {"season_code": match[1], "league_code": match[2], "discovery_page": page}
        except Exception as exc:
            failures.append({"url": page, "stage": "discovery", "reason": str(exc)})
    try:
        _, receipt = fetch("https://www.football-data.co.uk/notes.txt", "discovery/notes.txt")
        receipts.append(receipt)
    except Exception as exc:
        failures.append({"url": "https://www.football-data.co.uk/notes.txt", "reason": str(exc)})
    results, audits = [], []

    def download(url, metadata):
        filename = f"raw/{metadata['season_code']}/{metadata['league_code']}.csv"
        raw, receipt = fetch(url, filename)
        rows, audit = audit_csv(raw, source_url=url, verified_at=receipt["end_utc"],
                               league_code=metadata["league_code"], season_code=metadata["season_code"])
        return rows, {**metadata, **audit, "file": filename}, receipt

    with ThreadPoolExecutor(max_workers=3) as executor:
        jobs = {executor.submit(download, u, m): u for u, m in csvs.items()}
        for future in as_completed(jobs):
            try:
                rows, audit, receipt = future.result()
                results.extend(rows); audits.append(audit); receipts.append(receipt)
                print(json.dumps({"file": audit["file"], "accepted": len(rows),
                                  "explicit_ht": audit["explicit_ht_n"]}), flush=True)
            except Exception as exc:
                failures.append({"url": jobs[future], "stage": "csv", "reason": str(exc)})
    results.sort(key=lambda r: (r["league_code"], r["season_code"], r["source_row"]))
    with (folder / "score_observations.jsonl").open("w") as out:
        for row in results:
            out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    report = {"collected_at": datetime.now(timezone.utc).isoformat(),
        "files_discovered_n": len(csvs), "files_audited_n": len(audits),
        "score_observations_n": len(results), "explicit_ht_n": sum(a["explicit_ht_n"] for a in audits),
        "requested_codes": sorted(CODES), "requested_seasons": sorted(SEASONS),
        "missing_code_seasons": sorted(f"{s}/{c}" for c in CODES for s in SEASONS
            if not any(a["league_code"] == c and a["season_code"] == s for a in audits)),
        "audits": sorted(audits, key=lambda r: r["file"]), "failures": failures,
        "identity_or_odds_imported": False, "historical_backtest_eligible": False}
    (folder / "AUDIT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (folder / "RECEIPTS.json").write_text(json.dumps(receipts, ensure_ascii=False, indent=2))
    print(json.dumps({"folder": str(folder), **{k:v for k,v in report.items() if k not in {"audits", "failures"}},
                      "failures_n": len(failures)}), flush=True)


if __name__ == "__main__":
    main()
