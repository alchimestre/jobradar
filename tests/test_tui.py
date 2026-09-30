"""TUI smoke: the table reflects the store, marks are visible, workers own their connection."""

from __future__ import annotations

import asyncio

from textual.widgets import DataTable, Input

from jobradar import store, tui
from jobradar.config import load_config
from jobradar.models import Job, Match
from jobradar.scan import ScanReport
from jobradar.tui import RadarApp


def seed(db_path) -> None:
    conn = store.connect(db_path)
    store.upsert(
        conn,
        [
            (
                Job(company="Acme", title="ML Platform Engineer", url="https://a/1", source="ashby:acme", location="Berlin"),
                Match(score=12, decision="high", lane="de", reasons=["+ml-platform"]),
            ),
            (
                Job(company="Beta", title="DevOps Engineer", url="https://a/2", source="rss:wwr", location="Remote"),
                Match(score=2, decision="low", lane="eu-remote", reasons=["+devops"]),
            ),
        ],
    )
    conn.close()


def app_for(db) -> RadarApp:
    # never load the real hook config in a test: `a` would create real issues
    return RadarApp(db=str(db), cfg={**load_config(), "hooks": {}})


def test_status_column_is_visible_early() -> None:
    """Regression: `status` used to sit at char ~118 of a 141-char table, off-screen."""
    labels = [label for _, label, _ in tui.COLUMNS]
    assert labels[1] == "status"
    offset = sum(width for _, _, width in tui.COLUMNS[:1]) + 2
    assert offset <= 10


def test_table_lists_stored_postings_and_marks_status(tmp_path) -> None:
    db = tmp_path / "state.db"
    seed(db)
    app = app_for(db)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#jobs", DataTable)
            assert table.row_count == 2
            assert str(table.get_row_at(0)[1]) == "new"

            first_uid = app.current()["uid"]
            app.action_applied()
            await pilot.pause()

            # the change is visible in the table itself, not only in a toast
            assert str(table.get_row_at(0)[1]) == "applied"
            assert app.current()["uid"] == first_uid  # cursor did not jump
            assert "applied" in app.status_message
            assert store.query(app.conn, status="applied")[0]["company"] == "Acme"

            # filter cycle narrows the table to the high-confidence rows
            app.action_cycle_filter()
            await pilot.pause()
            assert app.decision == "high"
            assert table.row_count == 1
            assert "1 shown" in app.status_message
            assert "applied 1" in app.status_message  # the counter survives hook reporting

    asyncio.run(scenario())


def test_ignored_rows_are_styled_and_not_dropped(tmp_path) -> None:
    db = tmp_path / "state.db"
    seed(db)
    app = app_for(db)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_ignore()
            await pilot.pause()
            cell = app.query_one("#jobs", DataTable).get_row_at(0)[1]
            assert str(cell) == "ignored"
            assert cell.style == "dim strike"
            assert app.query_one("#jobs", DataTable).row_count == 2

    asyncio.run(scenario())


def test_out_of_scope_postings_are_hidden_until_asked_for(tmp_path) -> None:
    db = tmp_path / "state.db"
    conn = store.connect(db)
    store.upsert(
        conn,
        [
            (
                Job(company="Acme", title="ML Platform Engineer", url="https://a/1", source="x", location="Berlin"),
                Match(score=12, decision="high", lane="de", region="eu", language="en"),
            ),
            (
                Job(company="US Corp", title="Platform Engineer", url="https://a/2", source="x", location="Remote - USA"),
                Match(score=6, decision="out", lane="other", region="americas", language="en", reasons=["-region-americas"]),
            ),
        ],
    )
    conn.close()
    app = app_for(db)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#jobs", DataTable)
            assert table.row_count == 1
            assert "out-of-scope hidden" in app.status_message

            # the filter cycle reaches the out-of-scope bucket
            app.action_cycle_filter()  # high
            app.action_cycle_filter()  # review
            app.action_cycle_filter()  # out
            await pilot.pause()
            assert app.decision == "out"
            assert table.row_count == 1
            assert app.jobs[0]["company"] == "US Corp"

    asyncio.run(scenario())


