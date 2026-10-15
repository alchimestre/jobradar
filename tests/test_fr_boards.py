"""French multi-employer boards: LesJeudis, free-work."""

from __future__ import annotations

import json

import pytest

from jobradar.sources import fr_boards as fr


class Response:
    def __init__(self, payload=None, status_code: int = 200, text: str = "") -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        return self.payload


class Boom:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RuntimeError("boom")


class Stub:
    """Route stub: the first fragment found in the URL wins.

    `json`/`text` mirror tests/test_feeds.py's RouteStub; `get` returns a Response so a
    test can carry the status code a board pages on.
    """

    def __init__(self, routes: dict | None = None, text: str = "") -> None:
        self.routes = routes or {}
        self.fallback = text
        self.urls: list[str] = []

    def _lookup(self, url):
        for fragment, value in self.routes.items():
            if fragment in url:
                return value
        return None

    def json(self, url: str, headers: dict | None = None):
        self.urls.append(url)
        value = self._lookup(url)
        if isinstance(value, Boom):
            raise value.error
        return value.payload if isinstance(value, Response) else value

    def text(self, url: str, headers: dict | None = None) -> str:
        self.urls.append(url)
        value = self._lookup(url)
        if isinstance(value, Boom):
            raise value.error
        if isinstance(value, Response):
            return value.text
        return value if isinstance(value, str) else self.fallback

    def get(self, url: str, tries: int = 2, headers: dict | None = None) -> Response:
        self.urls.append(url)
        value = self._lookup(url)
        if isinstance(value, Boom):
            raise value.error
        return value if isinstance(value, Response) else Response(payload=value)


# ------------------------------------------------------------------- LesJeudis

LJ_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://lesjeudis.com/offers/old-1</loc><lastmod>2026-09-01T08:00:00.000Z</lastmod></url>
  <url><loc>https://lesjeudis.com/offers/new-2</loc><lastmod>2026-10-15T09:37:59.626Z</lastmod></url>
  <url><loc>https://lesjeudis.com/offers/mid-3</loc><lastmod>2026-10-14T12:00:00.000Z</lastmod></url>
