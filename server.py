"""
NRL-Alpha Omega — Multi-Topic Dashboard Server

Serves the generalized dashboard and topic state files.
Routes:
  GET /              → dashboard.html
  GET /topics        → JSON list of active topics
  GET /topics/{slug}/state.json → topic state file
  GET /topics/{slug}/briefs/    → list of briefs for topic
"""

import http.server
import json
import os
from pathlib import Path

PORT = int(os.environ.get("ALPHA_OMEGA_PORT", 8098))
DIR = Path(__file__).parent
TOPICS_DIR = DIR / "topics"
BRIEFS_DIR = DIR / "briefs"
DASHBOARDS_DIR = DIR / "dashboards"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIR), **kwargs)

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/triage":
            return self._handle_triage()

        if path == "/trigger":
            return self._handle_trigger()

        self.send_error(404)

    def _handle_trigger(self):
        """
        Queue an action-alert trigger for Claude to process.

        Body: {action, slug, severity, context}
            action    — one of: fire_indicator, run_red_team,
                         review_topic_design, mark_reviewed
            slug      — topic slug
            severity  — alert severity (REVIEW_NEEDED | ATTENTION)
            context   — dict with action-specific fields (indicator_id,
                         evidence_id, evidence_ids, alert_signature, reason)

        Writes a filled-in copy of canvas/triggers/review-action.md to
        canvas/triggers/pending/<timestamp>-<slug>-<action>.md so Claude
        can pick it up and route through the appropriate skill.
        """
        from datetime import datetime, timezone
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        action = (data.get("action") or "").strip()
        slug = (data.get("slug") or "").strip()
        severity = (data.get("severity") or "ATTENTION").strip()
        context = data.get("context") or {}

        allowed = {"fire_indicator", "run_red_team", "review_topic_design", "mark_reviewed", "review_dependencies", "rederive_downstream", "update_assumption", "accept_drift", "set_topic_lens", "rebuild_topic", "promote_replay"}
        if action not in allowed:
            self.send_error(400, f"invalid action (allowed: {sorted(allowed)})")
            return
        if not slug:
            self.send_error(400, "slug required")
            return

        # Find project root by walking up from this file until we hit canvas/
        here = Path(__file__).resolve()
        project_root = here.parent.parent
        tpl_path = project_root / "canvas" / "triggers" / "review-action.md"
        pending_dir = project_root / "canvas" / "triggers" / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)

        try:
            template = tpl_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.send_error(500, "review-action.md template missing")
            return

        context_json = json.dumps(context, ensure_ascii=False, indent=2)
        filled = (template
                  .replace("{{action}}", action)
                  .replace("{{slug}}", slug)
                  .replace("{{severity}}", severity)
                  .replace("{{context}}", context_json))

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # keep filenames filesystem-safe
        safe_slug = "".join(c for c in slug if c.isalnum() or c in "-_")[:48] or "unknown"
        out_path = pending_dir / f"{ts}-{safe_slug}-{action}.md"
        out_path.write_text(filled, encoding="utf-8")

        self._send_json({
            "ok": True,
            "trigger_file": str(out_path.relative_to(project_root)),
            "action": action,
            "slug": slug,
        })

    def _handle_triage(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        headline = data.get("headline", "")
        source = data.get("source")

        if not headline:
            self.send_error(400, "headline required")
            return

        try:
            from engine import triage_headline
            result = triage_headline(headline, source)
            self._send_json(result)
        except Exception as e:
            self.send_error(500, str(e))

    def _send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]  # strip query string

        # Dashboard
        if path == "/" or path == "/index.html":
            self.path = "/dashboard.html"
            return super().do_GET()

        # Mirror dashboard
        if path == "/mirror" or path == "/mirror.html":
            self.path = "/mirror.html"
            return super().do_GET()

        # Overview (cross-topic)
        if path == "/overview":
            return self._serve_overview()

        # Trajectories
        if path == "/trajectories" or path.startswith("/trajectories/"):
            slug = None
            if path.startswith("/trajectories/"):
                slug = path.split("/")[2] if len(path.split("/")) > 2 else None
            return self._serve_trajectories(slug)

        # Dependencies
        if path == "/dependencies":
            return self._serve_dependencies()

        # Topic list
        if path == "/topics":
            return self._serve_topic_list()

        # Topic state
        if path.startswith("/topics/") and path.endswith("/state.json"):
            slug = path.split("/")[2]
            return self._serve_topic_state(slug)

        # Topic governance report
        if path.startswith("/topics/") and path.endswith("/governance.json"):
            slug = path.split("/")[2]
            return self._serve_governance(slug)

        # Topic briefs list
        if path.startswith("/topics/") and path.endswith("/briefs/"):
            slug = path.split("/")[2]
            return self._serve_briefs_list(slug)

        # Topic brief file
        if path.startswith("/topics/") and "/briefs/" in path:
            parts = path.split("/")
            slug = parts[2]
            filename = parts[4] if len(parts) > 4 else ""
            brief_path = BRIEFS_DIR / slug / filename
            if brief_path.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.end_headers()
                self.wfile.write(brief_path.read_bytes())
                return
            self.send_error(404)
            return

        # Dashboard snapshots list
        if path == "/dashboards" or path == "/dashboards/":
            return self._serve_dashboards_list()

        # Dashboard snapshot file
        if path.startswith("/dashboards/") and path.endswith(".html"):
            parts = path.split("/")
            if len(parts) >= 4:
                slug = parts[2]
                filename = parts[3]
                dash_path = DASHBOARDS_DIR / slug / filename
                if dash_path.exists():
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(dash_path.read_bytes())
                    return
            self.send_error(404)
            return

        # Fall through to static file serving
        return super().do_GET()

    def _serve_topic_list(self):
        topics = []
        if TOPICS_DIR.exists():
            for p in sorted(TOPICS_DIR.glob("*.json")):
                if p.stem.startswith("_"):
                    continue
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    meta = data.get("meta", {})
                    topics.append({
                        "slug": meta.get("slug", p.stem),
                        "title": meta.get("title", p.stem),
                        "status": meta.get("status", "UNKNOWN"),
                        "classification": meta.get("classification", "ROUTINE"),
                        "question": meta.get("question", ""),
                        "lastUpdated": meta.get("lastUpdated", ""),
                    })
                except (json.JSONDecodeError, KeyError):
                    continue

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(topics, ensure_ascii=False).encode("utf-8"))

    def _serve_topic_state(self, slug):
        path = TOPICS_DIR / f"{slug}.json"
        if not path.exists():
            self.send_error(404, f"Topic not found: {slug}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(path.read_bytes())

    def _serve_governance(self, slug):
        path = TOPICS_DIR / f"{slug}.json"
        if not path.exists():
            self.send_error(404, f"Topic not found: {slug}")
            return
        try:
            from governor import governance_report
            from engine import load_topic, update_day_count
            topic = load_topic(slug)
            update_day_count(topic)
            report = governance_report(topic)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(report, ensure_ascii=False).encode("utf-8"))
        except Exception as e:
            self.send_error(500, str(e))

    def _serve_dashboards_list(self):
        from engine import list_dashboards
        dashboards = list_dashboards()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(dashboards, ensure_ascii=False).encode("utf-8"))

    def _serve_overview(self):
        try:
            from engine import get_overview
            result = get_overview()
            self._send_json(result)
        except Exception as e:
            self.send_error(500, str(e))

    def _serve_trajectories(self, slug=None):
        try:
            from engine import get_trajectories
            result = get_trajectories(slug)
            self._send_json(result)
        except Exception as e:
            self.send_error(500, str(e))

    def _serve_dependencies(self):
        try:
            from framework.dependencies import build_dependency_graph
            result = build_dependency_graph()
            self._send_json(result)
        except Exception as e:
            self.send_error(500, str(e))

    def _serve_briefs_list(self, slug):
        brief_dir = BRIEFS_DIR / slug
        briefs = []
        if brief_dir.exists():
            for p in sorted(brief_dir.glob("*.md"), reverse=True):
                briefs.append({
                    "filename": p.name,
                    "url": f"/topics/{slug}/briefs/{p.name}",
                })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(briefs, ensure_ascii=False).encode("utf-8"))

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def log_message(self, format, *args):
        pass  # quiet


def get_tailscale_ip():
    import subprocess
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


if __name__ == "__main__":
    # Count topics
    topic_count = 0
    if TOPICS_DIR.exists():
        topic_count = len([
            p for p in TOPICS_DIR.glob("*.json")
            if not p.stem.startswith("_")
        ])

    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"NROL-Alpha Omega -- Current Thing delenda est")
    print(f"  Topics:    {topic_count} active")
    print(f"  Local:     http://localhost:{PORT}")
    ts_ip = get_tailscale_ip()
    if ts_ip:
        print(f"  Tailscale: http://{ts_ip}:{PORT}")
    print()
    server.serve_forever()
