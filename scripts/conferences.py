#!/usr/bin/env python3
"""Conference archive: accepted papers from CoRL, RSS, ICLR, ICML, NeurIPS that match my interests.

Sources (all public, no key):
  - ICLR / ICML / NeurIPS: the conference sites' virtual-program JSON (has Oral / Spotlight decisions)
  - CoRL: PMLR bibliography.bib (titles, authors, abstracts)
  - RSS: roboticsproceedings.org index + per-paper pages (abstracts)
Writes papers/data/conf/<CONF>.json = {conf, updated, years: {year: [paper, ...]}} (on-topic, with abstracts)
and papers/data/conf/full/<CONF>.json = every accepted paper as [title, authors, honor, url] for the Search tab.
Papers are scored with the same topic keywords as the daily digest (papers/config.json); robotics
venues keep everything on-topic, ML venues keep the strongest matches.

Usage: python scripts/conferences.py [--only ICLR,CoRL] [--years 2023-2026]
Already-built conference-years are skipped unless --refresh (proceedings don't change).
"""
import argparse
import ast
import difflib
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "papers" / "data" / "conf"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_papers as fp  # noqa: E402  (keyword_score, compile_topics, extractive)

UA = {"User-Agent": "Mozilla/5.0 (paper-digest)"}
VIRTUAL = {"ICLR": "iclr", "ICML": "icml", "NeurIPS": "neurips"}
CORL_PMLR = {2023: 229, 2024: 270, 2025: 305}
RSS_VOL = {2023: "rss19", 2024: "rss20", 2025: "rss21", 2026: "rss22"}
ROBOTICS = {"CoRL", "RSS", "ICRA", "IROS"}
PAPERCEPT = {"ICRA", "IROS"}  # programs on ras.papercept.net (only recent years are public)
# keep at most this many per conference-year (ML venues have thousands of papers)
CAP = {"CoRL": 400, "RSS": 250, "ICLR": 150, "ICML": 150, "NeurIPS": 150, "ICRA": 300, "IROS": 250}
MIN_SCORE = {"CoRL": 1, "RSS": 1, "ICLR": 10, "ICML": 10, "NeurIPS": 10, "ICRA": 7, "IROS": 7}
HONOR_BONUS = {"Oral": 6, "Spotlight": 3, "Best Paper Award": 12, "Best Student Paper": 11, "Best Paper Finalist": 9}
HONOR_RANK = ["Best Paper Award", "Best Student Paper", "Best Paper Finalist", "Oral", "Spotlight", ""]


