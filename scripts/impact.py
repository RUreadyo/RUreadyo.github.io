#!/usr/bin/env python3
"""Most impactful papers of the last ~10 years per topic, by citation count (OpenAlex).

Writes papers/data/impact.json. Run weekly (the daily Action calls it when the file is >7 days old).
Usage: python scripts/impact.py [--years 10] [--per-topic 40]
"""
import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "papers" / "data" / "impact.json"
API = "https://api.openalex.org/works"

# A paper counts for a topic if one of its phrases is in the title or appears >=2 times in the abstract
# (full-text search alone lets mega-cited off-topic papers in).
TOPICS = {
    "ML": ["transformer", "self-attention", "attention is all you need", "vision transformer", "large language model",
           "language models", "contrastive learning", "self-supervised", "masked autoencoder", "scaling laws",
           "pre-training", "representation learning"],
    "GenModel": ["diffusion model", "diffusion models", "denoising diffusion", "score-based", "score matching",
                 "flow matching", "rectified flow", "consistency model", "consistency models", "stochastic interpolants",
                 "classifier-free guidance", "latent diffusion", "generative adversarial", "variational autoencoder",
                 "normalizing flow"],
    "IL": ["imitation learning", "behavior cloning", "behavioral cloning", "learning from demonstration",
           "learning from demonstrations", "diffusion policy", "visuomotor policy", "action chunking", "teleoperation"],
    "Dex": ["dexterous manipulation", "in-hand manipulation", "dexterous hand", "dexterous grasping", "multi-fingered",
            "in-hand object rotation", "robot hand"],
    "Tactile": ["tactile", "visuotactile", "visuo-tactile", "gelsight", "tactile sensing", "tactile sensor", "sense of touch",
                "electronic skin", "haptic"],
    "RL": ["reinforcement learning", "policy optimization", "actor-critic", "q-learning", "offline reinforcement learning",
           "model-based reinforcement learning", "policy gradient"],
    "RLGen": ["diffusion policies", "diffusion policy", "flow policy", "diffusion model"],
    "FM": ["vision-language-action", "robot foundation model", "generalist robot policy", "robotic transformer",
           "generalist robot", "embodied foundation model", "open x-embodiment", "vision-language model"],
    "WM": ["world model", "world models", "video prediction", "learned dynamics model"],
    "Video": ["video generation", "video diffusion", "text-to-video", "video synthesis"],
    "Control": ["model predictive control", "trajectory optimization", "whole-body control", "sim-to-real",
                "legged locomotion", "domain randomization", "optimal control"],
}
# topics that additionally require one of these words
REQUIRE = {
    "RLGen": ["reinforcement learning", "q-learning", "policy optimization", "reward"],
    "FM": ["robot", "robotic", "action", "embodied", "manipulation"],
    "Tactile": ["robot", "robotic", "manipulation", "grasp", "hand"],
    "Control": ["robot", "robotic", "locomotion", "manipulation", "legged", "quadruped", "humanoid"],
}

SHORT = [
    (r"neural information processing systems|neurips", "NeurIPS"), (r"international conference on machine learning", "ICML"),
    (r"international conference on learning representations", "ICLR"), (r"conference on robot learning", "CoRL"),
    (r"robotics: science and systems", "RSS"), (r"international conference on robotics and automation", "ICRA"),
    (r"intelligent robots and systems", "IROS"), (r"computer vision and pattern recognition", "CVPR"),
    (r"international conference on computer vision", "ICCV"), (r"european conference on computer vision", "ECCV"),
    (r"robotics and automation letters", "RA-L"), (r"transactions on robotics", "T-RO"), (r"science robotics", "Science Robotics"),
    (r"international journal of robotics research", "IJRR"), (r"^nature$", "Nature"), (r"^science$", "Science"),
    (r"north american chapter of the association for computational linguistics|naacl", "NAACL"),
    (r"empirical methods in natural language processing|emnlp", "EMNLP"),
    (r"annual meeting of the association for computational linguistics", "ACL"),
    (r"winter conference on applications of computer vision", "WACV"), (r"journal of field robotics", "JFR"),
    (r"aaai", "AAAI"), (r"transactions on machine learning research", "TMLR"), (r"journal of machine learning research", "JMLR"),
    (r"pattern analysis and machine intelligence", "TPAMI"), (r"siggraph|transactions on graphics", "SIGGRAPH / TOG"),
]


