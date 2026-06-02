from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import re
from typing import Any
from zoneinfo import ZoneInfo

from .config import KST_TZ
from .events import MarketEvent, normalize_text, normalize_token


EXTRACTOR_NAME = "rules_v1"
EXCHANGE_TOKENS = {"AMEX", "NASDAQ", "NAS", "NYSE", "N", "O", "TSX", "US"}
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

GENERIC_NEWS_RE = re.compile(
    r"\b(best|top)\s+\d*\s*(stocks?|etfs?)\s+to\s+buy\b|"
    r"\bstock quote price and forecast\b|"
    r"\bforecast\b.*\bcnn\b|"
    r"\bwatchlist\b",
    re.I,
)
PUBLISHER_SUFFIX_RE = re.compile(r"\s+(?:-|\|)\s+[A-Z0-9][A-Za-z0-9 .&'/-]{1,80}$")
LOW_SIGNAL_COMMENTARY_RE = re.compile(
    r"stock forecast|analyst ratings|predictions?|buy,\s*wait,\s*or\s*sell|"
    r"should you add|top holding|trade strategy|valuation signals|buy list|"
    r"how high can .* stock price rise|trade .* stock pre-market|"
    r"investment story|story is shifting|ai hopes? and fresh concerns|"
    r"which .* better buy|better buy now|how to play|earnings strategy|"
    r"exposure ahead of|best stocks?|best etfs?|stocks? to buy for \d+ years?|"
    r"stock picks?|top .* pick|large-cap pick|stands out as .* pick|"
    r"billionaires?|hedge fund|steve cohen",
    re.I,
)
LOW_SIGNAL_CORPORATE_ACTION_RE = re.compile(
    r"monthly dividend|dividend of \$|to issue .* dividend|"
    r"acquisition lawsuit|settles? .* acquisition lawsuit|"
    r"settlement over .* buyout|shareholders? reach .* settlement",
    re.I,
)
MATERIAL_CORPORATE_ACTION_RE = re.compile(
    r"\b(?:ipo|initial public offering|s-1|listing|lists?|listed|debut|"
    r"buyback|repurchase|acquisition|acquire|merger|buyout|takeover|"
    r"stake|strategic investment|bids?|offers?|proposal|proposes?|"
    r"rebuff(?:ed|s)?|reject(?:ed|s)?)\b",
    re.I,
)
LOW_SIGNAL_MACRO_QUOTE_RE = re.compile(
    r"\bprice today\b|\bprice chart\b|\bprice per barrel\b",
    re.I,
)
LOW_SIGNAL_OFFICIAL_MACRO_TITLE_RE = re.compile(
    r"\benforcement actions?\b|\btermination of enforcement actions?\b|"
    r"\bcease and desist\b|\bcivil money penalty\b|\bprohibition order\b|"
    r"\btakes oath\b|\boath of office\b|\bsworn in\b|"
    r"\bunanimously selects? .{0,80}\bchairman\b",
    re.I,
)
OFFICIAL_MACRO_SIGNAL_RE = re.compile(
    r"\bfomc\b|federal open market committee|interest rates?|federal funds|"
    r"monetary policy|policy statement|inflation|\bcpi\b|\bpce\b|"
    r"treasury|yields?|balance sheet|beige book|economic projections",
    re.I,
)
OFFICIAL_GEO_SIGNAL_RE = re.compile(
    r"tariff|sanction|china|russia|iran|ukraine|taiwan|trade|export|import|"
    r"semiconductor|chips?\b|artificial intelligence|\bai\b|energy|oil|gas|"
    r"critical minerals?|market|econom(?:y|ic)|treasury|tax|bank|crypto|"
    r"investment|antitrust|visa|immigration|supply chain",
    re.I,
)
HORMUZ_TOLL_RE = re.compile(
    r"(?:\bhormuz\b|strait of hormuz).{0,160}"
    r"\b(?:tolls?|tolling|fees?|fee regime|toll regime|toll system|"
    r"charge(?:s|d|ing)?|collect(?:s|ed|ing)?|payment|bitcoin)\b|"
    r"\b(?:tolls?|tolling|fees?|fee regime|toll regime|toll system|"
    r"charge(?:s|d|ing)?|collect(?:s|ed|ing)?|payment|bitcoin)\b"
    r".{0,160}(?:\bhormuz\b|strait of hormuz)",
    re.I,
)
IRAN_DEAL_CONDITIONS_RE = re.compile(
    r"(?:\biran\b.{0,220}\b(?:deal|agreement|peace|ceasefire|terms?)\b"
    r".{0,220}\b(?:condition|conditions|require|requires|required|"
    r"prerequisite|must|mandatory|demand|demands)\b)|"
    r"(?:\b(?:deal|agreement|peace|ceasefire|terms?)\b.{0,220}\biran\b"
    r".{0,220}\b(?:condition|conditions|require|requires|required|"
    r"prerequisite|must|mandatory|demand|demands)\b)|"
    r"(?:\babraham accords?\b.{0,260}\b(?:iran|tehran|saudi|qatar|egypt|"
    r"jordan|turkey|pakistan|gulf)\b)|"
    r"(?:\b(?:saudi|qatar|egypt|jordan|turkey|pakistan)\b.{0,260}"
    r"\babraham accords?\b)",
    re.I,
)
LOW_SIGNAL_OFFICIAL_GEO_TITLE_RE = re.compile(
    r"\bnominations?\b|withdrawal sent to the senate|memorial day|police week|"
    r"\bproclamation\b",
    re.I,
)

