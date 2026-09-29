"""TUI smoke: the table reflects the store, and row actions write status back."""

from __future__ import annotations

import asyncio

from textual.widgets import DataTable

from jobradar import store
from jobradar.config import load_config
from jobradar.models import Match
from jobradar.models import Job
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


def test_table_lists_stored_postings_and_marks_status(tmp_path) -> None:
    db = tmp_path / "state.db"
    seed(db)
    app = RadarApp(db=str(db), cfg=load_config())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#jobs", DataTable)
            assert table.row_count == 2

            # highest score is first, so the cursor starts on Acme
            app.action_applied()
            await pilot.pause()
            assert store.query(app.conn, status="applied")[0]["company"] == "Acme"

            # filter cycle narrows the table to the high-confidence rows
            app.action_cycle_filter()
            await pilot.pause()
            assert app.decision == "high"
            assert table.row_count == 1
            assert "1 shown" in app.status_message

    asyncio.run(scenario())
