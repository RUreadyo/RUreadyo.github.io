# Task: rate and summarize arXiv papers for a robot-learning researcher's daily digest

Inputs:
- Reader profile: papers/interests.md (read it first).
- Your batch: /tmp/cands.json — JSON object {"YYYY-MM-DD": [ {id, title, authors, categories, abstract}, ... ], ...}.
  Read the whole file (use Read with offset/limit if large). Rate EVERY paper in it.

For each paper produce:
- relevance: integer 0-10, fit to the reader's profile. Be discerning: most papers <=5; 8-10 only for papers squarely in core interests (dexterous manipulation, diffusion/flow policies & action chunking, RL+generative policies, generative-model theory) or likely to be important.
- quality: integer 0-10, expected significance/insight (clear new idea, strong real-robot results, rigorous analysis > incremental benchmark tweak).
- topic: one of RL, RLGen, IL, GenModel, FM, Video, WM, Dex, Control, Other
  (RL=reinforcement learning, RLGen=RL x diffusion/flow, IL=imitation learning, GenModel=generative models, FM=robot foundation models/VLA, Video=video generation, WM=world models, Dex=dexterous manipulation, Control=control).
- tldr: one sentence, <=25 words, what they did (concrete, no hype).
- summary: 2-3 sentences: problem, key idea, main result (include numbers/settings if the abstract gives them).
- why: one short sentence on why the reader should care (or why it's tangential). Refer to them as "the reader" (it is rewritten to first person on the page).

Also, for each date in the batch, write an "overview": a 3-4 sentence editor's note naming the 2-3 most notable papers (by short name) for this reader and any connecting theme. Plain text.

Output: write valid JSON to /tmp/ratings.json with exactly this shape:
{"papers": {"<arxiv id>": {"relevance": 7, "quality": 6, "topic": "IL", "tldr": "...", "summary": "...", "why": "...", "by": "claude"}, ...},
 "overviews": {"YYYY-MM-DD": "...", ...}}

## Daily routine (run from the repo root)
1. `python3 scripts/fetch_papers.py --export-candidates /tmp/cands.json` (lists only papers not yet rated).
2. Rate every candidate in /tmp/cands.json as described above and write /tmp/ratings.json. Write overviews only for dates that have at least one paper.
3. `python3 scripts/fetch_papers.py --import-ratings /tmp/ratings.json` (rebuilds the recent days with the new ratings, refreshes venues/citations, best.json).
4. If `papers/data/impact.json` is missing or its "updated" date is more than 7 days old: `python3 scripts/impact.py` (Semantic Scholar may rate-limit; if it fails, skip it).
5. `git add papers/data && git commit -m "papers: digest $(date -u +%F)" && git push`. If the push is rejected, `git pull --rebase` and push again.