def get(url, binary=False, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                data = r.read()
                return data if binary else data.decode("utf-8", "replace")
        except Exception as e:
            print(f"  {url}: {e} (retry {i + 1})", file=sys.stderr)
            time.sleep(4 * (i + 1))
    return None


def arxiv_id(*urls):
    for u in urls:
        m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", u or "")
        if m:
            return m.group(1)
    return ""


# ---------------------------------------------------------------- sources
def from_virtual(conf, year):
    raw = get(f"https://{VIRTUAL[conf]}.cc/static/virtual/data/{VIRTUAL[conf]}-{year}-orals-posters.json")
    if not raw:
        return []
    try:
        rows = json.loads(raw)["results"]
    except Exception:
        return []
    out = {}
    for r in rows:
        if r.get("eventtype") not in ("Poster", "Oral", "Spotlight") or not r.get("name"):
            continue
        dec = (r.get("decision") or "") + " " + (r.get("eventtype") or "")
        honor = "Oral" if re.search(r"oral", dec, re.I) else "Spotlight" if re.search(r"spotlight", dec, re.I) else ""
        al = r.get("authors") or []
        try:
            if isinstance(al, str):
                al = ast.literal_eval(al)
            authors = [a["fullname"] for a in al]
        except Exception:
            authors = re.findall(r"'fullname': '([^']+)'", str(al))
        title = clean_tex(r["name"].strip())
        key = re.sub(r"\W", "", title.lower())
        prev = out.get(key)
        if prev and (prev["honor"] or not honor):
            continue
        site = f"https://{VIRTUAL[conf]}.cc{r['virtualsite_url']}" if r.get("virtualsite_url") else ""
        paper = r.get("paper_url") if r.get("paper_url") not in (None, "None") else ""
        out[key] = {"title": title, "authors": authors, "abstract": " ".join((r.get("abstract") or "").split()),
                    "url": paper or site, "site": site, "honor": honor,
                    "code": r["url"] if "github.com" in (r.get("url") or "") else ""}
    return list(out.values())


def bib_field(entry, name):
    m = re.search(name + r"\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}", entry, re.S)
    return " ".join(m.group(1).replace("{", "").replace("}", "").split()) if m else ""


def find_corl_volume(year):
    """New CoRL volumes appear on PMLR a few months after the conference; probe the volumes after the last known one."""
    n = {2023: 7, 2024: 8, 2025: 9}.get(year, year - 2016)  # "Proceedings of The Nth Conference on Robot Learning"
    start = max(CORL_PMLR.values()) + 1
    for v in range(start, start + 60):
        t = re.search(r"<title>([^<]*)", get(f"https://proceedings.mlr.press/v{v}/", tries=1) or "")
        if t and re.search(rf"\b{n}(st|nd|rd|th) Conference on Robot Learning", t.group(1)):
            return v
    return None


def from_pmlr(year):
    vol = CORL_PMLR.get(year) or find_corl_volume(year)
    raw = vol and get(f"https://proceedings.mlr.press/v{vol}/assets/bib/bibliography.bib")
    if not raw:
        return []
    out = []
    for entry in raw.split("@InProceedings")[1:]:
        title = bib_field(entry, "title")
        if not title:
            continue
        authors = [" ".join(reversed([x.strip() for x in a.split(",", 1)])) for a in bib_field(entry, "author").split(" and ") if a.strip()]
        out.append({"title": clean_tex(title), "authors": authors, "abstract": bib_field(entry, "abstract"),
                    "url": bib_field(entry, "url"), "pdf": bib_field(entry, "pdf"), "honor": ""})
    return out


def from_rss(year):
    vol = RSS_VOL.get(year)
    base = f"https://www.roboticsproceedings.org/{vol}/"
    idx = vol and get(base + "index.html")
    if not idx:
        return []
    out = []
    for m in re.finditer(r'<a href="(p\d+)\.html">(.*?)</a>', idx, re.S):
        pid, title = m.group(1), html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        page = get(base + pid + ".html") or ""
        text = html.unescape(re.sub(r"<[^>]+>", "\n", page))
        ab = re.search(r"Abstract:\s*(.*?)(?:\n\s*\n|Download|Bibtex|$)", text, re.S)
        au = re.search(re.escape(title) + r"\s*\n+\s*([^\n]+)", text)
        out.append({"title": title, "authors": [a.strip() for a in re.split(r",| and ", au.group(1)) if a.strip()] if au else [],
                    "abstract": " ".join(ab.group(1).split()) if ab else "", "url": base + pid + ".html",
                    "pdf": base + pid + ".pdf", "honor": ""})
        time.sleep(0.2)
    return out


GREEK = {"pi": "π", "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "lambda": "λ", "mu": "μ",
         "sigma": "σ", "tau": "τ", "phi": "φ", "psi": "ψ", "omega": "ω", "theta": "θ", "infty": "∞", "times": "×"}


def clean_tex(t):
    """'$\\pi_0.5$: a VLA' -> 'π0.5: a VLA' (titles from proceedings keep LaTeX)."""
    if "$" not in t and "\\" not in t:
        return t
    t = re.sub(r"\\(?:mathbf|mathrm|textbf|text|mathcal|textit|emph)\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\\([a-zA-Z]+)", lambda m: GREEK.get(m.group(1), m.group(1)), t)
    return " ".join(re.sub(r"[$_^{}]", "", t).split())


def norm(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())[:90]


def corl_honors(year):
    """Orals (main-conference schedule) and awards from <year>.corl.org; empty if the site doesn't have them."""
    out = {}

    def lines(url):
        page = get(url, tries=2) or ""
        if f"CoRL {year}" not in page:  # the current year lives on www.corl.org
            alt = get(url.replace(f"https://{year}.corl.org", "https://www.corl.org"), tries=2) or ""
            page = alt if f"CoRL {year}" in alt else page
        return [l.strip() for l in html.unescape(re.sub(r"<[^>]+>", "\n", page)).split("\n") if l.strip()]

    t = lines(f"https://{year}.corl.org/program/main-conference")
    for i, l in enumerate(t):
        if l == "Authors:" and i:
            j = i + 1
            while j < len(t) and t[j] != "Authors:" and not t[j].startswith(("Oral Time", "Poster Time")):
                j += 1
            if j < len(t) and t[j].startswith("Oral Time"):
                out[norm(t[i - 1])] = "Oral"
    section = None
    for l in lines(f"https://{year}.corl.org/program/awards"):
        if re.fullmatch(r"Best Paper Awards?", l):
            section = "Best Paper Award"
        elif re.fullmatch(r"Best Student Paper Awards?", l):
            section = "Best Student Paper"
        elif re.search(r"finalist", l, re.I):
            section = "Best Paper Finalist"
        elif re.search(r"committee|report abuse", l, re.I):
            section = None
        elif section and len(l) > 15 and "," not in l[:40]:
            k = norm(l)
            if HONOR_RANK.index(section) < HONOR_RANK.index(out.get(k, "")):
                out[k] = section
    print(f"  CoRL {year} site: {sum(v == 'Oral' for v in out.values())} orals, {sum(v != 'Oral' for v in out.values())} awards/finalists")
    return out


def from_papercept(conf, year):
    code = f"{conf}{year % 100:02d}"
    base = f"https://ras.papercept.net/conferences/conferences/{code}/program/"
    out, seen = [], set()
    for n in range(1, 9):
        raw = get(f"{base}{code}_ContentListWeb_{n}.html", binary=True, tries=2)
        if not raw:
            if n == 1:
                return []
            break
        page = raw.decode("cp1252", "replace")
        sessions = [(m.start(), html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))) for m in
                    re.finditer(r'ProgramAtAGlanceWeb\.html#\w+" title="Click to go to the Program at a Glance"><b>(.*?)</b>', page, re.S)]
        for m in re.finditer(r'<tr class="pHdr">(.*?)(?=<tr class="pHdr">|$)', page, re.S):
            chunk = m.group(1)
            t = re.search(r'class="pTtl">.*?<a [^>]*>(.*?)</a>', chunk, re.S)
            if not t:
                continue
            title = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", t.group(1))).split())
            k = norm(title)
            if not title or k in seen:
                continue
            seen.add(k)
            head = chunk.split('<div id="Ab')[0]
            authors = [" ".join(reversed([x.strip() for x in html.unescape(a).split(",", 1)]))
                       for a in re.findall(r'AuthorIndexWeb\.html#\d+" title="Click to go to the Author Index">([^<]+)</a>', head)]
            ab = re.search(r"<strong>Abstract:</strong>(.*?)</div>", chunk, re.S)
            anchor = re.search(r'<a name="([^"]+)">', chunk)
            sess = next((name for pos, name in reversed(sessions) if pos < m.start()), "")
            out.append({"title": title, "authors": authors,
                        "abstract": " ".join(html.unescape(re.sub(r"<[^>]+>", " ", ab.group(1))).split()) if ab else "",
                        "url": f"{base}{code}_ContentListWeb_{n}.html" + (f"#{anchor.group(1)}" if anchor else ""),
                        "honor": "Best Paper Finalist" if re.search(r"award finalist", sess, re.I) else ""})
        time.sleep(0.5)
    return out


