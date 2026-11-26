# Task: rate and summarize arXiv papers for a robot-learning researcher's daily digest

Inputs:
- Reader profile: papers/interests.md (read it first).
- Your batch: /tmp/cands.json — JSON object {"YYYY-MM-DD": [ {id, title, authors, categories, abstract}, ... ], ...}.
  Read the whole file (use Read with offset/limit if large). Rate EVERY paper in it.

For each paper produce:
- relevance: integer 0-10, fit to the reader's profile. Be discerning: most papers <=5; 8-10 only for papers squarely in core interests (dexterous manipulation, diffusion/flow policies & action chunking, RL+generative policies, generative-model theory) or likely to be important.
- quality: integer 0-10, expected significance/insight (clear new idea, strong real-robot results, rigorous analysis > incremental benchmark tweak).
- topic: one of RL, RLGen, IL, GenModel, FM, Video, WM, Dex, Tactile, Agentic, Control, Other
  (RL=reinforcement learning, RLGen=RL x diffusion/flow, IL=imitation learning, GenModel=generative models, FM=robot foundation models/VLA, Video=video generation, WM=world models, Dex=dexterous manipulation, Tactile=tactile/visuotactile/force sensing and touch-based learning, Agentic=agentic robotics: LLM/VLM planners or orchestrators calling skills/tools, code-as-policy, embodied reasoning, re-planning and failure recovery, agents that automate robot learning (reward/task/data generation), Control=control).
- tldr: one sentence, <=25 words, what they did (concrete, no hype).
- summary: 2-3 sentences: problem, key idea, main result (include numbers/settings if the abstract gives them).
- why: one short sentence on why the reader should care (or why it's tangential). Refer to them as "the reader" (it is rewritten to first person on the page).

Also, for each date in the batch, write an "overview": a 3-4 sentence editor's note naming the 2-3 most notable papers (by short name) for this reader and any connecting theme. Plain text.

Output: write valid JSON to /tmp/ratings.json with exactly this shape:
{"papers": {"<arxiv id>": {"relevance": 7, "quality": 6, "topic": "IL", "tldr": "...", "summary": "...", "why": "...", "by": "claude"}, ...},
 "overviews": {"YYYY-MM-DD": "...", ...}}

## Daily routine (Claude routine, run from the repo root; NO network needed)
The GitHub Action fetches arXiv each weekday at 13:00 UTC and saves the papers to papers/data/cache/window.json.
The routine only rates them (the cloud sandbox cannot reach arXiv, so never fetch):
1. `python3 scripts/fetch_papers.py --offline --export-candidates /tmp/cands.json` (lists only papers not yet rated).
   If it reports 0 candidates, stop: there is nothing to do and nothing to commit.
2. Rate every candidate in /tmp/cands.json as described above and write /tmp/ratings.json. Write overviews only for dates that have at least one paper. Validate: `python3 -c 'import json;json.load(open("/tmp/ratings.json"))'`.
3. `python3 scripts/fetch_papers.py --offline --import-ratings /tmp/ratings.json` (rebuilds the recent days and best.json with the new ratings).
4. `git add papers/data && git commit -m "papers: Claude ratings $(date -u +%F)" && git push`. If the push is rejected, `git pull --rebase` and push again.
