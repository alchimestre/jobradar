"""Config + company-watchlist loading.

Everything is optional: a missing config.yaml falls back to DEFAULTS, a missing
companies.yaml falls back to an empty watchlist.
"""

from __future__ import annotations

import copy
import os
import pathlib
import re

import yaml

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def load_env_file(path: pathlib.Path) -> list[str]:
    """Read `KEY=VALUE` lines from a `.env` into the environment. Returns the names loaded.

    The real environment always wins, so `FOO=1 jobradar scan` still overrides the file.
    A missing file is not an error: the keys may simply be exported instead.
    """
    if not path.is_file():
        return []
    loaded: list[str] = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        found = _ENV_LINE.match(stripped)
        if not found:
            continue
        name, value = found.group(1), found.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded


def _expand(value):
    """`${VAR}` in config.yaml is read from the environment: keys stay out of the repo.

    An unset variable keeps its placeholder rather than becoming empty. The source is then
    skipped with the variable named, instead of being called with a blank key and 401ing.
    """
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    return value


def unset_placeholders(spec: dict) -> list[str]:
    """Environment variable names a config entry still needs before it can run."""
    names: list[str] = []
    for item in spec.values():
        for value in item if isinstance(item, list) else [item]:
            if isinstance(value, str):
                names.extend(_ENV.findall(value))
    return sorted(set(names))


def root() -> pathlib.Path:
    """Repo root, overridable per-run with JOBRADAR_HOME (read at call time)."""
    return pathlib.Path(
        os.environ.get("JOBRADAR_HOME") or pathlib.Path(__file__).resolve().parents[2]
    )

# Title rules: (regex, weight, tag). Weights are relative, not calibrated.
TITLE_RULES: list[tuple[str, int, str]] = [
    (r"\bml ?ops\b", 6, "mlops"),
    (r"\bml ?platform\b", 6, "ml-platform"),
    (r"\bmachine learning platform\b", 6, "ml-platform"),
    (r"\bml ?infrastructure\b", 6, "ml-infra"),
    (r"\bai infrastructure\b", 5, "ai-infra"),
    (r"\bai platform\b", 5, "ai-platform"),
    (r"\b(ml|ai) (engineer|engineering)\b", 3, "ai-ml"),
    (r"\bplatform engineer", 4, "platform"),
    (r"\bsite reliability engineer\b|\bsre\b", 3, "sre"),
    (r"\binfrastructure engineer", 3, "infra"),
    (r"\btraining platform\b|\binference platform\b|\bserving platform\b", 5, "serving"),
    (r"\bkubernetes\b|\bk8s\b", 2, "k8s"),
    (r"\bdevops\b", 2, "devops"),
    (r"\bcloud engineer", 2, "cloud"),
    (r"\bforward deployed\b|\bdeployment engineer\b|\bsolutions engineer\b", 2, "fwd-deployed"),
    (r"\breliability\b", 1, "reliability"),
    (r"\bobservability\b", 1, "observability"),
    # Web3 infrastructure: admitted lane, weighted below ML/MLOps so the primary
    # preference still wins on equal titles.
    (r"\bweb3\b|\bweb ?3\b", 3, "web3"),
    (r"\bblockchain\b", 3, "blockchain"),
    (r"\bcrypto\b|\bcryptocurrency\b|\bdigital assets?\b", 2, "crypto"),
    (r"\bprotocol engineer\b|\bprotocols? (infra|infrastructure|engineering)\b", 3, "protocol"),
    (r"\bvalidator\b|\bstaking\b|\bnode operator\b", 3, "staking"),
    (r"\bdefi\b|\bdex\b|\brollup\b|\blayer ?2\b|\bsmart contracts?\b|\bsolidity\b|\bevm\b", 3, "web3-tech"),
    (r"\bindexer\b|\brpc\b|\bcustody\b|\bwallet infrastructure\b", 2, "web3-infra"),
]