def get(params):
    url = API + "?" + urllib.parse.urlencode(params)
    for i in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "paper-digest/2.0"}), timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:
            print(f"  OpenAlex error ({e}); retry {i + 1}", file=sys.stderr)
            time.sleep(5 * (i + 1))
    raise RuntimeError("OpenAlex unreachable")


def abstract(inv):
    if not inv:
        return ""
    return " ".join(w for _, w in sorted((p, w) for w, ps in inv.items() for p in ps))


def venue(work):
    for loc in [work.get("primary_location")] + (work.get("locations") or []):
        name = ((loc or {}).get("source") or {}).get("display_name") or ""
        if not name or re.search(r"arxiv|biorxiv|medrxiv|ssrn|research square|openreview", name, re.I):
            continue
        return shorten(name)
    return ""


def shorten(name):
    for rx, short in SHORT:
        if re.search(rx, name, re.I):
            return short
    if re.search(r"university|ebooks|repository|scholarship|digital access|zenodo|figshare|hal ", name, re.I):
        return ""  # institutional repositories are not venues
    m = re.search(r"\(([A-Za-z][A-Za-z0-9&\-]{1,12})\)\s*$", name)
    return m.group(1) if m else name


def norm(title):
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())[:80]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--per-topic", type=int, default=40)
    args = ap.parse_args()
    today = dt.date.today()
    y0 = today.year - args.years

    out_topics = {}
    for topic, phrases in TOPICS.items():
        pool = {}
        for ph in phrases:
            data = get({
                "filter": f"publication_year:{y0}-{today.year},title_and_abstract.search:\"{ph}\"",
                "sort": "cited_by_count:desc", "per-page": 100,
                "select": "id,title,publication_year,publication_date,cited_by_count,primary_location,locations,ids,authorships,abstract_inverted_index",
            })
            for w in data.get("results", []):
                title = (w.get("title") or "").strip()
                abst = abstract(w.get("abstract_inverted_index"))
                tl, al = title.lower(), abst.lower()
                if not title or not any(p in tl or al.count(p) >= 2 for p in phrases):
                    continue
                if topic in REQUIRE and not any(r in tl or r in al for r in REQUIRE[topic]):
                    continue
                key = norm(title)
                prev = pool.get(key)
                if prev and prev["cited_by_count"] >= w["cited_by_count"]:
                    prev["_venue"] = prev["_venue"] or venue(w)  # preprint & published versions are separate works
                    continue
                w["_abstract"], w["_venue"] = abst, venue(w) or (prev or {}).get("_venue", "")
                pool[key] = w
            time.sleep(0.3)
        ranked = sorted(pool.values(), key=lambda w: w["cited_by_count"], reverse=True)[: args.per_topic]
        items = []
        for w in ranked:
            pub = w.get("publication_date") or f"{w.get('publication_year')}-07-01"
            age = max(0.5, (today - dt.date.fromisoformat(pub)).days / 365.25)
            doi = (w.get("ids") or {}).get("doi") or ""
            m = re.search(r"arxiv\.(\d{4}\.\d{4,5})", doi, re.I)
            summ = " ".join(re.split(r"(?<=[.!?])\s+(?=[A-Z])", w["_abstract"])[:2])
            items.append({
                "id": w["id"].rsplit("/", 1)[-1], "title": w["title"], "year": w.get("publication_year"), "date": pub,
                "authors": [a["author"]["display_name"] for a in (w.get("authorships") or [])][:30],
                "venue": f"{w['_venue']} {w.get('publication_year')}" if w["_venue"] else "",
                "citations": w["cited_by_count"], "per_year": round(w["cited_by_count"] / age, 1),
                "tldr": summ if len(summ) < 420 else summ[:417].rsplit(" ", 1)[0] + "…",
                "abstract": w["_abstract"],
                "url": f"https://arxiv.org/abs/{m.group(1)}" if m else (doi or w["id"]),
                "pdf": f"https://arxiv.org/pdf/{m.group(1)}" if m else "",
            })
        out_topics[topic] = items
        print(f"{topic}: {len(pool)} on-topic | " + " | ".join(f"{i['title'][:38]} ({i['citations']})" for i in items[:4]))

    OUT.write_text(json.dumps({"updated": today.isoformat(), "since": y0, "source": "OpenAlex", "topics": out_topics},
                              indent=1, ensure_ascii=False))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
