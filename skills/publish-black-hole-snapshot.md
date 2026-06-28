---
name: publish-black-hole-snapshot
description: Regenerate the public NROL-AO dashboard snapshot and push it to the black-hole site. Run when the user says "update the public dashboard", "publish the snapshot", "refresh the black-hole surface", or after a meaningful NROL-AO session (resolution, big posterior move, new topic activated).
---

# publish-black-hole-snapshot

Regenerate `surfaces/nrol-ao/data.json` in the black-hole repo from the
current NROL-AO topic state, then commit + push to black-hole `master`.

## What it does

1. Runs `python export_blackhole_snapshot.py --black-hole <black-hole-repo>`
   from the NROL-AO repo root. This writes a **sanitized** snapshot — per
   topic: slug, title, status, classification, lastUpdated, committed +
   shadow posteriors + deltas, governance health, expected value. **No
   evidence text, no source names, no article URLs.** Safe to publish.
2. `cd` into the black-hole repo, `git add surfaces/nrol-ao/data.json`,
   commit with a message like `chore(nrol-ao): refresh public snapshot
   (<topic_count> topics, <date>)`, and `git push origin master`.
3. Report the topic count + generated timestamp + the public URL
   (https://<site>/surfaces/nrol-ao/).

## Paths

- NROL-AO repo (source): `C:\Claude-Code\NROL-AO\temp-repo` (export script:
  `export_blackhole_snapshot.py`).
- Black-hole repo (target): `C:\Users\exast\OneDrive\Documents\Loom-Projects\black-hole`
  (writes `surfaces/nrol-ao/data.json`; the `index.html` surface and its
  `surfaces/config.json` entry already exist).

## When to run

- On ask: "update the public dashboard" / "publish the snapshot" /
  "refresh the black-hole surface".
- After a meaningful NROL-AO event: a topic resolution, a large posterior
  move, a new topic activated, or the end of an evidence-loop sweep.
- The snapshot is **not** live — it reflects state at generation time. If
  the user wants live data, that's a different (bigger) feature; this skill
  is the periodic-publish path.

## Guardrails

- Only `data.json` is rewritten — never touch `index.html` or `config.json`
  (those are the surface, set up once).
- The export script filters to the safe slice; do not hand-edit `data.json`
  to add evidence text or sources.
- If the black-hole repo has unrelated uncommitted work, commit only
  `surfaces/nrol-ao/data.json` (stage that path explicitly).
