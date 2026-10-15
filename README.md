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

# 2. poll everything (166 boards + 24 feeds; a few minutes)
uv run jobradar scan

# 3. work the queue
uv run jobradar tui
```

TUI keys: `r` rescan · `o` open posting · `s` shortlist · `a` applied · `x` ignore · `/` filter ·
`f` cycle decision filter (high → review → `out` → all) · `e` export markdown · `q` quit.
Column layout: `,`/`.` move the column cursor · `[`/`]` narrow/widen that column ·
`{`/`}` reorder it · `t` cycle the row sort (`smart`/score/company/title/seen/lane) · `T` reverse it.
Widths, order and sort are saved to `.jobradar/tui.json` and restored on the next start.

## Commands

| Command | Purpose |
|---|---|
| `scan [--only SUBSTR] [--cache-minutes N] [--no-learn]` | fetch every source, score, store; `--only ashby:mistralai` for one source |
| `list [--decision high\|review\|low\|out] [--lane …] [--region eu\|emea\|au\|americas\|apac\|unknown] [--mode remote\|hybrid\|onsite\|unknown] [--country fr\|de\|ch\|nl\|other\|unknown] [--language en\|fr\|de] [--min-score N] [--since 7d] [--search S] [--reason TAG] [--include-out] [--json]` | query the store; out-of-scope rows are hidden unless asked for |
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

**ATS adapters** — one function per platform, in `src/jobradar/sources/ats.py` and `ats_extra.py`. The
`ats` value is what a watchlist row names:

| `ats` | Endpoint | Token |
|---|---|---|
| `ashby` | `api.ashbyhq.com/posting-api/job-board/<token>?includeCompensation=true` | board slug |
| `greenhouse` | `boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true` | board slug |
| `lever` | `api.lever.co/v0/postings/<token>?mode=json` | board slug |
| `workable` | `apply.workable.com/api/v1/widget/accounts/<token>?details=true` | account slug |
| `smartrecruiters` | `api.smartrecruiters.com/v1/companies/<token>/postings` | company id |
| `personio` | `<token>.jobs.personio.de/xml?language=en` | subdomain |
| `recruitee` | `<token>.recruitee.com/api/offers/` | subdomain |
| `workday` | `POST https://<host>/wday/cxs/<tenant>/<site>/jobs` | `<host>/<site>`, e.g. `ag.wd3/Airbus` |
| `icims_jibe` | `https://<host>/api/jobs?limit=100&page=N` | career-site host |
| `successfactors` | `https://<host>/services/rss/job` | career-site host |
| `softgarden` | `https://<tenant>.career.softgarden.de/jobs.feed.json` | tenant subdomain |
| `join_com` | `https://join.com/candidate-api/graphql` | company slug |

**Feed adapters** — the multi-employer boards. The `type` in `config.yaml` selects one:

| `type` | Source | Notes |
|---|---|---|
| `rss` | any RSS/Atom URL | set `company:` when the feed title is not the employer |
| `remoteok` | remoteok.com | |
| `arbeitnow` | arbeitnow.com | mostly German JDs, so most rows land out of scope |
| `hn` | Hacker News *Who is hiring?* | HN Algolia, one row per top-level comment |
| `lesjeudis` | lesjeudis.com | sitemap discovery, then one page fetch per posting |
| `free_work` | free-work.com | French tech missions, day rate in `extra` |
| `jobicy`, `himalayas`, `workingnomads`, `landingjobs`, `jobdataapi` | global remote boards | full JD in the payload |
| `devitjobs` | devitjobs.uk | RSS; needs `user_agent: browser` |
| `themuse` | themuse.com | needs `user_agent: browser` |
| `web3career` | web3.career | token required |
| `lobsters` | lobste.rs *Who's hiring?* | quarterly thread |

Every feed honours `max_pages` and `max_items`; both exist so that one broad board cannot turn a scan into
thousands of requests. `enabled: false` on a feed or a watchlist row mutes it without forgetting it.

Sources fetch in parallel (`scan_workers`, default 8; `jobradar scan --jobs N` for one run). Scoring,
auto-learning and every database write stay on one thread in source order, so the result does not depend
on which fetch finished first. Within a source, detail calls (a Workday JD, a sitemap page, an enriched
teaser) run six at a time, so total concurrency is at most `scan_workers × 6`.

### Credentials

Keys live in `.env` beside `config.yaml` (copy `.env.example`), are referenced as `${VARIABLE}` and are
read from `.env` or from the environment, so none is ever committed. The real environment wins, so
`WEB3CAREER_KEY=... jobradar scan` overrides the file. Point `env_file` at another path if the file lives
elsewhere.

A source whose variable is unset is skipped and named in the scan summary, never called with a blank key.

| Source | Variables | Where |
|---|---|---|
| web3.career | `WEB3CAREER_KEY` | `web3.career/web3-jobs-api` |

```bash
cp .env.example .env      # then fill in the values
```

**web3.career.** Create a profile at `web3.career/web3-jobs-api` and generate a token. Any URL given at
signup is only a registration detail — the API accepts the token from anywhere, with no `Origin` or
`Referer` check. The licence requires attribution: link out with `apply_url` rather than `url`, keep the
token private, and add no tracking parameters.

