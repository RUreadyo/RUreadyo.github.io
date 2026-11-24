#!/usr/bin/env python3
"""Daily robot-learning paper digest for rureadyo.github.io/papers/.

1. Pulls recent arXiv papers (all of cs.RO + keyword-filtered cs.LG/cs.CV/cs.AI/...).
2. Scores them with topic keywords + tracked authors (papers/config.json).
3. If ANTHROPIC_API_KEY is set, asks Claude to rank the top candidates against
   papers/interests.md and write a one-line TL;DR + why-it-matters for each.
   Without a key (or if the call fails) it falls back to keyword ranking.
4. Writes papers/data/<date>.json and updates papers/data/index.json + seen.json.

Usage: python scripts/fetch_papers.py [--date YYYY-MM-DD] [--no-llm] [--dry-run]
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
DATA = PAPERS / "data"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
API = "https://export.arxiv.org/api/query"
MODEL = "claude-opus-5-5"


# --------------------------------------------------------------------------- arXiv
def arxiv_query(search: str, max_results: int) -> list[dict]:
    params = {
        "search_query": search,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": max_results,
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "paper-digest/1.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                xml = r.read()
            break
        except Exception as e:  # arXiv is flaky; back off and retry
            print(f"arXiv request failed ({e}); retry {attempt + 1}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError("arXiv API unreachable")

    out = []
    for e in ET.fromstring(xml).findall(f"{ATOM}entry"):
        raw_id = e.findtext(f"{ATOM}id", "")
        m = re.search(r"abs/([^v]+)(v\d+)?$", raw_id)
        if not m:
            continue
        aid = m.group(1)
        comment = (e.findtext(f"{ARXIV_NS}comment") or "").strip()
        proj = re.search(r"https?://[^\s,;)]+", comment)
        out.append({
            "id": aid,
            "title": " ".join(e.findtext(f"{ATOM}title", "").split()),
            "abstract": " ".join(e.findtext(f"{ATOM}summary", "").split()),
            "authors": [a.findtext(f"{ATOM}name", "") for a in e.findall(f"{ATOM}author")],
            "published": e.findtext(f"{ATOM}published", "")[:10],
            "primary": (e.find(f"{ARXIV_NS}primary_category").get("term")
                        if e.find(f"{ARXIV_NS}primary_category") is not None else ""),
            "categories": [c.get("term") for c in e.findall(f"{ATOM}category")],
            "comment": comment,
            "project": proj.group(0).rstrip(".") if proj else "",
            "url": f"https://arxiv.org/abs/{aid}",
            "pdf": f"https://arxiv.org/pdf/{aid}",
        })
    return out


def fetch_recent(cfg: dict, since: dt.date) -> list[dict]:
    full = " OR ".join(f"cat:{c}" for c in cfg["categories_full"])
    cats = " OR ".join(f"cat:{c}" for c in cfg["categories_filtered"])
    terms = " OR ".join(f'abs:"{t}"' for t in cfg["filtered_query_terms"])
    queries = [(f"({full})", 600), (f"({cats}) AND ({terms})", 800)]
    papers: dict[str, dict] = {}
    for q, n in queries:
        for p in arxiv_query(q, n):
            if dt.date.fromisoformat(p["published"]) >= since:
                papers.setdefault(p["id"], p)
        time.sleep(3)  # arXiv asks for >=3s between calls
    return list(papers.values())


# --------------------------------------------------------------------------- scoring
def compile_topics(cfg):
    comp = {}
    for key, t in cfg["topics"].items():
        comp[key] = [(re.compile(r"(?<![a-z0-9])" + re.escape(k.lower()) + r"(?![a-z0-9])"), w)
                     for k, w in t["keywords"].items()]
    return comp


def keyword_score(p, comp, tracked):
    title, abst = p["title"].lower(), p["abstract"].lower()
    per_topic = {}
    for key, pats in comp.items():
        s = 0
        for rx, w in pats:
            if rx.search(title):
                s += 2 * w
            elif rx.search(abst):
                s += w
        if s:
            per_topic[key] = s
    hits = [a for a in p["authors"] if a in tracked]
    score = sum(per_topic.values()) + 8 * len(hits) + (2 if "cs.RO" in p["categories"] else 0)
    p["topics"] = sorted(per_topic, key=per_topic.get, reverse=True)[:3]
    p["tracked_authors"] = hits
    p["kw_score"] = score
    return score


def first_sentence(text, limit=260):
    s = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


# --------------------------------------------------------------------------- Claude ranking
def llm_rank(cands, cfg):
    import anthropic

    interests = (PAPERS / "interests.md").read_text()
    topic_keys = list(cfg["topics"]) + ["Other"]
    topic_doc = "\n".join(f"- {k}: {t['label']}" for k, t in cfg["topics"].items())
    listing = "\n\n".join(
        f"[{p['id']}] {p['title']}\nAuthors: {', '.join(p['authors'][:12])}"
        f"{' et al.' if len(p['authors']) > 12 else ''}\nCategories: {', '.join(p['categories'])}\n"
        f"Abstract: {p['abstract']}"
        for p in cands
    )
    schema = {
        "type": "object",
        "properties": {
            "papers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "relevance": {"type": "integer", "description": "0-10 fit to the reader's interests"},
                        "quality": {"type": "integer", "description": "0-10 estimated significance/insight"},
                        "topic": {"type": "string", "enum": topic_keys},
                        "tldr": {"type": "string", "description": "one sentence, <=30 words, what they did"},
                        "why": {"type": "string", "description": "one short sentence on why it matters to this reader"},
                    },
                    "required": ["id", "relevance", "quality", "topic", "tldr", "why"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["papers"],
        "additionalProperties": False,
    }
    system = (
        "You curate a daily arXiv digest for one robot-learning researcher. "
        "Judge each paper against the reader's profile below. Be discerning: most papers should score "
        "relevance <=5; reserve 8-10 for papers squarely in the core interests or likely to be important. "
        "Write TL;DRs that state the concrete method/finding, not marketing language.\n\n"
        f"Topic keys:\n{topic_doc}\n- Other: anything else\n\nReader profile:\n{interests}"
    )
    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
        system=system,
        messages=[{"role": "user", "content": f"Rate every paper below (return one entry per id).\n\n{listing}"}],
    ) as stream:
        resp = stream.get_final_message()
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude declined the ranking request")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("ranking output was truncated")
    text = next(b.text for b in resp.content if b.type == "text")
    return {r["id"]: r for r in json.loads(text)["papers"]}


# --------------------------------------------------------------------------- main
def slim(p):
    keep = ["id", "title", "authors", "abstract", "url", "pdf", "project", "primary", "categories",
            "published", "topics", "tracked_authors", "kw_score", "relevance", "quality", "tldr", "why"]
    return {k: p[k] for k in keep if k in p}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = json.loads((PAPERS / "config.json").read_text())
    DATA.mkdir(parents=True, exist_ok=True)
    seen_path = DATA / "seen.json"
    seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()

    today = dt.date.fromisoformat(args.date)
    since = today - dt.timedelta(days=cfg["lookback_days"])
    papers = [p for p in fetch_recent(cfg, since) if p["id"] not in seen]
    print(f"{len(papers)} new papers since {since}")
    if not papers:
        print("nothing new; exiting")
        return

    comp = compile_topics(cfg)
    tracked = set(cfg["tracked_authors"])
    for p in papers:
        keyword_score(p, comp, tracked)
    relevant = [p for p in papers if p["kw_score"] > 2 or p["tracked_authors"]]
    relevant.sort(key=lambda p: p["kw_score"], reverse=True)
    cands = relevant[: cfg["max_candidates_for_llm"]]
    cands += [p for p in relevant[cfg["max_candidates_for_llm"]:] if p["tracked_authors"]]

    ranker = "keywords"
    if not args.no_llm and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            ratings = llm_rank(cands, cfg)
            for p in cands:
                r = ratings.get(p["id"])
                if r:
                    p.update(relevance=r["relevance"], quality=r["quality"], tldr=r["tldr"], why=r["why"])
                    if r["topic"] != "Other":
                        p["topics"] = [r["topic"]] + [t for t in p["topics"] if t != r["topic"]]
            ranker = MODEL
        except Exception as e:
            print(f"LLM ranking failed, falling back to keywords: {e!r}", file=sys.stderr)

    if ranker == MODEL:
        rated = [p for p in cands if "relevance" in p]
        key = lambda p: (p["relevance"] * 2 + p["quality"], p["kw_score"])
        rated.sort(key=key, reverse=True)
        top = [p for p in rated if p["relevance"] >= 7][: cfg["top_picks"]]
        rest = [p for p in rated if p not in top and p["relevance"] >= 4]
        rest += [p for p in relevant if "relevance" not in p and "cs.RO" in p["categories"]]
    else:
        for p in relevant:
            p["tldr"] = first_sentence(p["abstract"])
        top = [p for p in relevant if p["kw_score"] >= 10][: cfg["top_picks"]]
        rest = [p for p in relevant if p not in top]
    roundup = rest[: cfg["roundup_size"]]

    n_ro = sum("cs.RO" in p["categories"] for p in papers)
    digest = {
        "date": today.isoformat(),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "ranker": ranker,
        "stats": {"new_papers": len(papers), "cs_ro": n_ro, "candidates": len(cands)},
        "top_picks": [slim(p) for p in top],
        "roundup": [slim(p) for p in roundup],
    }
    if args.dry_run:
        for p in top:
            print(f"* [{p.get('relevance', p['kw_score'])}] {p['title']}  ({', '.join(p['topics'])})")
        print(f"... +{len(roundup)} roundup")
        return

    (DATA / f"{today.isoformat()}.json").write_text(json.dumps(digest, indent=1, ensure_ascii=False))
    idx_path = DATA / "index.json"
    idx = json.loads(idx_path.read_text()) if idx_path.exists() else []
    idx = [d for d in idx if d["date"] != today.isoformat()]
    idx.append({"date": today.isoformat(), "top": len(top), "roundup": len(roundup), "ranker": ranker})
    idx.sort(key=lambda d: d["date"], reverse=True)
    idx_path.write_text(json.dumps(idx, indent=1))
    seen |= {p["id"] for p in papers}
    seen_path.write_text(json.dumps(sorted(seen)[-20000:]))
    print(f"wrote {today}: {len(top)} top picks, {len(roundup)} roundup ({ranker})")


if __name__ == "__main__":
    main()