STALE_EARNINGS_EVENT_DAYS = 7
MONTH_NAME_TO_NUMBER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
EARNINGS_REPORTED_DATE_RE = re.compile(
    r"\b(?:reported|reports|posted|announced|released)\b"
    r"[^.]{0,120}?"
    r"\b(?:results|earnings|quarter|q[1-4])\b"
    r"[^.]{0,120}?"
    r"\b(?:on|dated)\s+"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?|tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{1,2})(?:,\s*(\d{4}))?",
    re.I,
)
INSTITUTIONAL_POSITION_RE = re.compile(
    r"\b(?:berkshire|buffett|13f|institutional|stake|position|share position)\b"
    r".{0,120}?"
    r"\b(?:exits?|exited|sells?|sold|dumps?|dumped|cuts?|cut|trims?|trimmed|"
    r"reduces?|reduced|liquidates?|liquidated)\b|"
    r"\b(?:exits?|exited|sells?|sold|dumps?|dumped|cuts?|cut|trims?|trimmed|"
    r"reduces?|reduced|liquidates?|liquidated)\b"
    r".{0,120}?"
    r"\b(?:berkshire|buffett|13f|institutional|stake|position|share position)\b",
    re.I,
)
CROSS_CATEGORY_EARNINGS_RESULT_RE = re.compile(
    r"\b(?:reports?|reported|posts?|posted|delivers?|delivered|announces?|"
    r"announced|releases?|released|unveils?|unveiled)\b.{0,140}"
    r"\b(?:earnings|results?|revenue|sales|eps|quarterly revenue|guidance)\b|"
    r"\b(?:earnings|results?|revenue|sales|eps|quarterly revenue|guidance)\b"
    r".{0,140}\b(?:beats?|beat|tops?|topped|above|record|raises?|raised|"
    r"boosts?|boosted|buyback|repurchase)\b",
    re.I,
)
COMPANY_POLICY_RISK_RE = re.compile(
    r"export controls?|export restrictions?|trade restrictions?|"
    r"commerce department|entity list|blacklist|sanctions?|tariffs?|"
    r"\b(?:h200|h20)\b|"
    r"(?:china|chinese).{0,100}(?:ai chips?|chip market|semiconductor|huawei)|"
    r"huawei.{0,100}(?:ai chips?|chip market|semiconductor|nvidia)|"
    r"nvidia.{0,100}(?:china.{0,60}(?:market|approval|approvals?|revenue)|huawei)",
    re.I,
)
COMPANY_POLICY_SUPPORT_RE = re.compile(
    r"\b(?:chips\s+act|chip\s+act|commerce department|department of commerce|"
    r"semiconductor(?:s)?\s+(?:grant|funding|award|subsidy|support)|"
    r"grant|funding|award|subsidy|incentive|support)\b"
    r".{0,180}\b(?:foundry|fab|chip|semiconductor|quantum|manufactur(?:e|ing))\b|"
    r"\b(?:foundry|fab|chip|semiconductor|quantum|manufactur(?:e|ing))\b"
    r".{0,180}\b(?:chips\s+act|commerce department|department of commerce|"
    r"grant|funding|award|subsidy|incentive|support)\b",
    re.I,
)
COMPANY_POLICY_RISK_WINDOW_CHARS = 240

TICKER_STOPWORDS = {
    "AI",
    "AFP",
    "AP",
    "AM",
    "API",
    "BBC",
    "CEO",
    "CFO",
    "CNBC",
    "CNN",
    "CPI",
    "DXY",
    "ETF",
    "EPS",
    "EST",
    "FY",
    "GAAP",
    "Fed".upper(),
    "FOMC",
    "HQ",
    "IPO",
    "M&A",
    "NASDAQ",
    "NYT",
    "NYSE",
    "PCE",
    "PM",
    "PPI",
    "Q1",
    "Q2",
    "Q3",
    "Q4",
    "SEC",
    "SPACEX",
    "US",
    "USD",
    "VIX",
    "WSJ",
    "WTI",
}

KNOWN_TICKERS = {
    "AAPL",
    "BA",
    "MSFT",
    "NVDA",
    "GOOGL",
    "GOOG",
    "AMZN",
    "META",
    "BRK-B",
    "TSLA",
    "AVGO",
    "JPM",
    "LLY",
    "V",
    "MA",
    "UNH",
    "XOM",
    "COST",
    "HD",
    "PG",
    "JNJ",
    "ABBV",
    "WMT",
    "NFLX",
    "BAC",
    "CRM",
    "ORCL",
    "CVX",
    "MRK",
    "KO",
    "PEP",
    "AMD",
    "ASML",
    "AXP",
    "BKR",
    "CBRE",
    "CDW",
    "DELL",
    "TMO",
    "ADBE",
    "CSCO",
    "ACN",
    "LIN",
    "MCD",
    "MRVL",
    "ZS",
    "BBY",
    "HPQ",
    "HPE",
    "ABT",
    "DHR",
    "TXN",
    "QCOM",
    "CMCSA",
    "CRWV",
    "DLR",
    "GE",
    "GEV",
    "NEE",
    "PM",
    "PFE",
    "RTX",
    "UNP",
    "HON",
    "INTC",
    "IBM",
    "AMGN",
    "LRCX",
    "MU",
    "NEM",
    "NBIS",
    "NOW",
    "SNDK",
    "SLM",
    "SNOW",
    "STX",
    "T",
    "TMUS",
    "TSM",
    "UAL",
    "UBER",
    "URI",
    "VRT",
    "WDC",
}

COMPANY_ALIASES = {
    "abbott": "ABT",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "american express": "AXP",
    "amd": "AMD",
    "apple": "AAPL",
    "asml": "ASML",
    "at&t": "T",
    "baker hughes": "BKR",
    "boeing": "BA",
    "broadcom": "AVGO",
    "cbre": "CBRE",
    "cdw": "CDW",
    "comcast": "CMCSA",
    "coreweave": "CRWV",
    "digital realty": "DLR",
    "dell": "DELL",
    "ge aerospace": "GE",
    "ge vernov": "GEV",
    "ge vernova": "GEV",
    "go pro": "GPRO",
    "gopro": "GPRO",
    "google": "GOOGL",
    "honeywell": "HON",
    "hp": "HPQ",
    "hp inc": "HPQ",
    "hewlett packard enterprise": "HPE",
    "ibm": "IBM",
    "intel": "INTC",
    "jpmorgan": "JPM",
    "lam research": "LRCX",
    "meta": "META",
    "microsoft": "MSFT",
    "micron": "MU",
    "micron technology": "MU",
    "netflix": "NFLX",
    "newmont": "NEM",
    "nvidia": "NVDA",
    "procter & gamble": "PG",
    "procter gamble": "PG",
    "qualcomm": "QCOM",
    "service now": "NOW",
    "servicenow": "NOW",
    "sandisk": "SNDK",
    "salesforce": "CRM",
    "seagate": "STX",
    "seagate technology": "STX",
    "marvell": "MRVL",
    "marvell technology": "MRVL",
    "zscaler": "ZS",
    "best buy": "BBY",
    "snowflake": "SNOW",
    "spacex": "SPACEX",
    "t-mobile": "TMUS",
    "t mobile": "TMUS",
    "tesla": "TSLA",
    "texas instruments": "TXN",
    "tsmc": "TSM",
    "uber": "UBER",
    "united airlines": "UAL",
    "united rentals": "URI",
    "unitedhealth": "UNH",
    "unitedhealth group": "UNH",
    "vertiv": "VRT",
    "western digital": "WDC",
}