NEGATIVE_RULES: list[tuple[str, int, str]] = [
    (r"\bintern\b|\bworking student\b|\bwerkstudent\b|\bapprentice", -8, "intern"),
    (r"\b(head|director|vp|chief|president)\b", -5, "leadership"),
    (r"\bengineering manager\b|\bproduct manager\b|\bprogram manager\b", -5, "non-ic"),
    (r"\bsales\b|\baccount executive\b|\bcustomer success\b|\brecruiter\b|\bmarketing\b", -6, "go-to-market"),
    (r"\bgrowth\b|\bpartnerships?\b|\bcommunity\b|\bbusiness development\b|\bevangelist\b", -6, "go-to-market"),
    (r"\bcompliance\b|\blegal\b|\bfinance\b|\baccounting\b|\baudit\b|\btalent\b|\bpeople ops\b|\bcounsel\b", -6, "non-engineering"),
    (r"\bfrontend\b|\bmobile\b|\bios\b|\bandroid\b|\bqa\b|\btest engineer\b", -5, "off-target"),
    (r"\bdata scientist\b|\bdata analyst\b|\bbusiness analyst\b", -4, "off-target"),
    (r"\bdata engineer\b|\bdataops\b|\banalytics engineer\b", -2, "data-pipeline"),
    (r"\bstaff\b|\bprincipal\b", -6, "seniority-stretch"),
]

LANE_RULES: list[tuple[str, str]] = [
    ("ch", r"\b(?:switzerland|swiss|z[uü]rich|zurich|lausanne|geneva|gen[eè]ve|basel|bern|zug|winterthur|fribourg|epfl|eth ?z[uü]rich|ethz)\b"),
    ("de", r"\b(?:germany|deutschland|berlin|munich|m[uü]nchen|hamburg|frankfurt|cologne|k[oö]ln|stuttgart|leipzig|dresden|d[uü]sseldorf|darmstadt|karlsruhe|potsdam|walldorf|jena|bielefeld)\b"),
    ("fr", r"\b(?:france|paris|lyon|lille|nantes|bordeaux|toulouse|marseille|nice|sophia|rennes|grenoble|montpellier)\b"),
    ("nl", r"\b(?:netherlands|holland|amsterdam|rotterdam|utrecht|eindhoven|the hague|den haag|delft|groningen|haarlem|nijmegen|tilburg|maastricht|enschede)\b"),
    ("au", r"\b(?:australia|sydney|melbourne|brisbane|perth|adelaide|canberra|auckland|new zealand|wellington)\b"),
]