**Keyed sources that are not wired:**

| Source | Gate |
|---|---|
| France Travail | **dead end as of 2026-10-15.** A registered application is not enough: the account's "Ajouter une API" list did not offer «Offres d'emploi v2», and without that subscription the token endpoint answers `invalid_client` — FT's own wording for an unknown client, not for a missing scope. The adapter was removed rather than kept as a source that cannot run |
| Adzuna | free tier is not enough for the volume; the paid plan was declined |
| Jooble | free key, but Cloudflare answers 403 to scripted requests |
| Careerjet | needs an approved publisher `affid` (`careerjet.com/partners/api/`) |
| findwork.dev | free token; not evaluated |
| Reddit (`r/sre`, `r/devops`) | OAuth app required; monthly threads, thin signal |
| Welcome to the Jungle / WelcomeKit | partner access only. `api.welcomekit.co` is introspectable but every data field answers `unauthorized` |
| NoFluffJobs (PL) | undocumented partner API; rejects guessed parameters |

### JD text decides everything

The scorer reads a posting's description to establish its language, and only English and French are in
scope. A row that arrives with no description cannot be admitted, however good its title. Three things
keep that from silently emptying a source:

- `greenhouse` requests `content=true`, so the JD arrives with the board listing.
- `enrich_limit: N` on a feed entry fetches the posting page for up to N short descriptions per scan and
  replaces the teaser with the page's own text. Use it on teaser-only feeds, such as
  `cryptocurrencyjobs.co`.
- `list --decision out` shows what was dropped and why; `-lang-unknown` means no description ever arrived.

Cloudflare- and WAF-protected boards (jobs.mlops.community, Welcome to the Jungle, Wellfound, startup.jobs,
SEEK, APEC, Malt, Jooble) block scripted access — keep those as browser bookmarks.

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
- **Lanes** are derived from location (falling back to the country): `eu-remote`, `de`, `ch`, `fr`, `nl`,
  `au`, else `other`.
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
- **Geography gate (work mode + country)** — a second, stricter pass over *where the job is done*, stored
  as `mode` (`remote` | `hybrid` | `onsite` | `unknown`) and `country` (`fr` | `de` | `ch` | `nl` | `other` |
  `unknown`):
  - A **physical** role (hybrid or on-site) is admitted only in a target country
    (`target_countries`, default FR/DE/CH/NL). London, Tel Aviv, Warsaw or "Headquarters" go to `out`
    with `-onsite-not-target`.
  - A **remote** role is admitted when it names a target country or a broad region that contains one
    (`EMEA`, `EU`, `Europe`, `worldwide`, `anywhere`), or when the posting says nothing at all about
    geography. One that names only somewhere else (`Remote - US`, `Remote, United Kingdom`, `NYC or
    Remote`) goes to `out` with `-remote-not-target`.
  - A remote posting with no geography at all is kept — a bare `Remote` is often a real EU-remote job —
    but tagged `-geo-unverified`, so `list --reason geo-unverified` audits exactly those rows.
  - Country is read from the location and title; when both are silent the JD body is consulted, but only
    for a *target* country. A JD that merely mentions another country is too noisy to place a posting by.
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

`.jobradar/tui.json` (gitignored, written by the TUI) holds the column order, column widths and row sort.
Delete it to go back to the defaults.

## Known limits

- **Workable rate-limits** (HTTP 429) after a burst of probing; `scan` records it as a per-source error
  and continues. Add `--cache-minutes 60` to avoid re-hitting everything.
- **A source never runs without its key.** A `${VARIABLE}` that the environment does not define leaves the
  feed out of the scan and the variable is named in the summary, so a half-configured source reads as
  configured-but-off rather than as a failure.
- **`enrich_limit` is a request budget, not a row count.** It is spent on the first short descriptions in
  feed order, so a large off-target feed spends its budget before reaching the roles that matter.
- **Workday stops at offset 2000** and returns 20 postings per page; its JD text costs one extra request
  per posting, capped at 200 per board per scan.
- `devitjobs` and `themuse` answer 499 and 403 to an unknown client. They need `user_agent: browser`.
- **`icims_jibe` and `successfactors` tokens cannot be guessed or learned.** Their token is the employer's
  own career-site host, which no URL pattern identifies, so those two rows are added to `companies.yaml` by
  hand. Workday and softgarden URLs *are* recognised, so a feed link adopts those boards automatically.
- Some boards are large (200+ reqs) and mostly irrelevant; the scorer, not the board, is the filter.
- RSS entries frequently omit the employer; `guess_company()` recovers it from `Company: Role` and
  `Role at Company` patterns, otherwise the feed title is used. Set `company:` on an RSS entry to
  override.
- No LinkedIn/Indeed scraping: both forbid it, and neither exposes the ATS token that makes a posting
  actionable.

## Tests

```bash
uv run pytest -q          # 269 tests: adapters vs captured payloads, scoring, store identity, scan isolation, TUI
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
  sources/      ats.py + ats_extra.py (per-employer), feeds.py + fr_boards.py +
                remote_boards.py + keyed_boards.py (multi-employer), helpers.py (shared)
```
