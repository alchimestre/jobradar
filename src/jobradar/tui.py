"""Textual TUI: one table over every stored posting."""

from __future__ import annotations

import time
import webbrowser

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Input, Static

from . import store
from .cli import _db_path
from .config import load_config, root
from .hooks import NO_HOOK, run_hook
from .scan import scan

# `status` sits near the front on purpose: it is the column the user changes, and it
# used to fall off the right edge of any terminal narrower than ~127 columns.
COLUMNS: list[tuple[str, str, int]] = [
    ("dec", "dec", 5),
    ("status", "status", 9),
    ("lane", "lane", 9),
    ("score", "score", 5),
    ("company", "company", 20),
    ("title", "title", 42),
    ("location", "location", 18),
    ("region", "region", 9),
    ("lang", "lang", 5),
    ("seen", "seen", 10),
]

STATUS_STYLE = {
    "new": "dim",
    "shortlist": "bold yellow",
    "applied": "bold green",
    "rejected": "red",
    "ignored": "dim strike",
}
DECISION_STYLE = {"high": "bold cyan", "review": "", "low": "dim", "out": "dim red"}


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
        self.counts_line = ""
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

    def load_rows(self, keep_uid: str | None = None) -> None:
        """Reload from the store, keeping the cursor on `keep_uid` when it survives.

        Out-of-scope rows (non-EU/EMEA geography, German-language JDs) never appear
        unless the decision filter is explicitly set to `out`.
        """
        rows = store.query(
            self.conn,
            decision=self.decision,
            min_score=self.min_score,
            search=self.search or None,
            limit=500,
        )
        self.jobs = [store.row_to_dict(r) for r in rows]
        table = self.query_one("#jobs", DataTable)
        table.clear()
        target: int | None = None
        for index, job in enumerate(self.jobs):
            values = self._cells(job)
            table.add_row(*[values[key] for key, _, _ in COLUMNS], key=job["uid"])
            if keep_uid and job["uid"] == keep_uid:
                target = index
        counts = store.counts(self.conn)
        by_status = counts["by_status"]
        scope = []
        if self.decision:
            scope.append(f"decision={self.decision}")
        if self.min_score:
            scope.append(f"min_score={self.min_score}")
        if self.search:
            scope.append(f"search={self.search!r}")
        self.counts_line = (
            f"{len(self.jobs)} shown · {counts['in_scope']} in scope · high {counts['by_decision'].get('high', 0)} · "
            f"new {by_status.get('new', 0)} · applied {by_status.get('applied', 0)}"
            + (f" · {counts['out_of_scope']} out-of-scope hidden (f cycles to them)" if counts["out_of_scope"] else "")
            + (f" · {' '.join(scope)}" if scope else "")
        )
        self.set_status(self.counts_line)
        if target is not None:
            table.move_cursor(row=target)
        elif self.jobs:
            self.show_detail(0)

    def _cells(self, job: dict) -> dict:
        return {
            "dec": Text(job["decision"], style=DECISION_STYLE.get(job["decision"], "")),
            "status": Text(job["status"], style=STATUS_STYLE.get(job["status"], "")),
            "lane": job["lane"],
            "score": str(job["score"]),
            "company": job["company"],
            "title": job["title"],
            "location": job["location"],
            "region": job["region"],
            "lang": job["language"],
            "seen": self._date(job["first_seen"]),
        }

    @staticmethod
    def _date(epoch: int) -> str:
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
            f"status={job['status']} lane={job['lane']} score={job['score']} scope={job['scope']} "
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
        row = store.get(self.conn, job["uid"])
        payload = store.row_to_dict(row) if row else job
        self.load_rows(keep_uid=job["uid"])
        label = f"{job['company']} — {job['title'][:38]} → {status}"
        self.set_status(f"{self.counts_line} · {label} · running {status} hook…")
        self.notify(label)
        self.run_status_hook(status, payload)

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
        cycle = {"high": "review", "review": "out", "out": None, None: "high"}
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

    # ---- workers ----------------------------------------------------------

    @work(thread=True)
    def run_status_hook(self, status: str, job: dict) -> None:
        """Run the configured hook off the UI thread; `gh` calls are not instant."""
        ok, message = run_hook(status, self.cfg, job)
        self.call_from_thread(self.hook_done, status, job, ok, message)

    def hook_done(self, status: str, job: dict, ok: bool, message: str) -> None:
        label = f"{job['company']} — {job['title'][:38]} → {status}"
        line = f"{self.counts_line} · {label}" if self.counts_line else label
        if message and message != NO_HOOK:
            line = f"{line} · {message}"
        self.set_status(line)
        if not ok:
            self.notify(message, severity="warning", timeout=10)

    def action_rescan(self) -> None:
        self.run_scan()

    @work(thread=True, exclusive=True)
    def run_scan(self) -> None:
        self.call_from_thread(self.set_status, "scanning…")
        try:
            # A worker thread gets its own connection: sqlite3 connections are not
            # shareable across threads.
            conn = store.connect(self.db)
            try:
                report = scan(
                    self.cfg,
                    conn,
                    progress=lambda message: self.call_from_thread(self.set_status, message),
                )
            finally:
                conn.close()
            self.call_from_thread(self.set_status, report.summary())
            for row in report.errors[:3]:
                self.call_from_thread(self.notify, f"{row['source']}: {row['error']}", severity="warning")
        except Exception as exc:  # never take the UI down with the scan
            self.call_from_thread(self.set_status, f"scan failed: {type(exc).__name__}: {exc}")
        finally:
            self.call_from_thread(self.load_rows)
