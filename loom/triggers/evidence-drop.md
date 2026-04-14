NROL-AO EVIDENCE DROP — a file was dropped on the Mirror dashboard.

## File
{{filename}}

## Instructions

Process this dropped file as potential evidence for the NROL-AO system:

1. **Read the file** — determine what it contains (article, screenshot, data, document).

2. **Extract headline and source** — identify the key claim or news item, and the originating source.

3. **Triage** — match against all active topics in `topics/`. Check indicators, watchpoints, and domain keywords.

4. **If relevant**, run the full pipeline:
   - Look up source trust in `source_db.json` and `source-trust.json`
   - Log evidence to the matched topic JSON in `topics/{slug}.json`
   - Update posteriors if warranted (MODERATE or MAJOR impact)
   - Update source calibration if this confirms/refutes existing evidence
   - Append to `activity-log.json`

5. **Report** what you found and what you updated.
