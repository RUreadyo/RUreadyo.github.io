#!/usr/bin/env python3
"""Daily robot-learning paper digest for rureadyo.github.io/papers/.

Pipeline (one run):
  1. Pull arXiv papers submitted in the last `lookback_days` (or --backfill N days):
     all of cs.RO + keyword-filtered cs.LG/cs.CV/cs.AI/stat.ML/eess.SY.
  2. Score each paper with topic keywords + followed authors (papers/config.json).
  3. Rank & summarize the top candidates of each day with Claude (needs ANTHROPIC_API_KEY),
     caching results in papers/data/cache/ratings.json so nothing is rated twice.
     Without a key, falls back to keyword ranking + extractive summaries.
  4. Add trending general-ML papers from Hugging Face Daily Papers (upvotes, AI summary, code).
  5. Look up venues / citation counts on Semantic Scholar (+ "Accepted to X" in arXiv comments).
  6. Write papers/data/days/<date>.json (grouped by arXiv submission date), index.json, best.json.

Usage:
  python scripts/fetch_papers.py                    # daily run
  python scripts/fetch_papers.py --backfill 30      # rebuild the last 30 days
  python scripts/fetch_papers.py --export-candidates out.json   # dump unrated candidates
  python scripts/fetch_papers.py --import-ratings r.json        # merge externally made ratings
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
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
DATA = PAPERS / "data"
DAYS = DATA / "days"
CACHE = DATA / "cache"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
MODEL = "claude-opus-5-5"
UA = {"User-Agent": "paper-digest/2.0"}


def http_json(url, data=None, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None,
                                         headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:
            print(f"  request failed ({e}); retry {i + 1}", file=sys.stderr)
            time.sleep(4 * (i + 1))
    return None


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def dump(path, obj, indent=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=indent, ensure_ascii=False))


# --------------------------------------------------------------------------- arXiv
def parse_entries(xml):
    out = []
    for e in ET.fromstring(xml).findall(f"{ATOM}entry"):
        m = re.search(r"abs/([^v]+)(v\d+)?$", e.findtext(f"{ATOM}id", ""))
        if not m:
            continue
        aid = m.group(1)
        comment = " ".join((e.findtext(f"{ARXIV_NS}comment") or "").split())
        jref = " ".join((e.findtext(f"{ARXIV_NS}journal_ref") or "").split())
        links = re.findall(r"https?://[^\s,;)]+", comment)
        prim = e.find(f"{ARXIV_NS}primary_category")
        out.append({
            "id": aid,
            "title": " ".join(e.findtext(f"{ATOM}title", "").split()),
            "abstract": " ".join(e.findtext(f"{ATOM}summary", "").split()),
            "authors": [a.findtext(f"{ATOM}name", "") for a in e.findall(f"{ATOM}author")],
            "published": e.findtext(f"{ATOM}published", "")[:10],
            "primary": prim.get("term") if prim is not None else "",
            "categories": [c.get("term") for c in e.findall(f"{ATOM}category")],
            "comment": comment,
            "journal_ref": jref,
            "project": next((l.rstrip(".") for l in links if "github.com" not in l), ""),
            "code": next((l.rstrip(".") for l in links if "github.com" in l), ""),
            "url": f"https://arxiv.org/abs/{aid}",
            "pdf": f"https://arxiv.org/pdf/{aid}",
        })
    return out


def arxiv_search(query, since, until, page=1000):
    rng = f"submittedDate:[{since:%Y%m%d}0000 TO {until:%Y%m%d}2359]"
    out, start = [], 0
    while True:
        params = {"search_query": f"({query}) AND {rng}", "sortBy": "submittedDate",
                  "sortOrder": "descending", "start": start, "max_results": page}
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
        for i in range(5):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                    batch = parse_entries(r.read())
                break
            except Exception as e:
                print(f"  arXiv failed ({e}); retry {i + 1}", file=sys.stderr)
                time.sleep(6 * (i + 1))
        else:
            raise RuntimeError("arXiv API unreachable")
        out += batch
        print(f"  arXiv {query[:40]}… start={start}: {len(batch)}")
        if len(batch) < page:
            return out
        start += page
        time.sleep(3)


def fetch_window(cfg, since, until):
    full = " OR ".join(f"cat:{c}" for c in cfg["categories_full"])
    cats = " OR ".join(f"cat:{c}" for c in cfg["categories_filtered"])
    terms = " OR ".join(f'abs:"{t}"' for t in cfg["filtered_query_terms"])
    papers = {}
    for q in (full, f"({cats}) AND ({terms})"):
        for p in arxiv_search(q, since, until):
            papers.setdefault(p["id"], p)
        time.sleep(3)
    return list(papers.values())


# --------------------------------------------------------------------------- scoring
def compile_topics(cfg):
    return {k: [(re.compile(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])"), w)
                for kw, w in t["keywords"].items()] for k, t in cfg["topics"].items()}


def keyword_score(p, comp, tracked):
    title, abst = p["title"].lower(), p["abstract"].lower()
    per = {}
    for key, pats in comp.items():
        s = sum(2 * w if rx.search(title) else w if rx.search(abst) else 0 for rx, w in pats)
        if s:
            per[key] = s
    p["tracked_authors"] = [a for a in p["authors"] if a in tracked]
    p["topics"] = sorted(per, key=per.get, reverse=True)[:3]
    p["kw_score"] = sum(per.values()) + 8 * len(p["tracked_authors"]) + (2 if "cs.RO" in p["categories"] else 0)


def clip(text, n):
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def first_person(text):
    """Ratings are written about 'the reader'; the page speaks as its owner."""
    for x, y in (("The reader's", "My"), ("the reader's", "my"), ("The reader", "I"), ("the reader", "me")):
        text = text.replace(x, y)
    return text


SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def extractive(abstract):
    """Cheap summary when no LLM is available: the 'we propose' sentence + a results sentence."""
    sents = SENT.split(abstract)
    method = next((s for s in sents if re.search(r"\b(we (propose|introduce|present|develop)|this (paper|work)|in this work)\b", s, re.I)), sents[0])
    result = next((s for s in sents if s is not method and re.search(r"\d+(\.\d+)?\s*(%|x|×)|outperform|state-of-the-art|achiev|improv", s, re.I)), "")
    tldr = method if len(method) < 280 else method[:277].rsplit(" ", 1)[0] + "…"
    return tldr, " ".join(x for x in (method, result) if x)


# --------------------------------------------------------------------------- venues
VENUES = ["CoRL", "RSS", "ICRA", "IROS", "NeurIPS", "ICML", "ICLR", "CVPR", "ICCV", "ECCV", "AAAI", "IJCAI",
          "RA-L", "RAL", "T-RO", "TRO", "IJRR", "Science Robotics", "Humanoids", "L4DC", "CDC", "ACC", "AISTATS",
          "TMLR", "JMLR", "WACV", "SIGGRAPH Asia", "SIGGRAPH", "3DV", "UAI", "ACL", "EMNLP", "NAACL", "COLM", "WAFR",
          "Nature", "Science", "TPAMI", "ISRR", "HRI", "CASE", "ICCV"]
VENUE_RX = re.compile(r"\b(" + "|".join(re.escape(v) for v in sorted(set(VENUES), key=len, reverse=True)) + r")\b(?:[\s'’]*(20\d\d|'?\d\d))?")
NEG_RX = re.compile(r"submitted|under review|in review|preprint|extended version of", re.I)


def venue_from_comment(text):
    if not text:
        return ""
    for clause in re.split(r"[;.]\s+|\s{2,}", text):
        m = VENUE_RX.search(clause)
        if not m or NEG_RX.search(clause):
            continue
        accepted = re.search(r"accept|to appear|published|camera|oral|spotlight|poster|presented|proceedings", clause, re.I)
        year = m.group(2) or ""
        if not (accepted or year):
            continue
        year = ("20" + year.strip("'")) if len(year.strip("'")) == 2 else year
        v = f"{m.group(1)} {year}".strip()
        if re.search(r"workshop", clause, re.I):
            v += " Workshop"
        h = honor_from_comment(text)
        if h:
            v += f" ({h})"
        return v
    return ""


HONORS = [(r"best[\w\s-]{0,30}paper[\w\s-]{0,12}(finalist|nominee|nomination|candidate)", "Best Paper Finalist"),
          (r"best[\w\s-]{0,30}paper[\w\s-]{0,12}award|\bbest paper\b", "Best Paper Award"),
          (r"outstanding paper", "Outstanding Paper"), (r"\boral\b", "Oral"), (r"\bspotlight\b", "Spotlight"),
          (r"\bhighlight\b", "Highlight")]


def honor_from_comment(text):
    """Oral / Spotlight / award, searched over the whole comment (it is often in a different clause than the venue)."""
    for rx, name in HONORS:
        if re.search(rx, text or "", re.I):
            return name
    return ""


def s2_lookup(ids):
    """Semantic Scholar venue + citation counts for arXiv ids."""
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        res = http_json("https://api.semanticscholar.org/graph/v1/paper/batch?fields=venue,publicationVenue,year,citationCount",
                        {"ids": [f"ARXIV:{x}" for x in chunk]})
        for aid, r in zip(chunk, res or []):
            if not r:
                continue
            venue = ""
            pv = r.get("publicationVenue") or {}
            name = pv.get("name") or r.get("venue") or ""
            if name and "arxiv" not in name.lower():
                short = next((a for a in pv.get("alternate_names", []) if a in VENUES), None)
                venue = f"{short or name} {r.get('year') or ''}".strip()
            out[aid] = {"citations": r.get("citationCount") or 0, "s2_venue": venue}
        time.sleep(2)
    return out


# --------------------------------------------------------------------------- Hugging Face trending
def hf_trending(date, n):
    res = http_json(f"https://huggingface.co/api/daily_papers?date={date}&limit=100") or []
    items = []
    for r in res:
        p = r.get("paper", {})
        if not p.get("id"):
            continue
        items.append({
            "id": p["id"], "title": " ".join(p.get("title", "").split()),
            "authors": [a.get("name", "") for a in p.get("authors", [])],
            "abstract": " ".join(p.get("summary", "").split()),
            "summary": p.get("ai_summary") or clip(" ".join(p.get("summary", "").split()), 320),
            "upvotes": p.get("upvotes", 0), "github": p.get("githubRepo") or "",
            "stars": p.get("githubStars") or 0, "project": p.get("projectPage") or "",
            "org": (p.get("organization") or {}).get("fullname", ""),
            "url": f"https://arxiv.org/abs/{p['id']}", "pdf": f"https://arxiv.org/pdf/{p['id']}",
            "hf": f"https://huggingface.co/papers/{p['id']}",
        })
    items.sort(key=lambda x: x["upvotes"], reverse=True)
    return items[:n]


# --------------------------------------------------------------------------- Claude ranking
def rating_schema(topic_keys):
    item = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "relevance": {"type": "integer", "description": "0-10 fit to the reader's interests"},
            "quality": {"type": "integer", "description": "0-10 expected significance / insight"},
            "topic": {"type": "string", "enum": topic_keys},
            "tldr": {"type": "string", "description": "one sentence, <=25 words: what they did"},
            "summary": {"type": "string", "description": "2-3 sentences: problem, key idea, main result"},
            "why": {"type": "string", "description": "one short sentence: why this reader should care"},
        },
        "required": ["id", "relevance", "quality", "topic", "tldr", "summary", "why"],
        "additionalProperties": False,
    }
    return {"type": "object", "properties": {"papers": {"type": "array", "items": item}},
            "required": ["papers"], "additionalProperties": False}


def rating_system_prompt(cfg):
    topic_doc = "\n".join(f"- {k}: {t['label']}" for k, t in cfg["topics"].items())
    return ("You curate a daily arXiv digest for one robot-learning researcher. Judge each paper against the "
            "reader's profile. Be discerning: most papers should get relevance <=5; reserve 8-10 for papers squarely "
            "in the core interests or likely to be important. Summaries must be concrete (method, setting, numbers), "
            "never marketing language.\n\n"
            f"Topic keys:\n{topic_doc}\n- Other: anything else\n\nReader profile:\n{(PAPERS / 'interests.md').read_text()}")


def fmt_paper(p):
    au = ", ".join(p["authors"][:10]) + (" et al." if len(p["authors"]) > 10 else "")
    return f"[{p['id']}] {p['title']}\nAuthors: {au}\nCategories: {', '.join(p['categories'])}\nAbstract: {p['abstract']}"


def claude_call(system, user, schema, max_tokens=32000):
    import anthropic
    client = anthropic.Anthropic()
    kwargs = dict(model=MODEL, max_tokens=max_tokens, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                  system=system, messages=[{"role": "user", "content": user}],
                  output_config={"effort": "medium", **({"format": {"type": "json_schema", "schema": schema}} if schema else {})})
    with client.beta.messages.stream(**kwargs) as s:
        resp = s.get_final_message()
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude stop_reason={resp.stop_reason}")
    return next(b.text for b in resp.content if b.type == "text")


def llm_rate(cands, cfg):
    keys = list(cfg["topics"]) + ["Other"]
    out = {}
    for i in range(0, len(cands), 40):
        chunk = cands[i:i + 40]
        text = claude_call(rating_system_prompt(cfg),
                           "Rate every paper below (one entry per id).\n\n" + "\n\n".join(map(fmt_paper, chunk)),
                           rating_schema(keys))
        for r in json.loads(text)["papers"]:
            r["by"] = MODEL
            out[r.pop("id")] = r
    return out


def llm_overview(date, top):
    if not top:
        return ""
    listing = "\n".join(f"- {p['title']}: {p.get('tldr', '')}" for p in top[:12])
    return claude_call("You write a 3-4 sentence editor's note for a robot-learning researcher's daily paper digest. "
                       "Name the 2-3 most notable papers by short name and any theme connecting the day. Plain text, no lists.",
                       f"Papers submitted {date} (most relevant first):\n{listing}", None, max_tokens=4000).strip()


# --------------------------------------------------------------------------- build
PUB_KEYS = ["id", "title", "authors", "abstract", "url", "pdf", "project", "code", "primary", "categories", "published",
            "topics", "tracked_authors", "kw_score", "relevance", "quality", "tldr", "summary", "why", "venue",
            "citations", "upvotes", "github", "stars", "hf", "rated_by"]


def slim(p):
    return {k: p[k] for k in PUB_KEYS if p.get(k) not in (None, "", [])}


PRIORITY = ["CoRL", "RSS", "ICRA", "ICML", "NeurIPS", "ICLR"]


def venue_bonus(v):
    """Main robotics / ML conferences get ranked up; workshops a little."""
    if not v:
        return 0
    if "Workshop" in v:
        return 1
    return (4 if v.split()[0] in PRIORITY else 2) + (1 if "(" in v else 0)  # +1 for oral / spotlight / award


def select_day(papers, ratings, cfg):
    for p in papers:
        p["venue"] = venue_from_comment(p["comment"]) or venue_from_comment(p["journal_ref"])
        if p["venue"]:
            p["kw_score"] += 2 * venue_bonus(p["venue"])
    relevant = sorted((p for p in papers if p["kw_score"] > 2 or p["tracked_authors"]),
                      key=lambda p: p["kw_score"], reverse=True)
    for p in relevant:
        r = ratings.get(p["id"])
        if r:
            p.update({k: r[k] for k in ("relevance", "quality", "tldr", "summary", "why")})
            p["why"] = first_person(p["why"])
            p["rated_by"] = r.get("by", "claude")
            if r.get("topic") not in (None, "Other"):
                p["topics"] = [r["topic"]] + [t for t in p["topics"] if t != r["topic"]]
        else:
            p["tldr"], p["summary"] = extractive(p["abstract"])
    rated = [p for p in relevant if "relevance" in p]
    if rated:
        rated.sort(key=lambda p: (score_of(p), p["kw_score"]), reverse=True)
        top = [p for p in rated if p["relevance"] >= 7 or (p["relevance"] >= 6 and venue_bonus(p.get("venue")) >= 4)][: cfg["top_picks"]]
        if len(top) < 5:  # quiet day: still show the best few
            top += [p for p in rated if p not in top and p["relevance"] >= 5][: 5 - len(top)]
        rest = [p for p in rated if p not in top and p["relevance"] >= 4]
        rest += [p for p in relevant if "relevance" not in p and "cs.RO" in p["categories"]]
    else:
        top = [p for p in relevant if p["kw_score"] >= 10][: cfg["top_picks"]]
        rest = [p for p in relevant if p not in top]
    return top, rest[: cfg["roundup_size"]], relevant


def score_of(p):
    base = p["relevance"] * 2 + p.get("quality", 5) if "relevance" in p else min(p.get("kw_score", 0), 40) / 2
    return base + venue_bonus(p.get("venue"))


def build_best(cfg, today):
    files = sorted(DAYS.glob("*.json"), reverse=True)
    week, month, ml_week, ml_month = [], [], {}, {}
    for f in files:
        d = load(f, {})
        age = (today - dt.date.fromisoformat(d["date"])).days
        if age > 30:
            continue
        for p in d.get("top_picks", []) + d.get("roundup", []):
            p = {**p, "day": d["date"]}
            month.append(p)
            if age <= 7:
                week.append(p)
        for p in d.get("trending_ml", []):
            ml_month.setdefault(p["id"], p)
            if age <= 7:
                ml_week.setdefault(p["id"], p)

    def rank(lst, n):
        lst.sort(key=lambda p: (score_of(p), p.get("citations", 0)), reverse=True)
        return lst[:n]
    up = lambda d, n: sorted(d.values(), key=lambda p: p.get("upvotes", 0), reverse=True)[:n]
    dump(DATA / "best.json", {"updated": today.isoformat(), "week": rank(week, 30), "month": rank(month, 60),
                              "ml_week": up(ml_week, 25), "ml_month": up(ml_month, 50)})


def refresh_s2(today):
    """Update venues / citations for every paper shown in the last 30 days."""
    files = [f for f in DAYS.glob("*.json") if (today - dt.date.fromisoformat(f.stem)).days <= 30]
    docs = {f: load(f, {}) for f in files}
    ids = {p["id"] for d in docs.values() for k in ("top_picks", "roundup", "trending_ml") for p in d.get(k, [])}
    info = s2_lookup(sorted(ids))
    print(f"Semantic Scholar: {len(info)}/{len(ids)} found")
    for f, d in docs.items():
        for k in ("top_picks", "roundup", "trending_ml"):
            for p in d.get(k, []):
                i = info.get(p["id"])
                if i:
                    p["citations"] = i["citations"]
                    if i["s2_venue"] and not p.get("venue"):
                        p["venue"] = i["s2_venue"]
        dump(f, d, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    ap.add_argument("--backfill", type=int, default=0, help="rebuild the last N days")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--export-candidates", help="write unrated candidates (per day) to this file and exit")
    ap.add_argument("--import-ratings", nargs="*", default=[], help="merge rating files into the cache")
    ap.add_argument("--candidates-per-day", type=int, default=None)
    args = ap.parse_args()

    cfg = json.loads((PAPERS / "config.json").read_text())
    ratings = load(CACHE / "ratings.json", {})
    overviews = load(CACHE / "overviews.json", {})
    for f in args.import_ratings:
        imp = load(Path(f), {})
        ratings.update(imp.get("papers", {}))
        overviews.update(imp.get("overviews", {}))
        print(f"imported {len(imp.get('papers', {}))} ratings from {f}")

    today = dt.date.fromisoformat(args.date)
    window = args.backfill or cfg["lookback_days"]
    since = today - dt.timedelta(days=window)
    print(f"Fetching arXiv {since} → {today}")
    papers = fetch_window(cfg, since, today)
    comp, tracked = compile_topics(cfg), set(cfg["tracked_authors"])
    by_day = defaultdict(list)
    for p in papers:
        keyword_score(p, comp, tracked)
        by_day[p["published"]].append(p)
    print(f"{len(papers)} papers over {len(by_day)} days")

    per_day = args.candidates_per_day or cfg["max_candidates_for_llm"]
    cands = {}
    for day, ps in by_day.items():
        rel = sorted((p for p in ps if p["kw_score"] > 2 or p["tracked_authors"]), key=lambda p: p["kw_score"], reverse=True)
        cands[day] = rel[:per_day] + [p for p in rel[per_day:] if p["tracked_authors"]]

    if args.export_candidates:
        out = {d: [{k: p[k] for k in ("id", "title", "authors", "categories", "abstract")} for p in c if p["id"] not in ratings]
               for d, c in sorted(cands.items())}
        dump(Path(args.export_candidates), out, 1)
        print(f"exported {sum(map(len, out.values()))} candidates")
        return

    use_llm = not args.no_llm and bool(os.environ.get("ANTHROPIC_API_KEY"))
    if use_llm:
        todo = [p for c in cands.values() for p in c if p["id"] not in ratings]
        print(f"Rating {len(todo)} new candidates with {MODEL}")
        try:
            ratings.update(llm_rate(todo, cfg))
        except Exception as e:
            print(f"LLM rating failed, using keywords for unrated papers: {e!r}", file=sys.stderr)
    dump(CACHE / "ratings.json", ratings)

    index = {d["date"]: d for d in load(DATA / "index.json", [])}
    for day in sorted(by_day):
        top, roundup, relevant = select_day(by_day[day], ratings, cfg)
        if use_llm and day not in overviews and any("relevance" in p for p in top):
            try:
                overviews[day] = llm_overview(day, top)
            except Exception as e:
                print(f"overview failed for {day}: {e!r}", file=sys.stderr)
        trending = hf_trending(day, cfg.get("trending_ml", 10))
        rated = sum("rated_by" in p for p in top + roundup)
        digest = {
            "date": day, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "ranker": "Claude" if rated else "keywords", "overview": overviews.get(day, ""),
            "stats": {"papers": len(by_day[day]), "cs_ro": sum("cs.RO" in p["categories"] for p in by_day[day]),
                      "relevant": len(relevant), "rated": rated},
            "top_picks": [slim(p) for p in top], "roundup": [slim(p) for p in roundup], "trending_ml": trending,
        }
        dump(DAYS / f"{day}.json", digest, 1)
        index[day] = {"date": day, "top": len(top), "roundup": len(roundup), "papers": len(by_day[day]),
                      "trending": len(trending), "ranker": digest["ranker"]}
        print(f"  {day}: {len(top)} top, {len(roundup)} roundup, {len(trending)} trending ({digest['ranker']})")
        time.sleep(0.5)

    dump(CACHE / "overviews.json", overviews, 1)
    dump(DATA / "index.json", sorted(index.values(), key=lambda d: d["date"], reverse=True), 1)
    refresh_s2(today)
    build_best(cfg, today)
    print("done")


if __name__ == "__main__":
    main()
