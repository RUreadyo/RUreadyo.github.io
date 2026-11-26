"""Monthly archive of the most upvoted Hugging Face Daily Papers, for the Trending-in-ML tab's month/year views.

Writes papers/data/trending.json = {updated, months: {"YYYY-MM": [paper, ...]}} (top N per month by upvotes).
Past months are fetched once; the current and previous month are refreshed on every run (upvotes keep growing).
Usage: python scripts/trending_archive.py [--since 2023-05] [--per-month 50] [--refresh]
"""
import argparse
import datetime as dt
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "papers" / "data" / "trending.json"


def fetch_month(m, n):
    for t in range(4):
        try:
            req = urllib.request.Request(f"https://huggingface.co/api/daily_papers?month={m}&limit=100",
                                         headers={"User-Agent": "Mozilla/5.0 (paper-digest)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.loads(r.read())
            break
        except Exception as e:
            print(f"  {m}: {e} (retry {t + 1})", file=sys.stderr)
            time.sleep(5 * (t + 1))
    else:
        return None
    items = []
    for r in res:
        p = r.get("paper", {})
        if not p.get("id"):
            continue
        summ = p.get("ai_summary") or " ".join(p.get("summary", "").split())
        items.append({
            "id": p["id"], "title": " ".join(p.get("title", "").split()),
            "authors": [a.get("name", "") for a in p.get("authors", [])][:12],
            "summary": summ[:320] + ("…" if len(summ) > 320 else ""),
            "upvotes": p.get("upvotes", 0), "github": p.get("githubRepo") or "", "stars": p.get("githubStars") or 0,
            "project": p.get("projectPage") or "", "org": (p.get("organization") or {}).get("fullname", ""),
            "url": f"https://arxiv.org/abs/{p['id']}", "pdf": f"https://arxiv.org/pdf/{p['id']}",
            "hf": f"https://huggingface.co/papers/{p['id']}", "day": (r.get("publishedAt") or p.get("publishedAt") or "")[:10] or f"{m}-01",
        })
    items.sort(key=lambda x: x["upvotes"], reverse=True)
    return items[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2023-05")
    ap.add_argument("--per-month", type=int, default=50)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    doc = json.loads(OUT.read_text()) if OUT.exists() else {"months": {}}
    today = dt.date.today()
    cur = today.strftime("%Y-%m")
    prev = (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    y, mo = map(int, a.since.split("-"))
    while f"{y:04d}-{mo:02d}" <= cur:
        m = f"{y:04d}-{mo:02d}"
        if a.refresh or m in (cur, prev) or m not in doc["months"]:
            items = fetch_month(m, a.per_month)
            if items:
                doc["months"][m] = items
                print(f"{m}: {len(items)} papers, top {items[0]['upvotes']} ▲ {items[0]['title'][:60]}")
            time.sleep(0.5)
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    doc["months"] = dict(sorted(doc["months"].items(), reverse=True))
    doc["updated"] = today.isoformat()
    OUT.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(doc['months'])} months)")


if __name__ == "__main__":
    main()