ARXIV_CACHE = ROOT / "papers" / "data" / "cache" / "arxiv_titles.json"


def arxiv_by_title(title, cache):
    """Authors / abstract / id for a title via the arXiv API (3 s politeness delay; cached)."""
    k = norm(title)
    if cache.get(k) or (k in cache and cache.get("_miss_" + k) == dt.date.today().isoformat()):
        return cache.get(k)
    q = re.sub(r"[^A-Za-z0-9 ]", " ", title)
    q = " AND ".join(f"ti:{w}" for w in q.split() if len(w) > 2)[:900]
    raw = get("https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"search_query": q, "max_results": 3}), tries=2) or ""
    time.sleep(3)
    hit = None
    for e in raw.split("<entry>")[1:]:
        t = " ".join(html.unescape(re.search(r"<title>(.*?)</title>", e, re.S).group(1)).split())
        if norm(t) == k or (len(k) > 30 and norm(t).startswith(k[:40])) or difflib.SequenceMatcher(None, norm(t), k).ratio() > 0.9:
            hit = {"arxiv": re.search(r"<id>https?://arxiv.org/abs/([\d.]+)", e).group(1),
                   "authors": [html.unescape(a) for a in re.findall(r"<name>(.*?)</name>", e)],
                   "abstract": " ".join(html.unescape(re.search(r"<summary>(.*?)</summary>", e, re.S).group(1)).split())}
            break
    cache[k] = hit
    if not hit:
        cache["_miss_" + k] = dt.date.today().isoformat()  # retried on later days
    return hit


def from_corl_site(year):
    """Accepted papers + oral/spotlight sessions from the CoRL website (used before PMLR publishes the volume)."""
    page = None
    for url in (f"https://{year}.corl.org/program/accepted-papers", "https://www.corl.org/program/accepted-papers"):
        page = get(url, tries=2)
        if page and f"CoRL {year}" in page:
            break
        page = None
    if not page:
        return []
    page = html.unescape(page)
    sess = [(m.start(), m.group(1)) for m in re.finditer(r'<h3[^>]*class="(oral|spotlight|poster)[^"]*"', page)]
    best = {}
    for m in re.finditer(r'<(?:li|tr) class="paper">(?:<td[^>]*>[^<]*</td><td>)?<a href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
        title = clean_tex(" ".join(html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).split()))
        kind = next((k for pos, k in reversed(sess) if pos < m.start()), "poster")
        honor = {"oral": "Oral", "spotlight": "Spotlight"}.get(kind, "")
        k = norm(title)
        if k not in best or HONOR_RANK.index(honor) < HONOR_RANK.index(best[k]["honor"]):
            best[k] = {"title": title, "authors": [], "abstract": "", "url": m.group(1), "honor": honor}
    cache = json.loads(ARXIV_CACHE.read_text()) if ARXIV_CACHE.exists() else {}
    print(f"  CoRL {year} site: {len(best)} papers; looking up authors/abstracts on arXiv ({sum(norm(p['title']) not in cache for p in best.values())} new)")
    for i, p in enumerate(best.values()):
        hit = arxiv_by_title(p["title"], cache)
        if hit:
            p.update(authors=hit["authors"], abstract=hit["abstract"], arxiv=hit["arxiv"], pdf=f"https://arxiv.org/pdf/{hit['arxiv']}")
        if i % 50 == 49:
            ARXIV_CACHE.write_text(json.dumps(cache))
    ARXIV_CACHE.write_text(json.dumps(cache))
    return list(best.values())


# ---------------------------------------------------------------- build
def build(conf, year, comp, tracked):
    src = (from_virtual(conf, year) if conf in VIRTUAL else (from_pmlr(year) or from_corl_site(year)) if conf == "CoRL"
           else from_papercept(conf, year) if conf in PAPERCEPT else from_rss(year))
    if not src:
        return None, None
    if conf == "CoRL":
        hon = corl_honors(year)
        for p in src:
            p["honor"] = hon.get(norm(p["title"]), p["honor"])
    full = [[p["title"], ", ".join(p["authors"][:8]) + (" et al." if len(p["authors"]) > 8 else ""), p["honor"], p["url"]] for p in src]
    scored = []
    for p in src:
        q = {"title": p["title"], "abstract": p["abstract"], "authors": p["authors"], "categories": ["cs.RO"] if conf in ROBOTICS else []}
        fp.keyword_score(q, comp, tracked)
        if not q["topics"] or q["kw_score"] < MIN_SCORE[conf]:
            continue
        ax = p.get("arxiv") or arxiv_id(p.get("url"), p.get("code"))
        tldr, summary = fp.extractive(p["abstract"]) if p["abstract"] else ("", "")
        venue = f"{conf} {year}" + (f" ({p['honor']})" if p["honor"] else "")
        scored.append({
            "id": f"{conf}{year}-{len(scored)}", "title": p["title"], "authors": p["authors"][:30], "venue": venue, "year": year,
            "topics": q["topics"], "tracked_authors": q["tracked_authors"],
            "score": q["kw_score"] + HONOR_BONUS.get(p["honor"], 0), "tldr": tldr,
            "abstract": p["abstract"][:1800], "url": p["url"], "pdf": p.get("pdf") or (f"https://arxiv.org/pdf/{ax}" if ax else ""),
            **({"code": p["code"]} if p.get("code") else {}), **({"arxiv": ax} if ax else {}),
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    print(f"{conf} {year}: {len(src)} papers, {len(scored)} on-topic, keeping {min(len(scored), CAP[conf])} "
          f"| top: {scored[0]['title'][:60] if scored else '-'}")
    return scored[:CAP[conf]], full


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="CoRL,RSS,ICRA,IROS,ICLR,ICML,NeurIPS")
    ap.add_argument("--years", default=f"2023-{dt.date.today().year}")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    y0, y1 = map(int, a.years.split("-"))
    cfg = json.loads((ROOT / "papers" / "config.json").read_text())
    comp, tracked = fp.compile_topics(cfg), set(cfg.get("tracked_authors", []))
    OUT.mkdir(parents=True, exist_ok=True)
    for conf in a.only.split(","):
        path = OUT / f"{conf}.json"
        fpath = OUT / "full" / f"{conf}.json"
        doc = json.loads(path.read_text()) if path.exists() else {"conf": conf, "years": {}}
        fdoc = json.loads(fpath.read_text()) if fpath.exists() else {"conf": conf, "years": {}}
        for year in range(y1, y0 - 1, -1):
            if str(year) in doc["years"] and str(year) in fdoc["years"] and not a.refresh:
                continue
            res, full = build(conf, year, comp, tracked)
            if res is not None:
                doc["years"][str(year)] = res
                fdoc["years"][str(year)] = full
        fpath.parent.mkdir(exist_ok=True)
        fdoc["years"] = dict(sorted(fdoc["years"].items(), reverse=True))
        fpath.write_text(json.dumps(fdoc, separators=(",", ":"), ensure_ascii=False))
        doc["years"] = dict(sorted(doc["years"].items(), reverse=True))
        doc["updated"] = dt.date.today().isoformat()
        path.write_text(json.dumps(doc, indent=0, ensure_ascii=False))
    index = {c.stem: {y: len(v) for y, v in json.loads(c.read_text())["years"].items()} for c in sorted(OUT.glob("*.json")) if c.stem != "index"}
    (OUT / "index.json").write_text(json.dumps(index, indent=1))
    print("index:", index)


if __name__ == "__main__":
    main()