EARNINGS_OWNER_TERM_RE = re.compile(
    r"\b(?:earnings|results?|reported|reports?|posted|posts?|"
    r"revenue|sales|eps|guidance|outlook|forecast|quarter|q[1-4]|"
    r"fiscal\s+q[1-4]|fy\d{2,4})\b",
    re.I,
)
GENERIC_EARNINGS_TITLE_RE = re.compile(
    r"\bearnings\s+live\s+updates?\b|\blive\s*:\b|\bstock\s+market\s+today\b",
    re.I,
)
CLOUD_PROVIDER_CONTEXT_RE = re.compile(
    r"\b(?:aws|cloud|deal|contract|spend|spending|commitment|customer|"
    r"partnership|infrastructure|capacity|hyperscaler)\b",
    re.I,
)
CLOUD_PROVIDER_PATTERNS = {
    "AMZN": re.compile(
        r"\b(?:aws|amazon\s+web\s+services|amazon\s+cloud|amazon's\s+cloud)\b",
        re.I,
    ),
    "MSFT": re.compile(r"\b(?:azure|microsoft\s+azure)\b", re.I),
    "GOOGL": re.compile(r"\b(?:google\s+cloud|gcp)\b", re.I),
    "ORCL": re.compile(r"\b(?:oracle\s+cloud|oci)\b", re.I),
}

MACRO_SUBJECTS = (
    ("USD_KRW", re.compile(r"\busd[/ -]?krw\b|\bkorean won\b|\bwon\b", re.I)),
    ("VIX", re.compile(r"\bvix\b|volatility", re.I)),
    ("DXY", re.compile(r"\bdxy\b|dollar index", re.I)),
    ("GOLD", re.compile(r"\bgold\b|xau[/ -]?usd", re.I)),
    ("OIL", re.compile(r"\bwti\b|\bbrent\b|\boil\b|crude", re.I)),
    ("FOMC", re.compile(r"\bfomc\b|federal open market committee", re.I)),
    ("RATES", re.compile(r"10-year|treasury|yield|rates?|fed|fomc", re.I)),
    ("CPI", re.compile(r"\bcpi\b|inflation", re.I)),
    ("PCE", re.compile(r"\bpce\b", re.I)),
    ("JOBS", re.compile(r"\bjobs?\b|payroll|unemployment", re.I)),
)

GEO_SUBJECTS = (
    ("TRUMP_XI", re.compile(r"trump.*xi|xi.*trump|us[- ]china|u\.s\.-china", re.I)),
    ("IRAN", re.compile(r"\biran\b", re.I)),
    ("RUSSIA", re.compile(r"\brussia\b|ukraine", re.I)),
    (
        "SOUTH_CHINA_SEA",
        re.compile(
            r"south china sea|spratly|scarborough|philippines.*china|"
            r"china.*philippines",
            re.I,
        ),
    ),
    ("VENEZUELA", re.compile(r"\bvenezuela\b|\bcaracas\b|\bmaduro\b", re.I)),
    ("SAUDI", re.compile(r"\bsaudi\b|\briyadh\b|\baramco\b", re.I)),
    ("RED_SEA", re.compile(r"\bred sea\b|\bsuez\b|\bhouthi\b|\byemen\b", re.I)),
    ("MIDDLE_EAST", re.compile(r"middle east|israel|gaza|hamas|hezbollah", re.I)),
    ("TAIWAN", re.compile(r"\btaiwan\b", re.I)),
    ("NORTH_KOREA", re.compile(r"north korea|pyongyang", re.I)),
    ("CHINA", re.compile(r"\bchina\b|\bxi\b", re.I)),
    ("TARIFFS", re.compile(r"tariff|duties", re.I)),
    ("SANCTIONS", re.compile(r"sanction", re.I)),
    ("WHITE_HOUSE", re.compile(r"white house|executive order", re.I)),
)

GEO_OBJECT_PATTERNS = (
    (
        "hormuz_shipping",
        re.compile(
            r"hormuz|strait of hormuz",
            re.I,
        ),
    ),
    (
        "taiwan_warning",
        re.compile(r"taiwan|strait crisis", re.I),
    ),
    (
        "iran_nuclear",
        re.compile(r"nuclear|iaea|uranium|enrichment", re.I),
    ),
    (
        "sanctions_enforcement",
        re.compile(r"sanctions?|treasury|shadow fleet|teapot|refiner|irgc oil", re.I),
    ),
    (
        "export_controls_ai",
        re.compile(
            r"export controls?|chips?|semiconductor|huawei|deepseek|nvidia|"
            r"artificial intelligence|\bai\b",
            re.I,
        ),
    ),
    (
        "military_escalation",
        re.compile(
            r"\bwar\b|missiles?|attack(?:s|ed|ing)?|strike|gunboat|"
            r"invasion|border clash|coup",
            re.I,
        ),
    ),
    (
        "ceasefire_talks",
        re.compile(r"ceasefire|truce|peace|deadline|extension|negotiat", re.I),
    ),
    (
        "trade_tariffs",
        re.compile(r"tariffs?|duties|trade|exports?|imports?", re.I),
    ),
    (
        "summit_diplomacy",
        re.compile(r"summit|meeting|talks?", re.I),
    ),
    (
        "market_pressure",
        re.compile(r"markets?|inflation|energy|oil|brent|wti|commodit", re.I),
    ),
    (
        "intelligence_cyber",
        re.compile(r"spies|intelligence|cyberattacks?|hack(?:s|ing)?", re.I),
    ),
)

GEO_OBJECT_ENTITIES = (
    ("hormuz", re.compile(r"hormuz|strait of hormuz", re.I)),
    ("south_china_sea", re.compile(r"south china sea|spratly|scarborough", re.I)),
    ("red_sea", re.compile(r"red sea|suez", re.I)),
    ("venezuela", re.compile(r"\bvenezuela\b|\bcaracas\b|\bmaduro\b", re.I)),
    ("saudi", re.compile(r"\bsaudi\b|\briyadh\b|\baramco\b", re.I)),
    ("yemen", re.compile(r"\byemen\b|\bhouthi\b", re.I)),
    ("philippines", re.compile(r"\bphilippines\b|\bmanila\b", re.I)),
    ("taiwan", re.compile(r"\btaiwan\b", re.I)),
    ("iran", re.compile(r"\biran\b|\btehran\b", re.I)),
    ("china", re.compile(r"\bchina\b|\bxi\b|\bbeijing\b", re.I)),
    ("russia", re.compile(r"\brussia\b|\bmoscow\b", re.I)),
    ("ukraine", re.compile(r"\bukraine\b|\bkyiv\b", re.I)),
    ("north_korea", re.compile(r"north korea|pyongyang", re.I)),
    ("israel", re.compile(r"\bisrael\b", re.I)),
    ("gaza", re.compile(r"\bgaza\b|\bhamas\b", re.I)),
    ("lebanon", re.compile(r"\blebanon\b|\bhezbollah\b", re.I)),
)

