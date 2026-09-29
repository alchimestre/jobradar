# jobradar

One SQLite store and one terminal UI over every employer ATS board and job feed worth watching.

Built for a search that was previously fed by LinkedIn notifications and APEC inbound: it turns
~110 employer ATS boards plus seven aggregator feeds into a single ranked, deduplicated table, and
it learns new boards from the job URLs it sees.

```
 uv run jobradar scan
 uv run jobradar tui
```

## Why

Public ATS endpoints (Ashby, Greenhouse, Lever, Workable, SmartRecruiters, Personio, Recruitee) expose a
company's live requisitions as JSON/XML, no auth. They are the *first* place a role appears — aggregators
and LinkedIn lag by days, and LinkedIn never shows the ATS token you need to contact the hiring manager.
Polling them directly is cheaper than scrolling boards, and it produces the outbound list instead of
another application pile.

## Install

```bash
uv sync                 # creates .venv from uv.lock
uv run jobradar --help
```

Python ≥3.11. Runtime dependencies: `httpx`, `PyYAML`, `textual`.

## Quickstart

```bash
# 1. find an employer's board from a name or any careers URL, and keep it
uv run jobradar discover "Mistral AI" --write
uv run jobradar discover "https://job-boards.greenhouse.io/gitlab/jobs/8556658002" --write

# 2. poll everything (~35s for 110 boards + 7 feeds)
uv run jobradar scan

# 3. work the queue
uv run jobradar tui
```

TUI keys: `r` rescan · `o` open posting · `s` shortlist · `a` applied · `x` ignore · `/` filter ·
`f` cycle decision filter (high → review → `out` → all) · `e` export markdown · `q` quit.

## Commands

| Command | Purpose |
|---|---|
| `scan [--only SUBSTR] [--cache-minutes N] [--no-learn]` | fetch every source, score, store; `--only ashby:mistralai` for one source |
| `list [--decision high\|review\|low\|out] [--lane …] [--region eu\|emea\|au\|americas\|apac] [--language en\|fr\|de] [--min-score N] [--since 7d] [--search S] [--reason TAG] [--include-out] [--json]` | query the store; out-of-scope rows are hidden unless asked for |
| `show UID` / `open UID` / `mark UID shortlist\|applied\|rejected\|ignored` | act on one posting |
| `discover NAME\|URL… [--write] [--market de]` | resolve an employer to a verified ATS board |
| `import SEED.yaml [--write]` | re-validate an external `{name, ats, token}` list and merge the live boards |
| `export [--since 14d] [--decision high] [--out FILE]` | markdown for the pipeline tracker |
| `sources` | per-source last fetch, job count, last error |
| `rescore [--prune]` | re-score stored postings against the current rules (no network); `--prune` deletes below-floor rows never acted on |
| `tui` | the terminal UI |

`UID` is the stable row id (12 hex chars) — put it in the opportunity doc so the tracker and the
store stay reconcilable.

## Sources

**ATS adapters** (`src/jobradar/sources/ats.py`), one function each:

| ATS | Endpoint |
|---|---|
| Ashby | `api.ashbyhq.com/posting-api/job-board/<token>?includeCompensation=true` |
| Greenhouse | `boards-api.greenhouse.io/v1/boards/<token>/jobs` |
| Lever | `api.lever.co/v0/postings/<token>?mode=json` |
| Workable | `apply.workable.com/api/v1/widget/accounts/<token>?details=true` |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/<token>/postings` |
| Personio | `<token>.jobs.personio.de/xml?language=en` |
| Recruitee | `<token>.recruitee.com/api/offers/` |

**Feeds** (`src/jobradar/sources/feeds.py`): any RSS/Atom URL, plus native adapters for RemoteOK,
Arbeitnow, and the monthly Hacker News *Ask HN: Who is hiring?* thread (via the HN Algolia API,
one row per top-level comment).

Add feeds in `config.yaml`. Cloudflare-protected boards (jobs.mlops.community, Welcome to the Jungle,
Wellfound, startup.jobs, SEEK) block scripted access — keep those as browser bookmarks.

## The watchlist

`companies.yaml`, one row per board:

```yaml
companies:
  - name: vCluster Labs
    ats: ashby
    token: vClusterLabs
    market: eu-remote      # informational; the scorer computes the lane from the posting
    tier: b
    auto: false            # true = learned automatically from a job URL
    enabled: true