# Region classification. In-scope patterns are tested first so a posting that offers
# both (e.g. "Remote - USA / Remote - EMEA") stays admissible: the candidate can take
# the European option.
REGION_IN_SCOPE: list[tuple[str, str]] = [
    (
        "au",
        r"\baustralia\b|\bsydney\b|\bmelbourne\b|\bbrisbane\b|\bperth\b|\badelaide\b|\bcanberra\b|"
        r"\bnew zealand\b|\bauckland\b|\bwellington\b",
    ),
    (
        "emea",
        r"\bisrael\b|\btel aviv\b|\bturkey\b|\bistanbul\b|\buae\b|\bdubai\b|\babu dhabi\b|\bsaudi\b|"
        r"\briyadh\b|\bqatar\b|\bdoha\b|\begypt\b|\bcairo\b|\bsouth africa\b|\bcape town\b|"
        r"\bjohannesburg\b|\bkenya\b|\bnairobi\b|\bnigeria\b|\blagos\b|\bmorocco\b|\bcasablanca\b|"
        r"\bjordan\b|\bamman\b|\barmenia\b|\bgeorgia\b|\bazerbaijan\b|\bkazakhstan\b",
    ),
    (
        "eu",
        r"\bemea\b|\beurope\b|\beuropean union\b|\beu\b|\beea\b|\bschengen\b|"
        r"\baustria\b|\bbelgium\b|\bbulgaria\b|\bcroatia\b|\bcyprus\b|\bczech\b|\bczechia\b|\bdenmark\b|"
        r"\bestonia\b|\bfinland\b|\bfrance\b|\bgermany\b|\bgreece\b|\bhungary\b|\biceland\b|\bireland\b|"
        r"\bitaly\b|\blatvia\b|\blithuania\b|\bluxembourg\b|\bmalta\b|\bnetherlands\b|\bnorway\b|"
        r"\bpoland\b|\bportugal\b|\bromania\b|\bslovakia\b|\bslovenia\b|\bspain\b|\bsweden\b|"
        r"\bswitzerland\b|\bswiss\b|\buk\b|\bunited kingdom\b|\bengland\b|\bscotland\b|\bwales\b|"
        r"\bamsterdam\b|\brotterdam\b|\butrecht\b|\beindhoven\b|\bberlin\b|\bmunich\b|\bm[uü]nchen\b|"
        r"\bhamburg\b|\bfrankfurt\b|\bstuttgart\b|\bcologne\b|\bk[oö]ln\b|\bd[uü]sseldorf\b|"
        r"\bleipzig\b|\bdresden\b|\bdarmstadt\b|\bkarlsruhe\b|\bpotsdam\b|\bwalldorf\b|\bparis\b|"
        r"\blyon\b|\blille\b|\bnantes\b|\bbordeaux\b|\btoulouse\b|\bmarseille\b|\brennes\b|"
        r"\bgrenoble\b|\bmadrid\b|\bbarcelona\b|\blisbon\b|\bporto\b|\bdublin\b|\bwarsaw\b|\bkrakow\b|"
        r"\bprague\b|\bbrno\b|\bstockholm\b|\bcopenhagen\b|\bhelsinki\b|\boslo\b|\bvienna\b|\bwien\b|"
        r"\bz[uü]rich\b|\bzurich\b|\bgeneva\b|\bgeneve\b|\bbasel\b|\bbern\b|\blausanne\b|\bmilan\b|"
        r"\brome\b|\bbrussels\b|\btallinn\b|\bvilnius\b|\briga\b|\bbudapest\b|\bbucharest\b|\bsofia\b|"
        r"\bathens\b|\bvalencia\b|\bseville\b|\bbilbao\b|\bmalaga\b|\bcork\b|\bgalway\b|\bedinburgh\b|"
        r"\bmanchester\b|\bbristol\b|\bcambridge\b|\boxford\b|\blondon\b",
    ),
]

REGION_OUT_OF_SCOPE: list[tuple[str, str]] = [
    (
        "americas",
        r"\bunited states\b|\busa\b|\bu\.s\.\b|\bus only\b|\bus-remote\b|\bus based\b|\bamericas\b|"
        r"\bcanada\b|\btoronto\b|\bvancouver\b|\bmontreal\b|\bottawa\b|\bbrazil\b|\bs[aã]o paulo\b|"
        r"\bmexico\b|\bargentina\b|\bbuenos aires\b|\bchile\b|\bsantiago\b|\bcolombia\b|\bbogot[aá]\b|"
        r"\bperu\b|\blima\b|\bcosta rica\b|\bnew york\b|\bsan francisco\b|\bseattle\b|\baustin\b|"
        r"\bboston\b|\bchicago\b|\bdenver\b|\batlanta\b|\bmiami\b|\blos angeles\b|\bsan jose\b|"
        r"\bsunnyvale\b|\bpalo alto\b|\bbellevue\b|\bnew jersey\b|\bwashington,? d\.?c\.?\b|"
        r"\bsan diego\b|\bdallas\b|\bhouston\b|\bphiladelphia\b|\bphoenix\b|\bminneapolis\b|\bd\.?c\.?\b",
    ),
    (
        "apac",
        r"\bindia\b|\bbangalore\b|\bbengaluru\b|\bhyderabad\b|\bpune\b|\bmumbai\b|\bdelhi\b|"
        r"\bsingapore\b|\bjapan\b|\btokyo\b|\bchina\b|\bshanghai\b|\bbeijing\b|\bshenzhen\b|"
        r"\bhong kong\b|\bkorea\b|\bseoul\b|\btaiwan\b|\btaipei\b|\bindonesia\b|\bjakarta\b|"
        r"\bmalaysia\b|\bkuala lumpur\b|\bthailand\b|\bbangkok\b|\bvietnam\b|\bhanoi\b|"
        r"\bho chi minh\b|\bphilippines\b|\bmanila\b|\bpakistan\b|\bbangladesh\b",
    ),
]

