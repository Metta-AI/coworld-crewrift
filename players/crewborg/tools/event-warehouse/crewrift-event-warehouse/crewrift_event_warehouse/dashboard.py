from __future__ import annotations

import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ASSETS_DIR = Path(__file__).parent / "assets"
MAX_ROWS = 5000

# Preset queries shown as one-click buttons in the UI. Each is a real answer to
# one of the example questions the warehouse was built for. {events}/{players}
# are substituted with the read_parquet() expressions for the open dataset.
PRESETS: list[dict[str, str]] = [
    {
        "title": "Room visits by policy",
        "sql": (
            "SELECT policy_name,\n"
            "       json_extract_string(value, '$.room') AS room,\n"
            "       count(*) AS visits\n"
            "FROM {events}\n"
            "WHERE key = 'entered_room' AND slot >= 0 AND policy_name IS NOT NULL\n"
            "GROUP BY policy_name, room\n"
            "ORDER BY visits DESC"
        ),
    },
    {
        "title": "Most common room overall",
        "sql": (
            "SELECT json_extract_string(value, '$.room') AS room, count(*) AS visits\n"
            "FROM {events}\n"
            "WHERE key = 'entered_room' AND slot >= 0\n"
            "GROUP BY room ORDER BY visits DESC"
        ),
    },
    {
        "title": "Most common routes",
        "sql": (
            "SELECT json_extract_string(value, '$.origin_room') AS from_room,\n"
            "       json_extract_string(value, '$.target_name') AS to_room,\n"
            "       count(*) AS trips\n"
            "FROM {events}\n"
            "WHERE key = 'headed_to' AND json_extract_string(value, '$.target_kind') = 'room'\n"
            "GROUP BY from_room, to_room ORDER BY trips DESC"
        ),
    },
    {
        "title": "Following: who tails whom (with followed role)",
        "sql": (
            "SELECT f.policy_name AS follower,\n"
            "       tgt.policy_name AS followed,\n"
            "       tgt.role AS followed_role,\n"
            "       count(*) AS intervals,\n"
            "       round(avg(json_extract(e.value, '$.alignment_ratio')::double), 3) AS mean_alignment\n"
            "FROM {events} e\n"
            "JOIN {players} f   ON f.episode_id = e.episode_id AND f.slot = e.slot\n"
            "JOIN {players} tgt ON tgt.episode_id = e.episode_id\n"
            "                  AND tgt.slot = json_extract(e.value, '$.target')::int\n"
            "WHERE e.key = 'following_interval'\n"
            "GROUP BY follower, followed, followed_role\n"
            "ORDER BY intervals DESC"
        ),
    },
    {
        "title": "Chase distance closed, by policy",
        "sql": (
            "SELECT policy_name,\n"
            "       count(*) AS chases,\n"
            "       round(avg(json_extract(value, '$.start_distance')::double\n"
            "                 - json_extract(value, '$.end_distance')::double), 2) AS mean_distance_closed\n"
            "FROM {events}\n"
            "WHERE key = 'chase_interval' AND slot >= 0\n"
            "GROUP BY policy_name ORDER BY chases DESC"
        ),
    },
    {
        "title": "Role outcomes by policy (win rate)",
        "sql": (
            "SELECT policy_name, role,\n"
            "       count(*) AS episodes,\n"
            "       round(avg(win::int), 3) AS win_rate,\n"
            "       round(avg(score), 1) AS mean_score\n"
            "FROM {players}\n"
            "WHERE policy_name IS NOT NULL\n"
            "GROUP BY policy_name, role ORDER BY policy_name, role"
        ),
    },
    {
        "title": "Event key counts",
        "sql": "SELECT key, count(*) AS n FROM {events} GROUP BY key ORDER BY n DESC",
    },
]


@dataclass
class Dataset:
    """Resolves the read_parquet() expressions for an open warehouse directory
    and runs queries against a shared DuckDB connection."""

    out_dir: Path

    def __post_init__(self) -> None:
        import duckdb

        self._events_glob = str(self.out_dir / "events" / "**" / "*.parquet")
        self._players_path = self.out_dir / "episode_players.parquet"
        self._con = duckdb.connect()
        manifest_path = self.out_dir / "manifest.json"
        self.manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    @property
    def events_expr(self) -> str:
        return f"read_parquet('{self._events_glob}', hive_partitioning = true)"

    @property
    def players_expr(self) -> str:
        return f"read_parquet('{self._players_path}')"

    def render(self, sql_template: str) -> str:
        return sql_template.format(events=self.events_expr, players=self.players_expr)

    def run(self, sql: str) -> dict[str, Any]:
        """Run user SQL. {events}/{players} placeholders are expanded so presets
        and hand-written queries share the same table names. Results are capped
        at MAX_ROWS so a careless query cannot stream the whole dataset to the UI.
        """
        rendered = self.render(sql)
        relation = self._con.sql(rendered)
        columns = list(relation.columns)
        rows = relation.limit(MAX_ROWS + 1).fetchall()
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        return {
            "columns": columns,
            "rows": [[_jsonable(v) for v in row] for row in rows],
            "truncated": truncated,
        }

    def schema(self) -> dict[str, Any]:
        events_cols = self._con.sql(f"SELECT * FROM {self.events_expr} LIMIT 0").columns
        players_cols = self._con.sql(f"SELECT * FROM {self.players_expr} LIMIT 0").columns
        return {
            "events": list(events_cols),
            "episode_players": list(players_cols),
            "manifest": self.manifest,
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _handler_class(dataset: Dataset) -> type[BaseHTTPRequestHandler]:
    index_html = (ASSETS_DIR / "index.html").read_text()
    presets_json = json.dumps(PRESETS)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # quiet by default
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                self._send(200, index_html.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/api/schema":
                self._send_json(200, dataset.schema())
            elif self.path == "/api/presets":
                self._send(200, presets_json.encode("utf-8"), "application/json")
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/api/query":
                self._send_json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                result = dataset.run(payload["sql"])
            except KeyError:
                self._send_json(400, {"error": "missing 'sql'"})
            except Exception as exc:  # surface DuckDB errors to the UI
                self._send_json(200, {"error": f"{type(exc).__name__}: {exc}"})
            else:
                self._send_json(200, result)

    return Handler


def serve(out_dir: Path, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    dataset = Dataset(Path(out_dir))
    server = ThreadingHTTPServer((host, port), _handler_class(dataset))
    print(f"warehouse dashboard: http://{host}:{port}  (dataset: {out_dir})")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        server.server_close()