```

Two ways entries appear:

1. `jobradar discover "<name or careers URL>" --write`.
2. **Automatically.** During `scan`, every ingested job URL is matched against the known ATS URL
   patterns; a new `(ats, token)` pair is appended to the watchlist with `auto: true` and polled from
   the next scan on. Aggregators therefore feed the watchlist: one HN comment linking
   `jobs.ashbyhq.com/newco/…` permanently adds NewCo's board.

## Ranking model

`filters.py` (pure functions, no I/O):

- **Title rules** — weighted regexes for the target titles (`+ml-platform`, `+k8s`, `+sre`, …).
- **Hard negatives** always apply: internships, leadership/manager titles, go-to-market, off-target
  disciplines, and `staff`/`principal` seniority stretch.
- **Soft negatives** only apply when no strong target title is present — a `data engineer` posting is
  demoted, but `ML Platform Engineer, Data` is not.
- **Lanes** are derived from location: `eu-remote`, `de`, `ch`, `fr`, `au`, else `other`.
- **Scope** demotes remote roles that explicitly exclude Europe (`scope=restricted`).
- **Off-lane cap** — a strong title outside every target lane, or a remote posting that excludes Europe,
  is capped at `review`, never `high`.
- **Web3 infrastructure** is an admitted lane (`+web3`, `+blockchain`, `+staking`, `+web3-tech`, …),
  weighted **below** ML/MLOps on purpose: ML stays the preference, Web3 is not gated out.
- **Scope gate (region + JD language)** — everything outside it is stored with decision `out`, never
  shown by default, and inspectable with `list --decision out`:
  - `region` = `eu` | `emea` | `au` | `americas` | `apac` | `unknown`, from the location string. A posting
    offering both (e.g. `Remote - USA / Remote - EMEA`) stays in scope — the European option is real.
  - `language` = `en` | `fr` | `de` | `unknown`, detected from the JD text (German umlauts + stopwords
    vs French/English markers). **Only the language is judged, never the country**: an English-language JD
    at a Berlin employer stays in scope, which is what keeps the Berlin lane open.
  - Configured by `allowed_languages` / `allowed_regions` in `config.yaml`.
- `decision`: `high` ≥ 6, `review` ≥ 3, `out` for the scope gate, below the `min_score` floor nothing is
  stored.

Upsert only ever keeps a *better* score, so after editing rules run `jobradar rescore [--prune]` — a
tightened rule will otherwise leave inflated rows behind, and `rescore` is also what reclassifies
`region`/`language` for rows stored before the rule existed.

Tune weights and rules in `config.yaml`; the defaults mirror the search's role-admission criteria.

## Hooks

Marking a posting (`a` applied, `s` shortlist, `x` ignore) writes the status **and** runs the hook
configured for that status:

```yaml
hooks:
  applied: python3 /Users/vincent/projects/jobsearch/scripts/log_applied.py
```

The command receives the posting as JSON on stdin and as `JOBRADAR_*` env vars (`UID`, `COMPANY`,
`TITLE`, `URL`, `LANE`, `STATUS`, `HOOK`); relative paths in it resolve against this repo, cwd is the
repo root, exit 0 is success and the last stdout line is shown in the TUI status bar. `JOBRADAR_HOOK_<STATUS>`
(env) is the fallback when no config entry exists; `jobradar mark <uid> <status> --no-hook` skips it.

Hooks run in a worker thread, so a slow `gh` call never freezes the UI, and a failing hook never loses
the status write. `jobradar mark` runs the same hook — a status change is a status change wherever it
happens.

## Storage

SQLite at `.jobradar/state.db` (gitignored). One row per posting; identity is **(company, title)** so the
same requisition appearing on the employer's ATS and on an aggregator — with different location
formatting — collapses into one row whose `sources`, `locations`, best score and status accumulate.
Statuses: `new`, `shortlist`, `applied`, `rejected`, `ignored`.

## Known limits

- **Workable rate-limits** (HTTP 429) after a burst of probing; `scan` records it as a per-source error
  and continues. Add `--cache-minutes 60` to avoid re-hitting everything.
- Some boards are large (200+ reqs) and mostly irrelevant; the scorer, not the board, is the filter.
- RSS entries frequently omit the employer; `guess_company()` recovers it from `Company: Role` and
  `Role at Company` patterns, otherwise the feed title is used. Set `company:` on an RSS entry to
  override.
- No LinkedIn/Indeed scraping: both forbid it, and neither exposes the ATS token that makes a posting
  actionable.

## Tests

```bash
uv run pytest -q          # 57 tests: adapters vs captured payloads, scoring, store identity, scan isolation, TUI
```

Adapter tests run against captured real payload shapes, so a provider changing its JSON schema fails the
suite rather than silently emptying the store. No test performs network I/O.

## Layout

```
src/jobradar/
  cli.py        argparse entry point
  config.py     defaults, config.yaml/companies.yaml loading, title+lane rules
  filters.py    scoring, lane detection, remote-scope triage (pure)
  models.py     Job / Match, identity rules
  net.py        httpx wrapper with bounded retries
  resolve.py    URL→ATS extraction, token guessing and probing
  scan.py       source fan-out, auto-learning, error isolation
  store.py      SQLite schema, upsert/merge, queries
  tui.py        Textual app
  sources/      ats.py + feeds.py
```