REMOTE_RE = (
    r"\bremote\b|\banywhere\b|\bdistributed\b|home ?office|\bworldwide\b|\bemea\b|\btelework\b|"
    r"\beurope\b|\beuropean union\b|\beverywhere\b|work from home"
)
WORLDWIDE_RE = r"\bworldwide\b|\banywhere\b|\bglobal\b|\bemea\b|\beurope\b|\beu\b"
HYBRID_RE = r"\bhybrid\b|\bpartially remote\b|\bremote[- ]friendly\b"
# Geography a remote posting can be worked from that includes France: if one of these
# is named next to a foreign country ("Remote - USA / Remote - EMEA"), the European
# option is real and the posting stays admissible.
BROAD_GEO_RE = (
    r"\bemea\b|\beurope\b|\beuropean union\b|\beu\b|\beea\b|\bschengen\b|"
    r"\bworldwide\b|\banywhere\b|\beverywhere\b|\bglobal(?:ly)?\b|\bthe world\b"
)

# Countries the search will physically move to (or work from in person). Tested before
# the non-target list, so a posting offering both ("Madrid / Berlin") keeps the target.
TARGET_COUNTRY_RULES: list[tuple[str, str]] = [
    (
        "fr",
        r"\bfrance\b|\bfrench\b|\bparis\b|\blyon\b|\blille\b|\bnantes\b|\bbordeaux\b|\btoulouse\b|"
        r"\bmarseille\b|\bsophia antipolis\b|\brennes\b|\bgrenoble\b|\bmontpellier\b|\bstrasbourg\b|"
        r"\bnancy\b|\brouen\b|\bdijon\b|\bb[eé]thune\b|\bsaclay\b|\bissy[- ]les[- ]moulineaux\b|"
        r"\bputeaux\b|\bnanterre\b|\bm[eé]rignac\b|\bclichy\b|\blevallois\b",
    ),
    (
        "de",
        r"\bgermany\b|\bdeutschland\b|\bberlin\b|\bmunich\b|\bm[uü]nchen\b|\bhamburg\b|\bfrankfurt\b|"
        r"\bcologne\b|\bk[oö]ln\b|\bstuttgart\b|\bleipzig\b|\bdresden\b|\bd[uü]sseldorf\b|\bdarmstadt\b|"
        r"\bkarlsruhe\b|\bpotsdam\b|\bwalldorf\b|\bjena\b|\bbielefeld\b|\bessen\b|\bbonn\b|\bn[uü]rnberg\b|"
        r"\bnuremberg\b|\bmannheim\b|\bfreiburg\b|\bheidelberg\b|\bm[uü]nster\b|\bbochum\b|\bdortmund\b|"
        r"\bhannover\b|\bhanover\b|\bbremen\b|\bregensburg\b|\baugsburg\b|\bwolfsburg\b|\bingolstadt\b|"
        r"\bleonberg\b|\bgr[uü]nwald\b|\bunterschlei[sß]heim\b|\bgarching\b|\bt[uü]bingen\b|\bravensburg\b",
    ),
    (
        "ch",
        r"\bswitzerland\b|\bswiss\b|\bchf\b|\bz[uü]rich\b|\bzurich\b|\bgeneva\b|\bgen[eè]ve\b|\bbasel\b|"
        r"\bbern\b|\blausanne\b|\bzug\b|\bwinterthur\b|\bfribourg\b|\blugano\b|\bluzern\b|\blucerne\b|"
        r"\bst\.? gallen\b|\bvevey\b|\bneuch[aâ]tel\b|\bbienne\b|\bthun\b|\bepfl\b|\beth ?z[uü]rich\b|\bethz\b|\bswisscom\b|"
        r"\brotkreuz\b|\bwallisellen\b|\bop[fn]ikon\b|\bdietikon\b|\bjona\b",
    ),
    (
        "nl",
        r"\bnetherlands\b|\bholland\b|\bamsterdam\b|\brotterdam\b|\butrecht\b|\beindhoven\b|\bden haag\b|"
        r"\bthe hague\b|\bdelft\b|\bgroningen\b|\bhaarlem\b|\bnijmegen\b|\btilburg\b|\bmaastricht\b|"
        r"\benschede\b|\barnhem\b|\bamersfoort\b|\bzwolle\b|\bleiden\b|\bbreda\b|\bapeldoorn\b|"
        r"\bden bosch\b|\bhoofddorp\b|\bnieuwegein\b",
    ),
]

