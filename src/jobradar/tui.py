"""Textual TUI: one table over every stored posting."""

from __future__ import annotations

import json
import pathlib
import time
import webbrowser

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Input, Static

from . import store
from .cli import _db_path
from .config import load_config, root
from .hooks import NO_HOOK, run_hook
from .scan import scan

# (key, label, default width). The width is always applied to the column, never inferred
# from the data: one 1,191-character HN comment used to size the title column to the
# width of the comment and push every other column off the terminal.
#
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
    ("mode", "mode", 8),
    ("region", "region", 9),
    ("lang", "lang", 5),
    ("seen", "seen", 10),
]
COLUMN_LABELS = {key: label for key, label, _ in COLUMNS}
COLUMN_WIDTHS = {key: width for key, _, width in COLUMNS}

STATUS_STYLE = {
    "new": "dim",
    "shortlist": "bold yellow",
    "applied": "bold green",
    "rejected": "red",
    "ignored": "dim strike",
}
DECISION_STYLE = {"high": "bold cyan", "review": "", "low": "dim", "out": "dim red"}

# Layout state (column order and widths, sort) lives in `.jobradar/tui.json` next to the
# store, so the table looks the way it was left after a restart.
SORT_KEYS = ("smart", "score", "company", "title", "seen", "lane")
MIN_WIDTH = 4
WIDTH_STEP = 2


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
        ("t", "cycle_sort", "Sort"),
        ("[", "shrink_column", "−col"),
        ("]", "grow_column", "+col"),
        # "," and "." cannot be written as literal binding keys: Textual splits a key
        # string on commas. Their Unicode names are what the key event reports.
        Binding("comma", "focus_prev_column", "◀col", key_display=","),
        Binding("full_stop", "focus_next_column", "col▶", key_display="."),
        Binding("{", "move_column_left", "col◀", show=False),
        Binding("}", "move_column_right", "col▶", show=False),
        Binding("T", "reverse_sort", "Reverse", show=False),
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
        self.column_layout: list[tuple[str, int]] = [(key, width) for key, _, width in COLUMNS]
        self.column_focus = 0
        self.sort_key = "smart"
        self.sort_reverse = False
        self._load_layout()

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
        # The search box is the first focusable widget in the compose order, so without
        # this every documented key (r/f/s/a/x/t/[/]/…) is typed into it instead.
        table.focus()
        self.load_rows()

    # ---- layout state -----------------------------------------------------

    def layout_path(self) -> pathlib.Path:
        return pathlib.Path(self.db).parent / "tui.json"

    def _load_layout(self) -> None:
        data: dict = {}
        try:
            data = json.loads(self.layout_path().read_text())
        except (OSError, ValueError):
            data = {}
        stored: list[tuple[str, int]] = []
        for item in data.get("columns") or []:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                key, width = item
                if key in COLUMN_WIDTHS and isinstance(width, int):
                    stored.append((key, max(MIN_WIDTH, width)))
        # A stored layout only survives while it still describes exactly the known
        # columns; otherwise the defaults win and the new column is not invisible.
        if stored and {key for key, _ in stored} == set(COLUMN_WIDTHS):
            self.column_layout = stored
        if data.get("sort") in SORT_KEYS:
            self.sort_key = data["sort"]
        self.sort_reverse = bool(data.get("reverse"))
        # The cursor starts on `title`: it is the column that needs adjusting (and the one
        # that used to blow the table out), so `[`/`]` do something useful immediately.
        focus = [key for key, _ in self.column_layout].index("title") if "title" in COLUMN_WIDTHS else 0
        self.column_focus = max(0, min(focus, len(self.column_layout) - 1))

    def _save_layout(self) -> None:
        try:
            self.layout_path().write_text(
                json.dumps(
                    {
                        "columns": [[key, width] for key, width in self.column_layout],
                        "sort": self.sort_key,
                        "reverse": self.sort_reverse,
                    },
                    indent=2,
                )
                + "\n"
            )
        except OSError:  # a read-only store must not take the UI down
            pass

    # ---- data -------------------------------------------------------------

    def load_rows(self, keep_uid: str | None = None, requery: bool = True) -> None:
        """Reload (or just re-render) the table, keeping the cursor on `keep_uid`.

        Out-of-scope rows (non-target geography, German-language JDs) never appear
        unless the decision filter is explicitly set to `out`.
        """
        if requery:
            rows = store.query(
                self.conn,
                decision=self.decision,
                min_score=self.min_score,
                search=self.search or None,
                limit=500,
            )
            self.jobs = [store.row_to_dict(r) for r in rows]
        if keep_uid is None:
            current = self.current()
            keep_uid = current["uid"] if current else None
        self.apply_sort()
        table = self.query_one("#jobs", DataTable)
        widths = dict(self.column_layout)
        table.clear(columns=True)
        for key, width in self.column_layout:
            table.add_column(self._header(key), width=width, key=key)
        target: int | None = None
        for index, job in enumerate(self.jobs):
            cells = self._cells(job, widths)
            table.add_row(*[cells[key] for key, _ in self.column_layout], key=job["uid"])
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
        key, width = self.column_layout[self.column_focus]
        scope.append(f"sort={self.sort_key}{'↑' if self.sort_reverse else '↓'} · col={key}:{width}")
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

    def apply_sort(self) -> None:
        if self.sort_key == "smart":
            if self.sort_reverse:
                self.jobs.reverse()
            return
        self.jobs.sort(key=self._sort_value, reverse=self.sort_reverse)

    def _sort_value(self, job: dict):
        if self.sort_key == "score":
            return (-job["score"], job["company"].lower())
        if self.sort_key == "seen":
            return (-job["first_seen"], job["company"].lower())
        if self.sort_key == "company":
            return job["company"].lower()
        if self.sort_key == "title":
            return job["title"].lower()
        if self.sort_key == "lane":
            return (job["lane"], job["company"].lower())
        return 0

    def _header(self, key: str) -> Text:
        index = [column for column, _ in self.column_layout].index(key)
        return Text(COLUMN_LABELS[key], style="bold reverse" if index == self.column_focus else "")

    def _clip(self, value: str, width: int) -> str:
        """Bound a cell to its column, so one long title cannot resize the table."""
        limit = max(1, width - 2 * self.query_one("#jobs", DataTable).cell_padding)
        text = value or ""
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 1)].rstrip() + "…"

    def _cells(self, job: dict, widths: dict[str, int]) -> dict:
        return {
            "dec": Text(job["decision"], style=DECISION_STYLE.get(job["decision"], "")),
            "status": Text(job["status"], style=STATUS_STYLE.get(job["status"], "")),
            "lane": job["lane"],
            "score": str(job["score"]),
            "company": self._clip(job["company"], widths["company"]),
            "title": self._clip(job["title"], widths["title"]),
            "location": self._clip(job["location"], widths["location"]),
            "mode": job["mode"],
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
            f"[b]{job['company']} — {self._clip(job['title'], 110)}[/b]\n"
            f"{job['url']}\n"
            f"status={job['status']} lane={job['lane']} score={job['score']} scope={job['scope']} "
            f"mode={job['mode']} country={job['country']} sources={','.join(job['sources'])} "
            f"published={job['published'] or 'n/a'}\n"
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

    # ---- columns and sorting (persisted) ----------------------------------

    def _after_layout_change(self, message: str) -> None:
        self._save_layout()
        self.load_rows(requery=False)
        self.set_status(f"{self.counts_line} · {message}")

    def _focus_column(self, index: int) -> None:
        self.column_focus = index % len(self.column_layout)
        self.load_rows(requery=False)
        key, width = self.column_layout[self.column_focus]
        self.set_status(f"{self.counts_line} · column {key} ({width})")

    def action_focus_prev_column(self) -> None:
        self._focus_column(self.column_focus - 1)

    def action_focus_next_column(self) -> None:
        self._focus_column(self.column_focus + 1)

    def _resize_column(self, delta: int) -> None:
        key, width = self.column_layout[self.column_focus]
        new_width = max(MIN_WIDTH, width + delta)
        if new_width == width:
            return
        self.column_layout[self.column_focus] = (key, new_width)
        self._after_layout_change(f"{key} width {new_width}")

    def action_grow_column(self) -> None:
        self._resize_column(WIDTH_STEP)

    def action_shrink_column(self) -> None:
        self._resize_column(-WIDTH_STEP)

    def _move_column(self, delta: int) -> None:
        target = self.column_focus + delta
        if not 0 <= target < len(self.column_layout):
            return
        layout = list(self.column_layout)
        layout[self.column_focus], layout[target] = layout[target], layout[self.column_focus]
        self.column_layout = layout
        self.column_focus = target
        self._after_layout_change("column order saved")

    def action_move_column_left(self) -> None:
        self._move_column(-1)

    def action_move_column_right(self) -> None:
        self._move_column(1)

    def action_cycle_sort(self) -> None:
        self.sort_key = SORT_KEYS[(SORT_KEYS.index(self.sort_key) + 1) % len(SORT_KEYS)]
        self._after_layout_change(f"sort {self.sort_key}")

    def action_reverse_sort(self) -> None:
        self.sort_reverse = not self.sort_reverse
        self._after_layout_change(f"sort {self.sort_key} {'ascending' if self.sort_reverse else 'descending'}")

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
