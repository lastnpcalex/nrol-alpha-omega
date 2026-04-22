"""
NROL-AO Extrapolation Audit Database

SQLite WAL-mode audit and analytics layer for the agent-driven conditional
prediction pipeline. Topic JSONs remain the source of truth; this DB is
supplementary observability.

Schema:
  agent_runs         — one row per sweep
  ideations          — every Haiku proposal (accepted or rejected)
  vetting            — Sonnet per-proposal verdicts
  meta_lint          — Gray-Opus portfolio critique
  approved_predictions — link back to conditionalPredictions in topic JSON
  portfolio_snapshots — time-series of Opus narratives
  sweep_lock         — single-writer coordination

Write path: topic JSON changes always go through engine.save_topic().
This DB only logs what the agent pipeline did and why.
"""

import sqlite3
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent.parent / "canvas" / "extrapolation.db"


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    """Open a WAL-mode connection. Usage: `with connect() as conn:`"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def init_schema():
    """Create tables if they don't exist. Idempotent."""
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'RUNNING',  -- RUNNING, COMPLETED, FAILED, CANCELLED
            dichotomy TEXT NOT NULL,                  -- TRAJECTORY, VALENCE, AGENCY
            generator_personas TEXT NOT NULL,         -- JSON list ["GREEN","AMBER"]
            critic_personas TEXT NOT NULL,            -- JSON list ["GRAY"]
            topic_scope TEXT NOT NULL,                -- JSON list of slugs or "ALL"
            duration_sec REAL,
            tokens_total INTEGER,
            cost_usd REAL,
            error_text TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS ideations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            persona TEXT NOT NULL,
            topic_slug TEXT NOT NULL,
            condition_hypothesis TEXT NOT NULL,
            prediction_text TEXT NOT NULL,
            resolution_criteria TEXT NOT NULL,
            deadline TEXT NOT NULL,
            conditional_probability REAL NOT NULL,
            linked_topic_slug TEXT,
            linked_hypothesis TEXT,
            tags TEXT,                                 -- JSON list
            reasoning TEXT,                            -- model's internal justification
            model_name TEXT                            -- e.g. claude-haiku-4-5
        );

        CREATE TABLE IF NOT EXISTS vetting (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ideation_id INTEGER NOT NULL REFERENCES ideations(id) ON DELETE CASCADE,
            run_id INTEGER NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
            vetted_at TEXT NOT NULL,
            persona TEXT NOT NULL,                     -- vetting persona (usually Sonnet in paired lens)
            verdict TEXT NOT NULL,                     -- APPROVE, REJECT, MODIFY
            reasoning TEXT,
            sub_checks TEXT,                           -- JSON: {falsifiable, deadline_ok, duplicate, cpt_aligned}
            modified_text TEXT,                        -- if verdict=MODIFY, the revised prediction
            modified_criteria TEXT,
            modified_deadline TEXT,
            modified_probability REAL,
            model_name TEXT
        );

        CREATE TABLE IF NOT EXISTS meta_lint (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
            linted_at TEXT NOT NULL,
            critic_persona TEXT NOT NULL,              -- GRAY
            portfolio_narrative TEXT NOT NULL,
            shared_assumptions TEXT,                   -- JSON list
            blind_spots TEXT,                          -- JSON list
            approve_ids TEXT,                          -- JSON list of ideation_ids
            drop_ids TEXT,                             -- JSON list of ideation_ids
            modify_suggestions TEXT,                   -- JSON: {ideation_id: suggestion_text}
            gap_fill_suggestions TEXT,                 -- JSON list of new prediction suggestions
            model_name TEXT
        );

        CREATE TABLE IF NOT EXISTS approved_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
            ideation_id INTEGER NOT NULL REFERENCES ideations(id) ON DELETE CASCADE,
            prediction_id TEXT NOT NULL,               -- cp_NNN in topic JSON
            topic_slug TEXT NOT NULL,
            final_source TEXT NOT NULL,                -- HAIKU_RAW, SONNET_MODIFIED, OPUS_GAP_FILL
            lens TEXT NOT NULL,                        -- GREEN, AMBER, BLUE, RED, VIOLET, OCHRE
            lens_agreement TEXT,                       -- JSON list if multiple lenses converged
            approved_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
            captured_at TEXT NOT NULL,
            dichotomy TEXT NOT NULL,
            portfolio_character TEXT,                  -- narrative summary
            domain_coverage TEXT,                      -- JSON: {ECON: 5, DIPLO: 3, ...}
            convergence_rate REAL,                     -- fraction of predictions both lenses agreed on
            critic_narrative TEXT,                     -- Gray's critique text
            critic_persona TEXT
        );

        CREATE TABLE IF NOT EXISTS sweep_lock (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            run_id INTEGER,
            locked_at TEXT,
            pid INTEGER
        );

        -- v0.3: per-prediction, per-critic verdicts. One row per (ideation, critic).
        CREATE TABLE IF NOT EXISTS critic_verdicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
            ideation_id INTEGER NOT NULL REFERENCES ideations(id) ON DELETE CASCADE,
            critic_persona TEXT NOT NULL,              -- AMBER | BLUE | GRAY | GREEN | OCHRE | RED | VIOLET
            verdict TEXT NOT NULL,                      -- APPROVE | MODIFY | DROP | NEUTRAL
            reasoning TEXT,
            modified_suggestion TEXT,                   -- if MODIFY, the suggested revision
            vetted_at TEXT NOT NULL,
            model_name TEXT,
            UNIQUE(run_id, ideation_id, critic_persona)
        );
        CREATE INDEX IF NOT EXISTS idx_cv_run ON critic_verdicts(run_id);
        CREATE INDEX IF NOT EXISTS idx_cv_ideation ON critic_verdicts(ideation_id);
        CREATE INDEX IF NOT EXISTS idx_cv_critic ON critic_verdicts(critic_persona);

        CREATE INDEX IF NOT EXISTS idx_ideations_run ON ideations(run_id);
        CREATE INDEX IF NOT EXISTS idx_ideations_topic ON ideations(topic_slug);
        CREATE INDEX IF NOT EXISTS idx_vetting_ideation ON vetting(ideation_id);
        CREATE INDEX IF NOT EXISTS idx_approved_run ON approved_predictions(run_id);
        CREATE INDEX IF NOT EXISTS idx_approved_topic ON approved_predictions(topic_slug);
        CREATE INDEX IF NOT EXISTS idx_snapshots_time ON portfolio_snapshots(captured_at);

        -- Initialize lock row if it doesn't exist
        INSERT OR IGNORE INTO sweep_lock (id, run_id, locked_at, pid) VALUES (1, NULL, NULL, NULL);
        """)


def acquire_lock(pid: int = None) -> int | None:
    """
    Try to acquire the single-writer lock. Returns run_id if acquired,
    None if another sweep is already running.
    """
    import os
    if pid is None:
        pid = os.getpid()

    with connect() as c:
        cur = c.execute("SELECT run_id, locked_at, pid FROM sweep_lock WHERE id = 1")
        row = cur.fetchone()
        if row and row["run_id"] is not None:
            return None  # locked
        # Create a placeholder run and take the lock
        cur = c.execute(
            "INSERT INTO agent_runs (started_at, status, dichotomy, generator_personas, critic_personas, topic_scope) "
            "VALUES (?, 'INITIALIZING', '', '[]', '[]', '[]')",
            (_now_iso(),)
        )
        run_id = cur.lastrowid
        c.execute(
            "UPDATE sweep_lock SET run_id = ?, locked_at = ?, pid = ? WHERE id = 1",
            (run_id, _now_iso(), pid)
        )
        return run_id


def release_lock(run_id: int):
    """Release the sweep lock."""
    with connect() as c:
        c.execute(
            "UPDATE sweep_lock SET run_id = NULL, locked_at = NULL, pid = NULL "
            "WHERE id = 1 AND run_id = ?",
            (run_id,)
        )


def start_run(run_id: int, dichotomy: str, generator_personas: list, critic_personas: list, topic_scope: list):
    """Update the run with its actual parameters."""
    with connect() as c:
        c.execute(
            "UPDATE agent_runs SET status = 'RUNNING', dichotomy = ?, "
            "generator_personas = ?, critic_personas = ?, topic_scope = ? WHERE id = ?",
            (dichotomy, json.dumps(generator_personas), json.dumps(critic_personas),
             json.dumps(topic_scope), run_id)
        )


def log_ideation(run_id: int, persona: str, topic_slug: str, condition_hypothesis: str,
                 prediction_text: str, resolution_criteria: str, deadline: str,
                 conditional_probability: float, linked_topic_slug: str = None,
                 linked_hypothesis: str = None, tags: list = None, reasoning: str = None,
                 model_name: str = None) -> int:
    """Log a Haiku proposal. Returns ideation_id."""
    with connect() as c:
        cur = c.execute(
            "INSERT INTO ideations (run_id, created_at, persona, topic_slug, "
            "condition_hypothesis, prediction_text, resolution_criteria, deadline, "
            "conditional_probability, linked_topic_slug, linked_hypothesis, tags, "
            "reasoning, model_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, _now_iso(), persona, topic_slug, condition_hypothesis,
             prediction_text, resolution_criteria, deadline, conditional_probability,
             linked_topic_slug, linked_hypothesis, json.dumps(tags or []), reasoning,
             model_name)
        )
        return cur.lastrowid


def log_vetting(ideation_id: int, run_id: int, persona: str, verdict: str,
                reasoning: str = None, sub_checks: dict = None,
                modified_text: str = None, modified_criteria: str = None,
                modified_deadline: str = None, modified_probability: float = None,
                model_name: str = None):
    """Log a Sonnet vetting verdict."""
    with connect() as c:
        c.execute(
            "INSERT INTO vetting (ideation_id, run_id, vetted_at, persona, verdict, "
            "reasoning, sub_checks, modified_text, modified_criteria, modified_deadline, "
            "modified_probability, model_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ideation_id, run_id, _now_iso(), persona, verdict, reasoning,
             json.dumps(sub_checks or {}), modified_text, modified_criteria,
             modified_deadline, modified_probability, model_name)
        )


def log_meta_lint(run_id: int, critic_persona: str, portfolio_narrative: str,
                  shared_assumptions: list = None, blind_spots: list = None,
                  approve_ids: list = None, drop_ids: list = None,
                  modify_suggestions: dict = None, gap_fill_suggestions: list = None,
                  model_name: str = None):
    """Log the Opus meta-lint output."""
    with connect() as c:
        c.execute(
            "INSERT INTO meta_lint (run_id, linted_at, critic_persona, portfolio_narrative, "
            "shared_assumptions, blind_spots, approve_ids, drop_ids, modify_suggestions, "
            "gap_fill_suggestions, model_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, _now_iso(), critic_persona, portfolio_narrative,
             json.dumps(shared_assumptions or []), json.dumps(blind_spots or []),
             json.dumps(approve_ids or []), json.dumps(drop_ids or []),
             json.dumps(modify_suggestions or {}), json.dumps(gap_fill_suggestions or []),
             model_name)
        )


def log_critic_verdict(run_id: int, ideation_id: int, critic_persona: str,
                        verdict: str, reasoning: str = None,
                        modified_suggestion: str = None, model_name: str = None):
    """
    Log a single critic's verdict on a single ideation.

    verdict: APPROVE | MODIFY | DROP | NEUTRAL

    Idempotent via UNIQUE(run_id, ideation_id, critic_persona) — same critic
    can't double-vote. Re-logging replaces the existing verdict via INSERT OR REPLACE.
    """
    valid = {"APPROVE", "MODIFY", "DROP", "NEUTRAL"}
    if verdict not in valid:
        raise ValueError(f"Invalid verdict '{verdict}'. Must be one of {valid}")
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO critic_verdicts "
            "(run_id, ideation_id, critic_persona, verdict, reasoning, "
            "modified_suggestion, vetted_at, model_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, ideation_id, critic_persona.upper(), verdict, reasoning,
             modified_suggestion, _now_iso(), model_name)
        )


def get_critic_verdicts_for_ideation(ideation_id: int) -> dict:
    """
    Returns {critic_persona: {verdict, reasoning, modified_suggestion}, ...}
    for a single ideation. Used when writing the final prediction to the topic JSON.
    """
    out = {}
    with connect() as c:
        cur = c.execute(
            "SELECT critic_persona, verdict, reasoning, modified_suggestion "
            "FROM critic_verdicts WHERE ideation_id = ?", (ideation_id,)
        )
        for r in cur.fetchall():
            out[r["critic_persona"]] = {
                "verdict": r["verdict"],
                "reasoning": r["reasoning"],
                "modified_suggestion": r["modified_suggestion"],
            }
    return out


def count_drops_for_ideation(ideation_id: int) -> int:
    """Count how many critics DROPPED this ideation (for consensus rule)."""
    with connect() as c:
        cur = c.execute(
            "SELECT COUNT(*) as n FROM critic_verdicts "
            "WHERE ideation_id = ? AND verdict = 'DROP'", (ideation_id,)
        )
        return cur.fetchone()["n"]


def log_approved_prediction(run_id: int, ideation_id: int, prediction_id: str,
                            topic_slug: str, final_source: str, lens: str,
                            lens_agreement: list = None):
    """Link an approved prediction back to its ideation row."""
    with connect() as c:
        c.execute(
            "INSERT INTO approved_predictions (run_id, ideation_id, prediction_id, "
            "topic_slug, final_source, lens, lens_agreement, approved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, ideation_id, prediction_id, topic_slug, final_source, lens,
             json.dumps(lens_agreement) if lens_agreement else None, _now_iso())
        )


def log_portfolio_snapshot(run_id: int, dichotomy: str, portfolio_character: str = None,
                           domain_coverage: dict = None, convergence_rate: float = None,
                           critic_narrative: str = None, critic_persona: str = "GRAY"):
    """Record the portfolio snapshot for this sweep."""
    with connect() as c:
        c.execute(
            "INSERT INTO portfolio_snapshots (run_id, captured_at, dichotomy, "
            "portfolio_character, domain_coverage, convergence_rate, critic_narrative, "
            "critic_persona) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, _now_iso(), dichotomy, portfolio_character,
             json.dumps(domain_coverage or {}), convergence_rate, critic_narrative,
             critic_persona)
        )


def finish_run(run_id: int, status: str = "COMPLETED", duration_sec: float = None,
               tokens_total: int = None, cost_usd: float = None, error_text: str = None):
    """Finalize a run."""
    with connect() as c:
        c.execute(
            "UPDATE agent_runs SET status = ?, finished_at = ?, duration_sec = ?, "
            "tokens_total = ?, cost_usd = ?, error_text = ? WHERE id = ?",
            (status, _now_iso(), duration_sec, tokens_total, cost_usd, error_text, run_id)
        )


def get_meta_lint_narrative(run_id: int, critic_persona: str) -> str | None:
    """Get the portfolio narrative written by a specific critic for a run."""
    with connect() as c:
        cur = c.execute(
            "SELECT portfolio_narrative FROM meta_lint "
            "WHERE run_id = ? AND critic_persona = ? ORDER BY id DESC LIMIT 1",
            (run_id, critic_persona)
        )
        row = cur.fetchone()
        return row["portfolio_narrative"] if row else None


def get_recent_runs(limit: int = 10) -> list:
    """Get recent sweep runs for display."""
    with connect() as c:
        cur = c.execute(
            "SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in cur.fetchall()]


def get_run_detail(run_id: int) -> dict:
    """Get full detail of a run: ideations, vetting, meta-lint, approvals."""
    with connect() as c:
        run = dict(c.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone() or {})
        if not run:
            return {}
        run["ideations"] = [dict(r) for r in c.execute(
            "SELECT * FROM ideations WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()]
        run["vetting"] = [dict(r) for r in c.execute(
            "SELECT * FROM vetting WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()]
        run["meta_lint"] = [dict(r) for r in c.execute(
            "SELECT * FROM meta_lint WHERE run_id = ?", (run_id,)
        ).fetchall()]
        run["approved"] = [dict(r) for r in c.execute(
            "SELECT * FROM approved_predictions WHERE run_id = ?", (run_id,)
        ).fetchall()]
        run["snapshots"] = [dict(r) for r in c.execute(
            "SELECT * FROM portfolio_snapshots WHERE run_id = ?", (run_id,)
        ).fetchall()]
        return run


if __name__ == "__main__":
    init_schema()
    print(f"Schema initialized at {DB_PATH}")
    print(f"Journal mode: ", end="")
    with connect() as c:
        print(c.execute("PRAGMA journal_mode").fetchone()[0])