# Everything a *physical* role must not be in once the target countries are excluded:
# the UK, the rest of Europe, and the out-of-scope world regions. Only consulted after
# the target list missed, and only the sign matters (target vs other vs unknown).
NON_TARGET_COUNTRY_RE = "|".join(
    [
        r"\bunited kingdom\b|\buk\b|\bengland\b|\bscotland\b|\bwales\b|\blondon\b|\bmanchester\b|"
        r"\bedinburgh\b|\bglasgow\b|\bbleeds\b|\bbristol\b|\bcambridge\b|\boxford\b|\bsheffield\b|"
        r"\bireland\b|\bdublin\b|\bcork\b|\bgalway\b|\bspain\b|\bmadrid\b|\bbarcelona\b|\bvalencia\b|"
        r"\bseville\b|\bbilbao\b|\bmalaga\b|\bportugal\b|\blisbon\b|\bporto\b|\bitaly\b|\bmilan\b|"
        r"\brome\b|\bturin\b|\baustria\b|\bvienna\b|\bwien\b|\bpoland\b|\bwarsaw\b|\bkrakow\b|"
        r"\bwroclaw\b|\bczech\b|\bczechia\b|\bprague\b|\bbrno\b|\bdenmark\b|\bcopenhagen\b|\bsweden\b|"
        r"\bstockholm\b|\bg[oö]teborg\b|\bnorway\b|\boslo\b|\bfinland\b|\bhelsinki\b|\bbelgium\b|"
        r"\bbrussels\b|\bleuven\b|\bghent\b|\bantwerp\b|\bluxembourg\b|\bromania\b|\btimi[sș]oara\b|"
        r"\bbucharest\b|\bhungary\b|\bbudapest\b|\bgreece\b|\bathens\b|\bestonia\b|\btallinn\b|"
        r"\blatvia\b|\briga\b|\blithuania\b|\bvilnius\b|\bslovakia\b|\bbratislava\b|\bslovenia\b|"
        r"\bljubljana\b|\bcroatia\b|\bzagreb\b|\bbulgaria\b|\bsofia\b|\bcyprus\b|\bmalta\b|\biceland\b|"
        r"\baustralia\b|\bsydney\b|\bmelbourne\b|\bbrisbane\b|\bperth\b|\badelaide\b|\bcanberra\b|"
        r"\bnew zealand\b|\bauckland\b|\bwellington\b|"
        # Short US forms the region patterns miss; read from the location only, where a
        # lowercase "us" pronoun cannot appear.
        r"\bus\b|\busa\b|\bu\.s\.?a?\.?\b|\bus based\b|\bnorth america\b|\bnyc\b|\bn\.?y\.?c\.?\b|"
        r"\bsf\b|\bsan fran\b|\btexas\b|\bcalifornia\b|"
        # EMEA cities outside the target: in region, out of reach for a physical role.
        r"\bisrael\b|\btel aviv\b|\bherzliya\b|\bhaifa\b|\bjerusalem\b|\bturkey\b|\bistanbul\b|"
        r"\bankara\b|\buae\b|\bdubai\b|\babu dhabi\b|\bqatar\b|\bdoha\b|\bsaudi\b|\briyadh\b|"
        r"\bkuwait\b|\bbahrain\b|\boman\b|\bmuscat\b|\blebanon\b|\bbeirut\b|\bjordan\b|\bamman\b|"
        r"\begypt\b|\bcairo\b|\bmorocco\b|\bcasablanca\b|\btunisia\b|\btunis\b|\balgeria\b|\balgiers\b|"
        r"\bsouth africa\b|\bcape town\b|\bjohannesburg\b|\bdurban\b|\bpretoria\b|\bkenya\b|\bnairobi\b|"
        r"\bnigeria\b|\blagos\b|\bghana\b|\baccra\b|\barmenia\b|\byerevan\b|\bgeorgia\b|\btbilisi\b|"
        r"\bazerbaijan\b|\bbaku\b|\bkazakhstan\b|\balmaty\b|\buzbekistan\b|\btashkent\b",
        *[pattern for _, pattern in REGION_OUT_OF_SCOPE],
    ]
)