</urlset>"""


def lj_page(title: str = "Site Reliability Engineer", company: str = "Inetum") -> str:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title,
        "datePosted": "2026-10-15T09:37:00.000Z",
        "validThrough": "2026-11-14T10:23:33.000Z",
        "hiringOrganization": {"@type": "Organization", "name": company},
        "jobLocation": {
            "@type": "Place",
            "address": {"@type": "PostalAddress", "addressLocality": "Saint-Ouen-sur-Seine", "addressCountry": "F"},
        },
        "employmentType": ["FULL_TIME"],
        "description": "<p>Kubernetes et Terraform au quotidien</p>",
    }
    return f'<html><head><script type="application/ld+json">{json.dumps(posting)}</script></head></html>'


def test_lesjeudis_reads_the_newest_offers_from_the_sitemap() -> None:
    stub = Stub(
        {"sitemap/offre.xml": LJ_SITEMAP, "new-2": lj_page("Ingénieur DevOps"), "mid-3": lj_page()}
    )

    jobs = fr.fetch_lesjeudis(stub, {"name": "lj", "max_items": 2})

    assert stub.urls[0] == fr.LJ_SITEMAP_URL
    assert stub.urls[1:] == ["https://lesjeudis.com/offers/new-2", "https://lesjeudis.com/offers/mid-3"]
    assert [job.title for job in jobs] == ["Ingénieur DevOps", "Site Reliability Engineer"]
    assert jobs[0].company == "Inetum"
    assert jobs[0].url == "https://lesjeudis.com/offers/new-2"
    assert jobs[0].location == "Saint-Ouen-sur-Seine, F"
    assert jobs[0].published == "2026-10-15"
    assert jobs[0].description == "Kubernetes et Terraform au quotidien"
    assert jobs[0].extra["valid_through"] == "2026-11-14"
    assert jobs[0].source == "lesjeudis:lj"


def test_lesjeudis_keeps_the_source_alive_when_one_offer_fails() -> None:
    routes = {
        "sitemap/offre.xml": LJ_SITEMAP,
        "new-2": Boom(),
        "mid-3": "<html><body>no structured data</body></html>",
        "old-1": lj_page(),
    }
    stub = Stub(routes)

    jobs = fr.fetch_lesjeudis(stub, {"max_items": 3})

    assert [job.url for job in jobs] == ["https://lesjeudis.com/offers/old-1"]


def test_lesjeudis_returns_nothing_when_the_sitemap_is_unparsable() -> None:
    stub = Stub({"sitemap/offre.xml": "<not xml"})

    assert fr.fetch_lesjeudis(stub, {"max_items": 3}) == []
    assert len(stub.urls) == 1


def test_lesjeudis_honours_max_items() -> None:
    stub = Stub({"sitemap/offre.xml": LJ_SITEMAP, "/offers/": lj_page()})

    assert len(stub.urls) == 0
    fr.fetch_lesjeudis(stub, {"max_items": 0})
    assert len(stub.urls) == 1  # 0 means no offer page at all


# ------------------------------------------------------------------- free-work


def fw_member(
    row_id: int,
    title: str,
    slug: str,
    salary: tuple[int, int] = (400, 550),
    remote: str = "full",
    contracts: list[str] | None = None,
    description: str = "<p>Mission Kubernetes</p>",
) -> dict:
    family = "ingenieur-devops-cloud"
    return {
        "@type": "JobPosting",
        "id": row_id,
        "title": title,
        "slug": slug,
        "publishedAt": "2026-10-15T13:18:17+02:00",
        "remoteMode": remote,
        "contracts": contracts or ["contractor"],
        "description": description,
        "minDailySalary": salary[0],
        "maxDailySalary": salary[1],
        "dailySalary": f"{salary[1]} €",
        "currency": "EUR",
        "company": {"@type": "Company", "id": 1, "name": "Naxotech", "slug": "naxotech"},
        "location": {"locality": "Lille", "label": "Lille, Hauts-de-France", "countryCode": "FR"},
        "job": {"@id": "/jobs/118", "id": 118, "nameForUserSlug": family, "slug": family},
    }


def fw_page(members: list[dict], last: int, page: int = 1) -> dict:
    return {
        "@type": "hydra:Collection",
        "hydra:totalItems": 10923,
        "hydra:view": {
            "hydra:first": "/job_postings?page=1",
            "hydra:last": f"/job_postings?itemsPerPage=100&page={last}",
            "hydra:next": f"/job_postings?page={page + 1}",
        },
        "hydra:member": members,
    }


def test_free_work_parses_the_hydra_collection() -> None:
    rows = [fw_member(682871, "Product Manager RFID", "product-manager-rfid", remote="partial")]
    stub = Stub({"page=1&itemsPerPage=100": fw_page(rows, last=1)})

    jobs = fr.fetch_free_work(stub, {"name": "fw"})

    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Product Manager RFID"
    assert job.company == "Naxotech"
    assert job.url == "https://www.free-work.com/fr/tech-it/job-mission/ingenieur-devops-cloud/product-manager-rfid"
    assert job.location == "Lille, Hauts-de-France"
    assert job.published == "2026-10-15"
    assert job.remote is None  # partial remote is neither full remote nor on-site
    assert job.description == "Mission Kubernetes"
    assert job.extra["minDailySalary"] == 400
    assert job.extra["maxDailySalary"] == 550
    assert job.extra["currency"] == "EUR"
    assert job.extra["contracts"] == ["contractor"]
    assert job.source == "free_work:fw"


def test_free_work_maps_the_remote_modes() -> None:
    rows = [
        fw_member(1, "DevOps full", "devops-full", remote="full"),
        fw_member(2, "DevOps none", "devops-none", remote="none"),
        fw_member(3, "DevOps partial", "devops-partial", remote="partial"),
    ]
    stub = Stub({"page=1&itemsPerPage=100": fw_page(rows, last=1)})

    assert [job.remote for job in fr.fetch_free_work(stub, {})] == [True, False, None]


def test_free_work_filters_client_side_by_keyword_and_folds_accents() -> None:
    rows = [
        fw_member(1, "Développeur Kotlin", "dev-kotlin", description="<p>Android au quotidien</p>"),
        fw_member(2, "Product Owner", "product-owner", description="<p>Backlog et ateliers</p>"),
    ]
    stub = Stub({"page=1&itemsPerPage=100": fw_page(rows, last=1)})

    # The board ignores `q`, so the keyword is applied over the fetched page.
    assert [job.title for job in fr.fetch_free_work(stub, {"keywords": "developpeur"})] == ["Développeur Kotlin"]
    assert fr.fetch_free_work(stub, {"keywords": "kubernetes,rust"}) == []
    assert len(fr.fetch_free_work(stub, {"keywords": "backlog"})) == 1


def test_free_work_filters_client_side_by_rate_and_contract() -> None:
    rows = [
        fw_member(1, "Mission 400", "mission-400", salary=(300, 400)),
        fw_member(2, "Mission 550 CDI", "mission-550", salary=(500, 550), contracts=["fixed-term"]),
    ]
    stub = Stub({"page=1&itemsPerPage=100": fw_page(rows, last=1)})

    assert [job.title for job in fr.fetch_free_work(stub, {"min_daily_rate": 500})] == ["Mission 550 CDI"]
    assert [job.title for job in fr.fetch_free_work(stub, {"contracts": ["contractor"]})] == ["Mission 400"]
    assert [job.title for job in fr.fetch_free_work(stub, {"contracts": "fixed-term"})] == ["Mission 550 CDI"]


def test_free_work_honours_max_pages_and_the_last_page() -> None:
    routes = {
        "page=1&itemsPerPage=2": fw_page([fw_member(1, "A", "a")], last=3, page=1),
        "page=2&itemsPerPage=2": fw_page([fw_member(2, "B", "b")], last=3, page=2),
        "page=3&itemsPerPage=2": fw_page([fw_member(3, "C", "c")], last=3, page=3),
    }
    stub = Stub(routes)
    spec = {"items_per_page": 2, "max_pages": 2}

    assert [job.title for job in fr.fetch_free_work(stub, spec)] == ["A", "B"]
    assert len(stub.urls) == 2

    stub = Stub({"page=1&itemsPerPage=2": fw_page([fw_member(1, "A", "a")], last=1)})
    assert len(fr.fetch_free_work(stub, spec)) == 1
    assert len(stub.urls) == 1


def test_free_work_honours_max_items_and_stops_on_an_empty_page() -> None:
    routes = {
        "page=1&itemsPerPage=2": fw_page([fw_member(1, "A", "a"), fw_member(2, "B", "b")], last=9, page=1),
        "page=2&itemsPerPage=2": fw_page([], last=9, page=2),
    }
    assert len(fr.fetch_free_work(Stub(routes), {"items_per_page": 2, "max_pages": 5, "max_items": 1})) == 1

    stub = Stub({"page=1&itemsPerPage=2": fw_page([], last=9)})
    assert fr.fetch_free_work(stub, {"items_per_page": 2, "max_pages": 5}) == []
    assert len(stub.urls) == 1


def test_free_work_skips_rows_without_a_title_or_slug() -> None:
    rows = [fw_member(1, "", "no-title"), {"@type": "JobPosting", "id": 2, "title": "No slug"}]
    stub = Stub({"page=1&itemsPerPage=100": fw_page(rows, last=1)})

    assert fr.fetch_free_work(stub, {}) == []


# ------------------------------------------------------------------ registry


def test_the_registry_names_its_two_types() -> None:
    assert set(fr.FETCHERS) == {"lesjeudis", "free_work"}