GEO_OBJECT_ACTIONS = (
    (
        "maritime_blockade",
        re.compile(
            r"blockade|shipping|maritime|tanker|vessels?|cargo|ship|"
            r"container|seiz(?:e|ed|ure)|navy|naval",
            re.I,
        ),
    ),
    ("drone_attack", re.compile(r"drone|uav", re.I)),
    ("missile_attack", re.compile(r"missiles?|rocket", re.I)),
    (
        "market_pressure",
        re.compile(
            r"markets?|stocks?|investors?|investment|outlook|"
            r"cut(?:s|ting)? investment|oil|brent|wti|gas|commodit|energy",
            re.I,
        ),
    ),
    (
        "military_escalation",
        re.compile(
            r"attack(?:s|ed|ing)?|strike|invasion|border clash|clash|gunboat",
            re.I,
        ),
    ),
    ("coup", re.compile(r"\bcoup\b|regime change|military takeover", re.I)),
    ("election_risk", re.compile(r"election|vote|ballot", re.I)),
    (
        "sanctions",
        re.compile(r"sanctions?|treasury|shadow fleet|refiner|irgc oil", re.I),
    ),
    (
        "export_controls",
        re.compile(
            r"export controls?|chips?|semiconductor|huawei|deepseek|nvidia|"
            r"artificial intelligence|\bai\b",
            re.I,
        ),
    ),
    ("trade_tariffs", re.compile(r"tariffs?|duties|trade|exports?|imports?", re.I)),
    (
        "ceasefire_talks",
        re.compile(r"ceasefire|truce|peace|deadline|extension|negotiat", re.I),
    ),
    ("nuclear", re.compile(r"nuclear|iaea|uranium|enrichment", re.I)),
    ("energy_supply", re.compile(r"energy|oil|brent|wti|gas|commodit", re.I)),
    ("cyber_intel", re.compile(r"spies|intelligence|cyberattacks?|hack(?:s|ing)?", re.I)),
    (
        "warning",
        re.compile(r"warn(?:s|ed|ing)?|caution(?:s|ed|ing)?|threatens?", re.I),
    ),
    ("summit_diplomacy", re.compile(r"summit|meeting|talks?", re.I)),
)


@dataclass(frozen=True)
class ExtractedEvent:
    candidate_id: str
    event: MarketEvent
    confidence: float
    reason: str
    extractor: str = EXTRACTOR_NAME

    def link_id(self) -> str:
        raw = f"{self.candidate_id}|{self.event.signature()}|{self.extractor}"
        return sha256(raw.encode("utf-8")).hexdigest()

    def as_record(self) -> dict[str, Any]:
        event_record = self.event.as_record()
        payload = event_record["payload"]
        return {
            "id": self.link_id(),
            "candidate_id": self.candidate_id,
            "event": {
                "signature": event_record["signature"],
                "event_type": payload["event_type"],
                "subject": payload["subject"],
                "effective_date": payload["effective_date"],
                "scope": payload["scope"],
                "period": payload["period"],
                "action": payload["action"],
                "object": payload["object"],
                "stage": payload["stage"],
                "source": self.event.source,
                "title": self.event.title,
                "url": self.event.url,
                "published_at": self.event.published_at,
                "metadata": event_record.get("metadata", {}),
                "payload": payload,
            },
            "extractor": self.extractor,
            "confidence": self.confidence,
            "reason": self.reason,
        }


def _published_date_kst(candidate: dict[str, Any], as_of: datetime) -> str:
    published_at = str(candidate.get("published_at") or "")
    if published_at:
        try:
            parsed = datetime.fromisoformat(published_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
            return parsed.astimezone(ZoneInfo(KST_TZ)).date().isoformat()
        except ValueError:
            pass
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=ZoneInfo(KST_TZ))
    return as_of.astimezone(ZoneInfo(KST_TZ)).date().isoformat()


def _as_of_date_kst(as_of: datetime) -> date:
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=ZoneInfo(KST_TZ))
    return as_of.astimezone(ZoneInfo(KST_TZ)).date()


def _published_at_date_kst(candidate: dict[str, Any], as_of: datetime) -> date:
    return date.fromisoformat(_published_date_kst(candidate, as_of))


def _reference_year(candidate: dict[str, Any], as_of: datetime) -> int:
    published_at = str(candidate.get("published_at") or "")
    if published_at:
        try:
            parsed = datetime.fromisoformat(published_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
            return parsed.astimezone(ZoneInfo(KST_TZ)).year
        except ValueError:
            pass
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=ZoneInfo(KST_TZ))
    return as_of.astimezone(ZoneInfo(KST_TZ)).year


def _detected_earnings_report_date(
    *,
    candidate: dict[str, Any],
    text: str,
    as_of: datetime,
) -> date | None:
    match = EARNINGS_REPORTED_DATE_RE.search(text)
    if not match:
        return None
    month = MONTH_NAME_TO_NUMBER.get(match.group(1).lower().rstrip("."))
    if not month:
        return None
    year = int(match.group(3) or _reference_year(candidate, as_of))
    try:
        return date(year, month, int(match.group(2)))
    except ValueError:
        return None


def _is_stale_earnings_report(
    *,
    candidate: dict[str, Any],
    text: str,
    as_of: datetime,
) -> bool:
    freshness = _earnings_freshness_metadata(
        candidate=candidate,
        text=text,
        as_of=as_of,
    )
    return bool(freshness.get("stale"))


def _earnings_freshness_metadata(
    *,
    candidate: dict[str, Any],
    text: str,
    as_of: datetime,
) -> dict[str, Any]:
    published_date = _published_at_date_kst(candidate, as_of)
    as_of_date = _as_of_date_kst(as_of)
    report_date = _detected_earnings_report_date(
        candidate=candidate,
        text=text,
        as_of=as_of,
    )
    metadata: dict[str, Any] = {
        "as_of_date": as_of_date.isoformat(),
        "published_date": published_date.isoformat(),
        "max_age_days": STALE_EARNINGS_EVENT_DAYS,
    }
    if report_date is None:
        metadata["status"] = "unknown_event_date"
        metadata["event_date_source"] = "published_at"
        metadata["event_date"] = published_date.isoformat()
        metadata["stale"] = False
        return metadata

    age_days = (as_of_date - report_date).days
    metadata.update(
        {
            "status": "stale_event_date"
            if age_days > STALE_EARNINGS_EVENT_DAYS
            else "fresh_event_date",
            "event_date_source": "body_report_date",
            "event_date": report_date.isoformat(),
            "event_age_days": age_days,
            "stale": age_days > STALE_EARNINGS_EVENT_DAYS,
        }
    )
    return metadata


def _event_date_for(category: str, candidate: dict[str, Any], text: str, as_of: datetime) -> str:
    if category == "EARN":
        freshness = _earnings_freshness_metadata(
            candidate=candidate,
            text=text,
            as_of=as_of,
        )
        return str(freshness.get("event_date") or _published_date_kst(candidate, as_of))
    return _published_date_kst(candidate, as_of)


def _strip_publisher_suffix(title: str) -> str:
    return PUBLISHER_SUFFIX_RE.sub("", title).strip()


def _title_fingerprint(title: str) -> str:
    normalized = normalize_text(_strip_publisher_suffix(title))
    if not normalized:
        return "title_unknown"
    return f"title_{sha256(normalized.encode('utf-8')).hexdigest()[:12]}"