# Language detection. Marker counts over title + description; umlauts count triple
# because they are near-conclusive for German and never appear in English/French JDs.
GERMAN_MARKERS = (
    r"\bwir\b|\buns\b|\bunser(e[nmrs]?)?\b|\bdein(e[nmrs]?)?\b|\bdu\b|\bsie\b|\bist\b|\bsind\b|\bund\b|"
    r"\bmit\b|\bf[uü]r\b|\bbei\b|\bals\b|\bzum\b|\bzur\b|\bvon\b|\bim\b|\bein(e[nmrs]?)?\b|\bnicht\b|"
    r"\bauch\b|\bkenntnisse\b|\berfahrung\b|\baufgaben\b|\bprofil\b|\bbewerbung\b|\bstellenangebot\b|"
    r"\bvollzeit\b|\bteilzeit\b|\bunbefristet\b|\bberufserfahrung\b|\bansprechpartner\b|\barbeiten\b|"
    r"\bsoftwareentwickler\b|\bentwickler\b|\bingenieur\b|\bbetreuer\b|\bmitarbeiter\b|\breferent\b|"
    r"\bberater\b|\bleiter\b|\bfachbereich\b|\bsystemadministrator\b|\bumgebung\b|\bkenntnisse\b"
)
FRENCH_MARKERS = (
    r"\bnous\b|\bvous\b|\bvotre\b|\bvos\b|\bnotre\b|\bnos\b|\bune\b|\bdes\b|\bles\b|\bpour\b|"
    r"\bavec\b|\bdans\b|\bsur\b|\best\b|\bsont\b|\bet\b|\baux\b|\bdu\b|\bmissions\b|\bprofil\b|"
    r"\bcomp[eé]tences\b|\bexp[eé]rience\b|\brejoindre\b|[\u00e9]quipe\b|\bposte\b|\bcontrat\b|"
    r"\bcdi\b|\bcdd\b|\balternance\b|\bstage\b|\bd[eé]veloppeur\b|\bing[eé]nieur\b|\bresponsable\b|"
    r"\bcharg[eé]\b|\bsoci[eé]t[eé]\b|\bcandidature\b|\bposte\b|\br[eé]mun[eé]ration\b"
)
ENGLISH_MARKERS = (
    r"\bthe\b|\band\b|\bwith\b|\byou\b|\byour\b|\bwe\b|\bour\b|\bis\b|\bare\b|\bfor\b|\bto\b|"
    r"\bof\b|\bin\b|\bon\b|\bas\b|\bwill\b|\bhave\b|\bexperience\b|\bteam\b|\brole\b|\bskills\b|"
    r"\brequirements\b|\bresponsibilities\b|\babout\b|\byou'll\b|\bwe're\b|\bengineering\b"
)
SCOPE_BLOCKED_RE = (
    r"united states|usa|u\.s\.|us only|us-remote|americas|canada|latam|brazil|india|"
    r"singapore|japan|china|korea|new york|san francisco|austin|seattle|boston|chicago|"
    r"toronto|vancouver|mexico|argentina|philippines|vietnam"
)
SCOPE_OPEN_RE = (
    r"europe|emea|european union|\beu\b|france|germany|deutschland|switzerland|swiss|"
    r"netherlands|spain|portugal|italy|poland|ireland|uk|united kingdom|london|remote \(eu"
)