def test_scan_worker_uses_a_connection_usable_from_its_thread(tmp_path, monkeypatch) -> None:
    """Regression: the scan worker used the main thread's sqlite connection."""
    db = tmp_path / "state.db"
    seed(db)
    app = app_for(db)
    observed: dict[str, object] = {}

    def fake_scan(cfg, conn, **kwargs):
        # raises ProgrammingError if `conn` was created on another thread
        observed["rows"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        observed["same_as_main"] = conn is app.conn
        return ScanReport(sources=[{"source": "fake", "jobs": 0, "error": "", "cached": False}])

    monkeypatch.setattr(tui, "scan", fake_scan)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_rescan()
            for _ in range(150):
                await asyncio.sleep(0.02)
                if observed:
                    break

    asyncio.run(scenario())

    assert observed["rows"] == 2
    assert observed["same_as_main"] is False
    assert "scan failed" not in app.status_message


def test_hook_failure_is_surfaced_without_losing_the_status(tmp_path) -> None:
    db = tmp_path / "state.db"
    seed(db)
    app = RadarApp(db=str(db), cfg={**load_config(), "hooks": {"applied": "/nonexistent/hook.sh"}})

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_applied()
            for _ in range(150):
                await asyncio.sleep(0.02)
                if "failed to start" in app.status_message:
                    break
            assert "failed to start" in app.status_message
            # the status write still landed even though the hook failed
            assert store.query(app.conn, status="applied")[0]["company"] == "Acme"

    asyncio.run(scenario())


def test_long_title_is_clipped_to_its_column_width(tmp_path) -> None:
    """Regression: a 1,191-character HN comment sized the title column to the comment
    and pushed every other column off the terminal."""
    db = tmp_path / "state.db"
    conn = store.connect(db)
    long_title = "Location: London, UK Remote: Yes — UK remote or London hybrid/onsite " + "x" * 1200
    store.upsert(
        conn,
        [(
            Job(company="HN", title=long_title, url="https://a/1", source="hn:hn"),
            Match(score=9, decision="review", lane="eu-remote", mode="remote", country="other"),
        )],
    )
    conn.close()
    app = app_for(db)

    async def scenario() -> None:
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            table = app.query_one("#jobs", DataTable)
            widths = dict(app.column_layout)
            for column in table.ordered_columns:
                assert column.width == widths[column.key.value]
            title_index = [key for key, _ in app.column_layout].index("title")
            cell = str(table.get_row_at(0)[title_index])
            assert len(cell) <= widths["title"] and cell.endswith("…")

    asyncio.run(scenario())


def test_column_widths_are_resizable_and_persisted(tmp_path) -> None:
    db = tmp_path / "state.db"
    seed(db)
    app = app_for(db)
    before = dict(app.column_layout)["title"]

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#jobs", DataTable).focus()
            app.column_focus = 5  # title
            app.action_grow_column()
            await pilot.pause()
            assert dict(app.column_layout)["title"] == before + tui.WIDTH_STEP
            assert app.query_one("#jobs", DataTable).ordered_columns[5].width == before + tui.WIDTH_STEP
            assert "title width" in app.status_message

    asyncio.run(scenario())

    # a fresh app on the same store starts with the saved layout
    reopened = app_for(db)
    assert dict(reopened.column_layout)["title"] == before + tui.WIDTH_STEP


def test_columns_can_be_reordered_and_persisted(tmp_path) -> None:
    db = tmp_path / "state.db"
    seed(db)
    app = app_for(db)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.column_focus = 5  # title
            app.action_move_column_left()
            await pilot.pause()
            order = [key for key, _ in app.column_layout]
            assert order[4] == "title" and order[5] == "company"
            assert [c.key.value for c in app.query_one("#jobs", DataTable).ordered_columns] == order

    asyncio.run(scenario())

    reopened = app_for(db)
    assert [key for key, _ in reopened.column_layout][4] == "title"


def test_rows_can_be_sorted(tmp_path) -> None:
    db = tmp_path / "state.db"
    seed(db)
    app = app_for(db)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert [job["company"] for job in app.jobs] == ["Acme", "Beta"]
            app.action_cycle_sort()  # smart -> score
            await pilot.pause()
            assert app.sort_key == "score"
            assert [job["score"] for job in app.jobs] == [12, 2]
            keep = app.current()["uid"]
            app.action_reverse_sort()
            await pilot.pause()
            assert [job["score"] for job in app.jobs] == [2, 12]
            # the re-render keeps the cursor on the same posting
            assert app.current()["uid"] == keep
            assert "sort score ascending" in app.status_message

    asyncio.run(scenario())


def test_documented_keys_reach_their_actions(tmp_path) -> None:
    """Regression: the search box holds focus at mount, so every documented key was
    typed into it instead of reaching the app."""
    db = tmp_path / "state.db"
    seed(db)
    app = app_for(db)

    async def scenario() -> None:
        async with app.run_test(size=(150, 34)) as pilot:
            await pilot.pause()
            assert app.focused is not None and app.focused.id == "jobs"
            title = [key for key, _ in app.column_layout].index("title")
            app.column_focus = title

            await pilot.press("]")
            await pilot.pause()
            assert dict(app.column_layout)["title"] == tui.COLUMN_WIDTHS["title"] + tui.WIDTH_STEP
            await pilot.press("[")
            await pilot.pause()
            assert dict(app.column_layout)["title"] == tui.COLUMN_WIDTHS["title"]

            await pilot.press("t")
            await pilot.pause()
            assert app.sort_key == "score"
            await pilot.press("comma")
            await pilot.pause()
            assert app.column_focus == title - 1

            await pilot.press("slash")
            await pilot.pause()
            assert app.query_one("#search", Input).has_focus

    asyncio.run(scenario())