def _canonical_geo_object_for(title: str) -> str:
    if IRAN_DEAL_CONDITIONS_RE.search(title):
        return "iran_deal_conditions"
    if HORMUZ_TOLL_RE.search(title):
        return "hormuz_toll_regime"
    entity = ""
    action = ""
    for label, pattern in GEO_OBJECT_ENTITIES:
        if pattern.search(title):
            entity = label
            break
    for label, pattern in GEO_OBJECT_ACTIONS:
        if pattern.search(title):
            action = label
            break
    if entity and action:
        if entity == "taiwan" and action == "warning":
            return "taiwan_warning"
        if entity == "hormuz" and action == "maritime_blockade":
            return "hormuz_shipping"
        return f"{entity}_{action}"
    return ""


def _geo_object_for(title: str) -> str:
    canonical = _canonical_geo_object_for(title)
    if canonical:
        return canonical
    for label, pattern in GEO_OBJECT_PATTERNS:
        if pattern.search(title):
            return label
    return _title_fingerprint(title)


def _event_object_for(category: str, title: str) -> str:
    if category == "MACRO":
        return _title_fingerprint(title)
    if category == "GEO":
        return _geo_object_for(title)
    return normalize_token(category.lower())


def _normalize_ticker(raw: str) -> str:
    text = raw.upper().strip()
    if re.fullmatch(r"(?:FY|CY)\d{2,4}|Q[1-4](?:FY|CY)\d{2,4}", text):
        return ""
    if ":" in text:
        left, right = text.split(":", 1)
        if left in EXCHANGE_TOKENS:
            text = right
        elif right in EXCHANGE_TOKENS:
            text = left
        else:
            text = right
    if ISIN_RE.match(text):
        return ""
    ticker = text.replace(".", "-")
    if "-" in ticker:
        head, suffix = ticker.split("-", 1)
        if suffix in EXCHANGE_TOKENS:
            return head
    if len(ticker) > 6:
        return ""
    return ticker