DEFAULTS: dict = {
    "db": ".jobradar/state.db",
    "companies_file": "companies.yaml",
    "timeout": 25,
    "cache_minutes": 0,
    # Keys for the sources that need one: a `.env` beside this file, or wherever this points.
    "env_file": ".env",
    # Sources fetch in parallel: they are network-bound and mutually independent. Raise it
    # for a cold full scan, set 1 to serialise when a provider starts rate-limiting.
    "scan_workers": 8,
    "user_agent": "jobradar/0.1 (personal job-search poller)",
    "min_score": 3,
    # Admission scope: JD language and geography. Anything outside is stored with
    # decision "out" (inspectable, never shown by default).
    "allowed_languages": ["en", "fr", "unknown"],
    "allowed_regions": ["eu", "emea", "au", "unknown"],
    # On-site/hybrid roles must be in one of these; remote roles must either name one
    # of them or be open to a broad region (EU/EMEA/worldwide) that includes France.
    "target_countries": ["fr", "de", "ch", "nl"],
    "title_rules": [[p, w, t] for p, w, t in TITLE_RULES],
    "negative_rules": [[p, w, t] for p, w, t in NEGATIVE_RULES],
    "feeds": [
        {"name": "hn-whoishiring", "type": "hn", "url": "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=6"},
        {"name": "weworkremotely-devops", "type": "rss", "url": "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss"},
        {"name": "euremotejobs", "type": "rss", "url": "https://euremotejobs.com/feed/"},
        {"name": "swissdevjobs", "type": "rss", "url": "https://swissdevjobs.ch/rss"},
        {"name": "germantechjobs", "type": "rss", "url": "https://germantechjobs.de/rss"},
        {"name": "remoteok", "type": "remoteok", "url": "https://remoteok.com/api"},
        {"name": "arbeitnow", "type": "arbeitnow", "url": "https://www.arbeitnow.com/api/job-board-api"},
        {"name": "cryptojobslist", "type": "rss", "url": "https://cryptojobslist.com/rss"},
    ],
}


def load_config(path: pathlib.Path | None = None) -> dict:
    """Deep-merge config.yaml over DEFAULTS."""
    cfg = copy.deepcopy(DEFAULTS)
    path = path or root() / "config.yaml"
    if path.exists():
        user = yaml.safe_load(path.read_text()) or {}
        # The user config may name the file, so it is read before any `${VAR}` is expanded.
        named = user.get("env_file") if isinstance(user.get("env_file"), str) else None
        load_env_file(path.parent / str(named or cfg.get("env_file", ".env")))
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(_expand(value))
            else:
                cfg[key] = _expand(value)
    cfg["root"] = str(root())
    return cfg


def load_companies(path: pathlib.Path | None = None) -> list[dict]:
    """Read the watchlist; returns [] when the file does not exist yet."""
    cfg = load_config()
    path = path or root() / str(cfg["companies_file"])
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    rows = data.get("companies") or []
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or "ats" not in row or "token" not in row:
            continue
        key = (str(row["ats"]).lower(), str(row["token"]).lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "name": row.get("name") or row["token"],
                "ats": str(row["ats"]).lower(),
                "token": str(row["token"]),
                "market": row.get("market", ""),
                "tier": row.get("tier", ""),
                "auto": bool(row.get("auto", False)),
                "enabled": bool(row.get("enabled", True)),
            }
        )
    return out


def save_companies(companies: list[dict], path: pathlib.Path | None = None) -> pathlib.Path:
    cfg = load_config()
    path = path or root() / str(cfg["companies_file"])
    unique: dict[tuple[str, str], dict] = {}
    for company in companies:
        unique.setdefault((str(company["ats"]).lower(), str(company["token"]).lower()), company)
    ordered = sorted(unique.values(), key=lambda c: (str(c["name"]).lower(), c["ats"]))
    body = yaml.safe_dump(
        {"companies": ordered}, sort_keys=False, allow_unicode=True, width=100
    )
    header = (
        "# jobradar watchlist — one entry per employer ATS board.\n"
        "# `auto: true` entries were learned from a job URL seen in an aggregator feed.\n"
        "# Add with:  jobradar discover '<name-or-careers-url>' --write\n\n"
    )
    path.write_text(header + body)
    return path
