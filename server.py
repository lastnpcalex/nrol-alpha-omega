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


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIR), **kwargs)

    def do_GET(self):
        path = self.path.split("?")[0]  # strip query string

        # Dashboard
        if path == "/" or path == "/index.html":
            self.path = "/dashboard.html"
            return super().do_GET()

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
    print(f"NRL-ALPHA OMEGA — Epistemic Bayesian Estimator")
    print(f"  Topics:    {topic_count} active")
    print(f"  Local:     http://localhost:{PORT}")
    ts_ip = get_tailscale_ip()
    if ts_ip:
        print(f"  Tailscale: http://{ts_ip}:{PORT}")
    print()
    server.serve_forever()