def _company_subject_candidates(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for match in re.finditer(r"\(([A-Z][A-Z0-9.:-]{0,14})\)", text):
        ticker = _normalize_ticker(match.group(1))
        if ticker and ticker not in TICKER_STOPWORDS:
            candidates.append(
                {
                    "start": match.start(),
                    "end": match.end(),
                    "priority": 1,
                    "ticker": ticker,
                    "reason": "paren_ticker",
                    "match_text": match.group(1),
                }
            )

    normalized = normalize_text(text)
    for alias, ticker in COMPANY_ALIASES.items():
        match = re.search(rf"\b{re.escape(alias)}\b", normalized)
        if not match:
            continue
        candidates.append(
            {
                "start": match.start(),
                "end": match.end(),
                "priority": -len(alias),
                "ticker": ticker,
                "reason": "company_alias",
                "match_text": alias,
            }
        )

    for match in re.finditer(r"\b[A-Z][A-Z0-9.-]{0,5}\b", text):
        token = match.group(0)
        ticker = _normalize_ticker(token)
        if ticker in KNOWN_TICKERS and ticker not in TICKER_STOPWORDS:
            candidates.append(
                {
                    "start": match.start(),
                    "end": match.end(),
                    "priority": 0,
                    "ticker": ticker,
                    "reason": "known_ticker",
                    "match_text": token,
                }
            )
    return sorted(
        candidates,
        key=lambda candidate: (
            int(candidate["start"]),
            int(candidate["priority"]),
            str(candidate["ticker"]),
        ),
    )


def _find_company_subject(text: str, category: str) -> tuple[str, str] | None:
    candidates = _company_subject_candidates(text)

    if not candidates:
        return None
    candidate = candidates[0]
    return str(candidate["ticker"]), str(candidate["reason"])


def _cloud_counterparty_reason(text: str, candidate: dict[str, Any]) -> str:
    ticker = str(candidate.get("ticker") or "")
    start = int(candidate.get("start") or 0)
    end = int(candidate.get("end") or start)
    window = text[max(0, start - 90) : min(len(text), end + 130)]
    if ticker == "AMZN":
        if CLOUD_PROVIDER_PATTERNS["AMZN"].search(window):
            return "cloud_provider_counterparty_context"
        if (
            re.search(r"\bamazon\b", window, re.I)
            and CLOUD_PROVIDER_CONTEXT_RE.search(window)
        ):
            return "cloud_provider_counterparty_context"
    pattern = CLOUD_PROVIDER_PATTERNS.get(ticker)
    if pattern and pattern.search(window) and CLOUD_PROVIDER_CONTEXT_RE.search(window):
        return "cloud_provider_counterparty_context"
    return ""


def _candidate_near_earnings_terms(text: str, candidate: dict[str, Any]) -> bool:
    start = int(candidate.get("start") or 0)
    end = int(candidate.get("end") or start)
    window = text[max(0, start - 120) : min(len(text), end + 180)]
    return bool(EARNINGS_OWNER_TERM_RE.search(window))


def _related_cloud_entities(text: str, primary_subject: str) -> list[str]:
    related: list[str] = []
    for ticker, pattern in CLOUD_PROVIDER_PATTERNS.items():
        if ticker == primary_subject:
            continue
        if pattern.search(text) and ticker not in related:
            related.append(ticker)
    return related


def _ownership_metadata(
    *,
    primary_subject: str,
    basis: str,
    confidence: str,
    related_entities: list[str],
    rejected: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "version": "earnings_primary_subject_v1",
        "primary_subject": primary_subject,
        "basis": basis,
        "confidence": confidence,
        "related_entities": sorted(set(related_entities)),
        "rejected_subject_candidates": rejected,
    }


def _resolve_earnings_primary_subject(
    *,
    title_text: str,
    full_text: str,
) -> dict[str, Any] | None:
    rejected: list[dict[str, str]] = []

    def choose_from(text: str, *, basis_prefix: str) -> dict[str, Any] | None:
        candidates = _company_subject_candidates(text)
        if not candidates:
            return None
        owners: list[dict[str, Any]] = []
        for candidate in candidates:
            ticker = str(candidate["ticker"])
            counterparty_reason = _cloud_counterparty_reason(text, candidate)
            if counterparty_reason:
                entry = {"subject": ticker, "reason": counterparty_reason}
                if entry not in rejected:
                    rejected.append(entry)
                continue
            if _candidate_near_earnings_terms(text, candidate):
                owners.append(candidate)
        if not owners:
            return None
        owner = owners[0]
        ticker = str(owner["ticker"])
        related = _related_cloud_entities(full_text, ticker)
        related.extend(
            str(candidate["ticker"])
            for candidate in candidates
            if str(candidate["ticker"]) != ticker
            and str(candidate["ticker"]) not in related
        )
        return {
            "subject": ticker,
            "reason": str(owner["reason"]),
            "ownership": _ownership_metadata(
                primary_subject=ticker,
                basis=f"{basis_prefix}_{owner['reason']}_near_earnings_terms",
                confidence="high" if basis_prefix == "title" else "medium",
                related_entities=related,
                rejected=rejected,
            ),
        }

    title_resolution = choose_from(title_text, basis_prefix="title")
    if title_resolution is not None:
        return title_resolution

    if (
        GENERIC_EARNINGS_TITLE_RE.search(title_text)
        or not _company_subject_candidates(title_text)
        or rejected
    ):
        return choose_from(full_text, basis_prefix="text")
    return None


def _find_macro_subject(text: str) -> tuple[str, str] | None:
    for subject, pattern in MACRO_SUBJECTS:
        if pattern.search(text):
            return subject, "macro_keyword"
    return "MACRO", "macro_default"


def _find_geo_subject(text: str) -> tuple[str, str] | None:
    for subject, pattern in GEO_SUBJECTS:
        if pattern.search(text):
            return subject, "geo_keyword"
    return None


def _is_low_signal_official_geo(
    candidate: dict[str, Any],
    *,
    title: str,
    text: str,
) -> bool:
    if str(candidate.get("category") or "") != "GEO":
        return False
    provider = str(candidate.get("provider") or "")
    source = str(candidate.get("source") or "")
    if provider != "official_rss" and source != "white-house-presidential-actions":
        return False
    if LOW_SIGNAL_OFFICIAL_GEO_TITLE_RE.search(title):
        return True
    return OFFICIAL_GEO_SIGNAL_RE.search(text) is None


def _is_low_signal_official_macro(candidate: dict[str, Any], *, title: str) -> bool:
    if str(candidate.get("category") or "") != "MACRO":
        return False
    provider = str(candidate.get("provider") or "")
    source = str(candidate.get("source") or "")
    if provider != "official_rss" and source != "federal-reserve-press":
        return False
    if LOW_SIGNAL_OFFICIAL_MACRO_TITLE_RE.search(title):
        return True
    return OFFICIAL_MACRO_SIGNAL_RE.search(title) is None


def _is_low_signal_by_category(category: str, title: str) -> bool:
    if (
        category in {"ANAL", "EARN", "MOVE", "STRAT"}
        and LOW_SIGNAL_COMMENTARY_RE.search(title)
    ):
        return True
    if category == "MA" and LOW_SIGNAL_CORPORATE_ACTION_RE.search(title):
        return True
    if category == "MACRO" and LOW_SIGNAL_MACRO_QUOTE_RE.search(title):
        return True
    return False


def _action_for(category: str, text: str) -> str:
    lower = text.lower()
    if category == "EARN":
        if "guidance" in lower or "outlook" in lower:
            stable_guidance = (
                r"guidance[^.;:|()]{0,50}\b(reaffirm(?:ed|s)?|maintain(?:ed|s)?|"
                r"unchanged|keeps?|holds?)\b|"
                r"\b(reaffirm(?:ed|s)?|maintain(?:ed|s)?|keeps?|holds?)"
                r"[^.;:|()]{0,50}guidance"
            )
            raise_guidance = (
                r"\b(raise[sd]?|boost(?:ed|s)?|increase[sd]?|lift(?:ed|s)?)"
                r"[^.;:|()]{0,50}\b(guidance|outlook)\b|"
                r"\b(guidance|outlook)\b[^.;:|()]{0,50}"
                r"\b(raise[sd]?|boost(?:ed|s)?|increase[sd]?|lift(?:ed|s)?)\b"
            )
            cut_guidance = (
                r"\b(cut|cuts|lower(?:ed|s)?|reduce[sd]?|trim(?:med|s)?)"
                r"[^.;:|()]{0,50}\b(guidance|outlook)\b|"
                r"\b(guidance|outlook)\b[^.;:|()]{0,50}"
                r"\b(cut|cuts|lower(?:ed|s)?|reduce[sd]?|trim(?:med|s)?)\b"
            )
            if re.search(stable_guidance, lower):
                return "guidance_update"
            if re.search(raise_guidance, lower):
                return "guidance_raise"
            if re.search(cut_guidance, lower):
                return "guidance_cut"
            return "guidance_update"
        if re.search(r"earnings|results?|revenue|eps|q[1-4]", lower):
            return "earnings_report"
        return "earnings_related"
    if category == "MA":
        if re.search(
            r"\bipo\b|initial public offering|\bs-1\b|files? publicly|"
            r"(?:nasdaq|nyse).{0,40}\b(?:listing|list|debut)\b|"
            r"\b(?:listing|list|debut)\b.{0,40}(?:nasdaq|nyse)",
            lower,
        ):
            return "ipo"
        if re.search(r"repurchase|buyback", lower):
            return "buyback"
        if re.search(
            r"acquisition|acquire|merger|buyout|takeover|sale|"
            r"\bbids?\b|\boffers?\b|\bproposal\b|\bproposes?\b|"
            r"\brebuff(?:ed|s)?\b|\breject(?:ed|s)?\b",
            lower,
        ):
            return "ma"
        if "strategic investment" in lower or "stake" in lower:
            return "strategic_investment"
        return "corporate_transaction"
    if category == "STRAT":
        if re.search(r"partnership|partner", lower):
            return "partnership"
        if re.search(r"supply|supplier", lower):
            return "supply_deal"
        if re.search(r"investment|invest", lower):
            return "investment"
        return "strategic_update"
    if category == "MOVE":
        if re.search(r"plunge|falls?|drops?|down|selloff", lower):
            return "shares_down"
        if re.search(
            r"surge|soars?|rises?|jumps?|climbs?|gains?|up|rally|"
            r"record highs?|trillion-dollar|trillion dollar|trillion club",
            lower,
        ):
            return "shares_up"
        return "mover"
    if category == "ANAL":
        if "double-downgrade" in lower or "downgrade" in lower:
            return "downgrade"
        if "upgrade" in lower:
            return "upgrade"
        if "price target" in lower:
            return "price_target"
        return "analyst_action"
    if category == "MACRO":
        if (
            re.search(r"\bminutes\b", lower)
            and re.search(r"\bfomc\b|federal open market committee", lower)
        ):
            return "fomc_minutes"
        if re.search(r"fed|fomc|rates?|treasury|yield", lower):
            return "rates_update"
        if re.search(r"oil|wti|brent|crude", lower):
            return "oil_update"
        if "gold" in lower:
            return "gold_update"
        if re.search(r"vix|volatility", lower):
            return "volatility_update"
        return "macro_update"
    if category == "GEO":
        if IRAN_DEAL_CONDITIONS_RE.search(text):
            return "policy_geo"
        if HORMUZ_TOLL_RE.search(text):
            return "policy_geo"
        if re.search(r"tariff|duties", lower):
            return "tariff_policy"
        if "sanction" in lower:
            return "sanctions"
        if re.search(r"summit|talks?|meeting", lower):
            return "diplomacy"
        if re.search(
            r"\bwar\b|missiles?|\battack(?:s|ed|ing)?\b|\btruce\b|"
            r"blockade|drone|uav|strike|invasion|border clash|clash|\bcoup\b",
            lower,
        ):
            return "conflict"
        return "policy_geo"
    return "news"


def _event_type(category: str) -> str:
    return {
        "ANAL": "analyst",
        "EARN": "earnings",
        "GEO": "geo",
        "MA": "corporate_action",
        "MACRO": "macro",
        "MOVE": "mover",
        "STRAT": "strategic",
    }.get(category, "news")


def _scope(category: str) -> str:
    if category in {"MACRO", "GEO"}:
        return "market"
    return "company"


def _metadata_for_event(
    *,
    category: str,
    candidate: dict[str, Any],
    text: str,
    as_of: datetime,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source_category": category,
        "published_date": _published_date_kst(candidate, as_of),
    }
    if category == "EARN":
        metadata["freshness"] = _earnings_freshness_metadata(
            candidate=candidate,
            text=text,
            as_of=as_of,
        )
    return metadata


def _extract_institutional_position_event(
    *,
    candidate: dict[str, Any],
    category: str,
    title: str,
    text: str,
    as_of: datetime,
) -> ExtractedEvent | None:
    if category not in {"EARN", "MA", "MOVE", "STRAT"}:
        return None
    if not INSTITUTIONAL_POSITION_RE.search(text):
        return None
    subject_match = _find_company_subject(text, category)
    if subject_match is None:
        return None
    subject, reason = subject_match
    event = MarketEvent(
        event_type="corporate_action",
        subject=subject,
        effective_date=_published_date_kst(candidate, as_of),
        scope="company",
        action="stake_exit",
        object="institutional_position",
        stage="candidate",
        source=str(candidate.get("source") or ""),
        title=title,
        url=str(candidate.get("url") or ""),
        published_at=str(candidate.get("published_at") or ""),
        metadata={
            "source_category": category,
            "published_date": _published_date_kst(candidate, as_of),
            "event_date_source": "published_at",
            "extracted_from": "institutional_position_pattern",
        },
    )
    confidence = 0.82 if reason in {"paren_ticker", "known_ticker", "company_alias"} else 0.78
    return ExtractedEvent(
        candidate_id=str(candidate["id"]),
        event=event,
        confidence=confidence,
        reason=f"{category}:{reason}:stake_exit",
    )


def _extract_cross_category_earnings_event(
    *,
    candidate: dict[str, Any],
    category: str,
    title: str,
    text: str,
    as_of: datetime,
) -> ExtractedEvent | None:
    if category == "EARN":
        return None
    if "$" not in text and not re.search(
        r"\b(?:earnings|results?|eps|revenue|sales|guidance)\b",
        text,
        re.I,
    ):
        return None
    if not CROSS_CATEGORY_EARNINGS_RESULT_RE.search(text):
        return None
    resolution = _resolve_earnings_primary_subject(
        title_text=_strip_publisher_suffix(title),
        full_text=text,
    )
    if resolution is None:
        return None
    subject = str(resolution["subject"])
    reason = str(resolution["reason"])
    action = _action_for("EARN", text)
    if action == "earnings_related":
        action = "earnings_report"
    event = MarketEvent(
        event_type="earnings",
        subject=subject,
        effective_date=_published_date_kst(candidate, as_of),
        scope="company",
        action=action,
        object=_event_object_for("EARN", title),
        stage="candidate",
        source=str(candidate.get("source") or ""),
        title=title,
        url=str(candidate.get("url") or ""),
        published_at=str(candidate.get("published_at") or ""),
        metadata={
            "source_category": category,
            "published_date": _published_date_kst(candidate, as_of),
            "event_date_source": "published_at",
            "extracted_from": "cross_category_earnings_result",
            "ownership": resolution["ownership"],
        },
    )
    return ExtractedEvent(
        candidate_id=str(candidate["id"]),
        event=event,
        confidence=0.84 if reason in {"company_alias", "paren_ticker", "known_ticker"} else 0.8,
        reason=f"{category}:{reason}:{action}:cross_category_earnings",
    )


def _extract_company_policy_risk_event(
    *,
    candidate: dict[str, Any],
    category: str,
    title: str,
    text: str,
    as_of: datetime,
) -> ExtractedEvent | None:
    if category not in {"GEO", "STRAT"}:
        return None
    subject_match = _find_company_policy_risk_subject(text)
    if subject_match is None:
        return None
    subject, reason = subject_match
    event = MarketEvent(
        event_type="strategic",
        subject=subject,
        effective_date=_published_date_kst(candidate, as_of),
        scope="company",
        action="policy_risk",
        object=_geo_object_for(title),
        stage="candidate",
        source=str(candidate.get("source") or ""),
        title=title,
        url=str(candidate.get("url") or ""),
        published_at=str(candidate.get("published_at") or ""),
        metadata={
            "source_category": category,
            "published_date": _published_date_kst(candidate, as_of),
            "event_date_source": "published_at",
            "extracted_from": "company_policy_risk",
        },
    )
    confidence = 0.84 if reason in {"company_alias", "paren_ticker", "known_ticker"} else 0.8
    return ExtractedEvent(
        candidate_id=str(candidate["id"]),
        event=event,
        confidence=confidence,
        reason=f"{category}:{reason}:policy_risk:company_policy_risk",
    )


def _find_company_policy_risk_subject(text: str) -> tuple[str, str] | None:
    """Find a company only when it appears near the policy-risk phrase.

    Full article bodies often contain unrelated recommendation modules such as
    "Google announces..." or abbreviations like "PM" far away from the actual
    sanctions/tariff sentence. Searching the whole body turns generic geo
    articles into false company policy-risk events.
    """
    for match in COMPANY_POLICY_RISK_RE.finditer(text):
        start = max(0, match.start() - COMPANY_POLICY_RISK_WINDOW_CHARS)
        end = min(len(text), match.end() + COMPANY_POLICY_RISK_WINDOW_CHARS)
        window = text[start:end]
        subject_match = _find_company_subject(window, "STRAT")
        if subject_match is None:
            continue
        subject, reason = subject_match
        if reason == "known_ticker" and len(subject) <= 2:
            continue
        return subject_match
    return None


def _extract_company_policy_support_event(
    *,
    candidate: dict[str, Any],
    category: str,
    title: str,
    text: str,
    as_of: datetime,
) -> ExtractedEvent | None:
    if category not in {"EARN", "GEO", "STRAT", "MOVE", "MA"}:
        return None
    if not COMPANY_POLICY_SUPPORT_RE.search(text):
        return None
    subject_match = _find_company_subject(text, "STRAT")
    if subject_match is None:
        return None
    subject, reason = subject_match
    event = MarketEvent(
        event_type="strategic",
        subject=subject,
        effective_date=_published_date_kst(candidate, as_of),
        scope="company",
        action="policy_support",
        object="semiconductor_policy_support",
        stage="candidate",
        source=str(candidate.get("source") or ""),
        title=title,
        url=str(candidate.get("url") or ""),
        published_at=str(candidate.get("published_at") or ""),
        metadata={
            "source_category": category,
            "published_date": _published_date_kst(candidate, as_of),
            "event_date_source": "published_at",
            "extracted_from": "company_policy_support",
        },
    )
    confidence = 0.84 if reason in {"company_alias", "paren_ticker", "known_ticker"} else 0.8
    return ExtractedEvent(
        candidate_id=str(candidate["id"]),
        event=event,
        confidence=confidence,
        reason=f"{category}:{reason}:policy_support:company_policy_support",
    )


def _earnings_text_subject_rescue(text: str) -> bool:
    if "$" not in text and not re.search(
        r"\b(?:earnings|results?|eps|revenue|sales|guidance)\b",
        text,
        re.I,
    ):
        return False
    return bool(CROSS_CATEGORY_EARNINGS_RESULT_RE.search(text))


def extract_event_from_candidate(
    candidate: dict[str, Any],
    *,
    as_of: datetime,
) -> ExtractedEvent | None:
    events = extract_events_from_candidate(candidate, as_of=as_of)
    return events[0] if events else None


def extract_events_from_candidate(
    candidate: dict[str, Any],
    *,
    as_of: datetime,
) -> list[ExtractedEvent]:
    category = str(candidate.get("category") or "")
    title = str(candidate.get("title") or "")
    summary = str(candidate.get("summary") or "")
    body_text = str(candidate.get("body_text") or "")
    title_without_publisher = _strip_publisher_suffix(title)
    text = f"{title} {summary} {body_text}".strip()
    subject_text = title_without_publisher
    normalized_title = normalize_text(title)

    if (
        not title
        or GENERIC_NEWS_RE.search(normalized_title)
        or _is_low_signal_by_category(category, title_without_publisher)
    ):
        return []
    if _is_low_signal_official_geo(candidate, title=title_without_publisher, text=text):
        return []
    if _is_low_signal_official_macro(candidate, title=title_without_publisher):
        return []

    company_policy_support_event = _extract_company_policy_support_event(
        candidate=candidate,
        category=category,
        title=title,
        text=text,
        as_of=as_of,
    )
    if company_policy_support_event is not None:
        return [company_policy_support_event]

    company_policy_risk_event = _extract_company_policy_risk_event(
        candidate=candidate,
        category=category,
        title=title,
        text=text,
        as_of=as_of,
    )
    if company_policy_risk_event is not None:
        return [company_policy_risk_event]

    extracted: list[ExtractedEvent] = []
    institutional_event = _extract_institutional_position_event(
        candidate=candidate,
        category=category,
        title=title,
        text=text,
        as_of=as_of,
    )
    if institutional_event is not None:
        extracted.append(institutional_event)
    cross_category_earnings_event = _extract_cross_category_earnings_event(
        candidate=candidate,
        category=category,
        title=title,
        text=text,
        as_of=as_of,
    )
    if cross_category_earnings_event is not None:
        extracted.append(cross_category_earnings_event)
        if category == "MA" and not MATERIAL_CORPORATE_ACTION_RE.search(text):
            return extracted
    if category == "EARN" and _is_stale_earnings_report(
        candidate=candidate,
        text=text,
        as_of=as_of,
    ):
        return extracted

    subject_basis_text = subject_text
    event_object_text = title_without_publisher
    earnings_ownership: dict[str, Any] | None = None
    if category == "EARN":
        resolution = _resolve_earnings_primary_subject(
            title_text=subject_text,
            full_text=text,
        )
        title_has_company_candidate = bool(_company_subject_candidates(subject_text))
        allow_text_rescue = (
            GENERIC_EARNINGS_TITLE_RE.search(subject_text)
            or not title_has_company_candidate
        )
        if (
            resolution is None
            and allow_text_rescue
            and _earnings_text_subject_rescue(text)
        ):
            resolution = _resolve_earnings_primary_subject(
                title_text="",
                full_text=text,
            )
            if resolution is not None:
                subject_basis_text = text
        if resolution is None:
            subject_match = None
        else:
            subject_match = (
                str(resolution["subject"]),
                str(resolution["reason"]),
            )
            earnings_ownership = dict(resolution["ownership"])
            if str(earnings_ownership.get("basis") or "").startswith("text_"):
                subject_basis_text = text
    elif category in {"MACRO"}:
        subject_match = _find_macro_subject(subject_text)
    elif category in {"GEO"}:
        if IRAN_DEAL_CONDITIONS_RE.search(text):
            subject_match = ("IRAN", "geo_iran_deal_conditions")
            subject_basis_text = text
            event_object_text = text
        else:
            subject_match = _find_geo_subject(subject_text)
    else:
        subject_match = _find_company_subject(subject_text, category)

    if subject_match is None:
        return extracted

    subject, reason = subject_match
    action_text = subject_basis_text if category in {"EARN", "GEO"} else title_without_publisher
    action = _action_for(category, action_text or text)
    metadata = _metadata_for_event(
        category=category,
        candidate=candidate,
        text=text,
        as_of=as_of,
    )
    if earnings_ownership is not None:
        metadata["ownership"] = earnings_ownership

    event = MarketEvent(
        event_type=_event_type(category),
        subject=subject,
        effective_date=_event_date_for(category, candidate, text, as_of),
        scope=_scope(category),
        action=action,
        object=_event_object_for(category, event_object_text),
        stage="candidate",
        source=str(candidate.get("source") or ""),
        title=title,
        url=str(candidate.get("url") or ""),
        published_at=str(candidate.get("published_at") or ""),
        metadata=metadata,
    )
    confidence = 0.75
    if reason in {"paren_ticker", "known_ticker", "company_alias"}:
        confidence = 0.86
    if category in {"MACRO", "GEO"}:
        confidence = 0.8
    extracted.append(
        ExtractedEvent(
            candidate_id=str(candidate["id"]),
            event=event,
            confidence=confidence,
            reason=f"{category}:{reason}:{action}",
        )
    )
    return extracted


def extract_events(
    candidates: list[dict[str, Any]],
    *,
    as_of: datetime,
) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []
    seen_links: set[str] = set()
    for candidate in candidates:
        for event in extract_events_from_candidate(candidate, as_of=as_of):
            record = event.as_record()
            if record["id"] in seen_links:
                continue
            seen_links.add(record["id"])
            extracted.append(record)
    return extracted
