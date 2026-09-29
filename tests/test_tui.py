"""TUI smoke: the table reflects the store, marks are visible, workers own their connection."""

from __future__ import annotations

import asyncio

from textual.widgets import DataTable

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
