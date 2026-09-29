"""Textual TUI: one table over every stored posting."""

from __future__ import annotations

import time
import webbrowser

from textual import work
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Input, Static

from . import store
from .cli import _db_path
from .config import load_config, root
from .scan import scan

COLUMNS: list[tuple[str, str, int]] = [
    ("dec", "dec", 6),
    ("score", "score", 5),
    ("lane", "lane", 9),
    ("company", "company", 20),
    ("title", "title", 46),
    ("location", "location", 20),
    ("status", "status", 9),
    ("seen", "seen", 10),
]


class RadarApp(App):
    """Terminal UI over the jobradar store."""

    CSS = """
    #status { height: 1; background: $boost; padding: 0 1; }
    #detail { height: 6; border: round $primary; padding: 0 1; }
    DataTable { height: 1fr; }
    """
    BINDINGS = [
        ("r", "rescan", "Rescan"),
        ("o", "open", "Open"),
        ("s", "shortlist", "Shortlist"),
        ("a", "applied", "Applied"),
        ("x", "ignore", "Ignore"),
        ("/", "search", "Search"),
        ("f", "cycle_filter", "Filter"),
        ("e", "export", "Export"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        min_score: int | None = None,
        decision: str | None = None,
        db: str | None = None,
        cfg: dict | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg or load_config()
        self.db = db or _db_path(self.cfg)
        self.min_score = min_score
        self.decision = decision
        self.search = ""
        self.status_message = ""
        self.jobs: list[dict] = []
        self.conn = store.connect(self.db)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("ready", id="status")
        yield Input(placeholder="filter: company / title / location", id="search")
        yield DataTable(id="jobs", zebra_stripes=True)
        yield Static("", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#jobs", DataTable)
        table.cursor_type = "row"
        table.add_columns(*[label for _, label, _ in COLUMNS])
        self.load_rows()

    # ---- data -------------------------------------------------------------

    def load_rows(self) -> None:
        rows = store.query(
            self.conn, decision=self.decision, min_score=self.min_score, search=self.search or None, limit=500
        )
        self.jobs = [store.row_to_dict(r) for r in rows]
        table = self.query_one("#jobs", DataTable)
        table.clear()
        for job in self.jobs:
            seen = self._date(job["first_seen"])
            values = {
                "dec": job["decision"],
                "score": str(job["score"]),
                "lane": job["lane"],
                "company": job["company"],
                "title": job["title"],
                "location": job["location"],
                "status": job["status"],
                "seen": seen,
            }
            table.add_row(*[values[key] for key, _, _ in COLUMNS], key=job["uid"])
        counts = store.counts(self.conn)
        scope = []
        if self.decision:
            scope.append(f"decision={self.decision}")
        if self.min_score:
            scope.append(f"min_score={self.min_score}")
        if self.search:
            scope.append(f"search={self.search!r}")
        self.set_status(
            f"{len(self.jobs)} shown · {counts['total']} stored · "
            f"high {counts['by_decision'].get('high', 0)} · new {counts['by_status'].get('new', 0)}"
            + (f" · {' '.join(scope)}" if scope else "")
        )
        if self.jobs:
            self.show_detail(0)

    @staticmethod
    def _date(epoch: int) -> str:
        import time

        return time.strftime("%Y-%m-%d", time.localtime(epoch))

    def current(self) -> dict | None:
        table = self.query_one("#jobs", DataTable)
        index = table.cursor_row
        if 0 <= index < len(self.jobs):
            return self.jobs[index]
        return None

    def set_status(self, message: str) -> None:
        self.status_message = message
        self.query_one("#status", Static).update(message)

    def show_detail(self, index: int) -> None:
        if not (0 <= index < len(self.jobs)):
            return
        job = self.jobs[index]
        detail = (
            f"[b]{job['company']} — {job['title']}[/b]\n"
            f"{job['url']}\n"
            f"lane={job['lane']} score={job['score']} scope={job['scope']} "
            f"sources={','.join(job['sources'])} published={job['published'] or 'n/a'}\n"
            f"reasons: {', '.join(job['reasons'])}\n"
            f"uid={job['uid']}"
        )
        self.query_one("#detail", Static).update(detail)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.show_detail(event.cursor_row)

    # ---- actions ----------------------------------------------------------

    def action_open(self) -> None:
        job = self.current()
        if job and job["url"]:
            webbrowser.open(job["url"])
            self.set_status(f"opened {job['url']}")

    def _mark(self, status: str) -> None:
        job = self.current()
        if not job:
            return
        store.set_status(self.conn, job["uid"], status)
        self.notify(f"{job['company']} — {job['title']} → {status}")
        self.load_rows()

    def action_shortlist(self) -> None:
        self._mark("shortlist")

    def action_applied(self) -> None:
        self._mark("applied")

    def action_ignore(self) -> None:
        self._mark("ignored")

    def action_search(self) -> None:
        self.query_one("#search", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.search = event.value.strip()
        self.load_rows()
        self.query_one("#jobs", DataTable).focus()

    def action_cycle_filter(self) -> None:
        cycle = {"high": "review", "review": None, None: "high"}
        self.decision = cycle[self.decision]
        self.load_rows()

    def action_export(self) -> None:
        path = root() / "exports"
        path.mkdir(exist_ok=True)
        rows = store.query(self.conn, decision=self.decision, min_score=self.min_score, limit=500)
        target = path / f"jobradar-{self._date(int(time.time()))}.md"
        lines = [f"# jobradar export — {len(rows)} postings", ""]
        for row in rows:
            lines.append(
                f"- [ ] **{row['company']} — {row['title']}** · `{row['lane']}` · score {row['score']} · "
                f"{row['location'] or 'n/a'} · {row['url']} · `{row['uid']}`"
            )
        target.write_text("\n".join(lines) + "\n")
        self.notify(f"exported {len(rows)} rows → {target}")

    # ---- scan worker ------------------------------------------------------

    def action_rescan(self) -> None:
        self.run_scan()

    @work(thread=True, exclusive=True)
    def run_scan(self) -> None:
        self.call_from_thread(self.set_status, "scanning…")
        try:
            report = scan(
                self.cfg,
                self.conn,
                progress=lambda message: self.call_from_thread(self.set_status, message),
            )
            self.call_from_thread(self.set_status, report.summary())
            for row in report.errors[:3]:
                self.call_from_thread(self.notify, f"{row['source']}: {row['error']}", severity="warning")
        except Exception as exc:  # never take the UI down with the scan
            self.call_from_thread(self.set_status, f"scan failed: {type(exc).__name__}: {exc}")
        finally:
            self.call_from_thread(self.load_rows)
