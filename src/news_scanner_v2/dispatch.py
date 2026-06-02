from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any
from urllib.parse import urlsplit


POLICY_NAME = "dispatch_rules_v1"

SEND_CANDIDATE = "send_candidate"
REVIEW = "review"
REJECT = "reject"
GRADE_A = "A"
GRADE_B = "B"
GRADE_C = "C"

BASE_SCORE_BY_TYPE = {
    "geo": 62,
    "macro": 52,
    "earnings": 50,
    "corporate_action": 50,
    "analyst": 42,
    "strategic": 38,
    "mover": 34,
}

ACTION_BONUS = {
    "conflict": 14,
    "sanctions": 12,
    "tariff_policy": 12,
    "diplomacy": 8,
    "guidance_raise": 12,
    "guidance_cut": 12,
    "guidance_update": 8,
    "buyback": 10,
    "ipo": 10,
    "ma": 10,
    "stake_exit": 10,
    "strategic_investment": 8,
    "product_platform_launch": 10,
    "platform_roadmap": 8,
    "policy_risk": 8,
    "upgrade": 6,
    "downgrade": 6,
    "price_target": 3,
    "fomc_minutes": 10,
    "rates_update": 8,
    "oil_update": 8,
    "gold_update": 6,
    "volatility_update": 6,
    "shares_up": 4,
    "shares_down": 4,
}

GENERIC_SUBJECTS = {"", "geo", "macro", "market", "news"}
SINGLE_EVIDENCE_SENSITIVE_TYPES = {"analyst", "strategic", "mover"}
SINGLE_EVIDENCE_TRUST_REQUIRED_TYPES = {
    "geo",
    "macro",
    "earnings",
    "corporate_action",
}
LOW_SIGNAL_DISPATCH_TITLE_RE = re.compile(
    r"barack obama|latest\s*&\s*breaking|headlines today|major crash coming|"
    r"flashes warning|how to play it|top stories|news updates|"
    r"\bmerger buzz\b|\brumou?rs?\b|\bspeculations?\b",
    re.I,
)
SOFT_ANALYSIS_TITLE_RE = re.compile(
    r"\b(?:investment story|stock forecast|analyst ratings?|predictions?|"
    r"which .* better buy|better buy now|best stocks?|best etfs?|"
    r"how to play|exposure ahead of|earnings strategy|earnings snapshot|"
    r"ai hopes? and fresh concerns|story is shifting|should you buy|"
    r"stocks? to buy for \d+ years?|hidden .* business|about to overtake|"
    r"outperforming|total addressable market|\btam\b)\b",
    re.I,
)
NUMERIC_PCT_MOVE_RE = re.compile(r"(?<![A-Za-z0-9])[+-]?\d+(?:\.\d+)?\s*%")
GEO_TALKS_HALT_TITLE_RE = re.compile(
    r"\b(?:iran|tehran)\b.{0,100}\b(?:halt(?:s|ed)?|stop(?:s|ped)?|"
    r"suspend(?:s|ed)?|cut(?:s)? off|walk(?:s|ed)? from|break(?:s)? off)\b"
    r".{0,100}\b(?:talks?|negotiations?|messages?|mediators?|ceasefire)\b|"
    r"\b(?:talks?|negotiations?|messages?|mediators?|ceasefire)\b.{0,100}"
    r"\b(?:halt(?:s|ed)?|stop(?:s|ped)?|suspend(?:s|ed)?|cut(?:s)? off|"
    r"walk(?:s|ed)? from|break(?:s)? off)\b.{0,100}\b(?:iran|tehran)\b",
    re.I,
)
GEO_CONFLICT_TITLE_RE = re.compile(
    r"\b(?:u\.s\.|us|iran|irgc|centcom|kuwait)\b.{0,120}"
    r"\b(?:strike(?:s)?|struck|airbase attack|missiles? shot down|"
    r"missile(?:s)? fired|drones?\s*&\s*missiles?|mine(?:s|laying)?)\b|"
    r"\b(?:strike(?:s)?|struck|airbase attack|missiles? shot down|"
    r"missile(?:s)? fired|drones?\s*&\s*missiles?|mine(?:s|laying)?)\b"
    r".{0,120}\b(?:iran|irgc|centcom|kuwait|hormuz|lebanon)\b",
    re.I,
)
GEO_HORMUZ_BLOCK_TITLE_RE = re.compile(
    r"\b(?:iran|tehran|hormuz|strait)\b.{0,120}"
    r"\b(?:block(?:s|ed|ade)?|close(?:s|d)?|closed|reopen(?:ing)?|"
    r"maritime blockade)\b.{0,80}\b(?:hormuz|strait|shipping)?\b|"
    r"\b(?:block(?:s|ed|ade)?|close(?:s|d)?|closed|reopen(?:ing)?|"
    r"maritime blockade)\b.{0,120}\b(?:hormuz|strait|shipping|iran|tehran)\b",
    re.I,
)
GEO_RESCUE_WRAPPER_TITLE_RE = re.compile(
    r"\b(?:stock market today|s&p\s*500|dow jones|nasdaq live|markets wrap|"
    r"market wrap|tsx slides|what happens to the us economy|posts on x|"
    r"phemex|daily express|benzinga|tells cnbc)\b",
    re.I,
)
EVENT_LINKED_MOVER_SUBJECTS = {
    "aapl",
    "amd",
    "amzn",
    "asml",
    "avgo",
    "crwv",
    "dell",
    "googl",
    "hpe",
    "hpq",
    "ibm",
    "intc",
    "meta",
    "msft",
    "mu",
    "nbis",
    "nvda",
    "orcl",
    "qcom",
    "tsla",
    "tsm",
    "vrt",
}
EVENT_LINKED_CAUSE_RE = re.compile(
    r"\b(?:after|as|amid|following|on|because|boost(?:s|ed)?|pressure|"
    r"weigh(?:s|ed)?|react(?:s|ed)?|launch(?:es|ed)?|unveil(?:s|ed)?|"
    r"announce(?:s|d)?|roadmap|platform|gtc|computex|ai pc|chip(?:s)?|"
    r"gpu|cpu|nvidia|qualcomm|intel|amd|microsoft|tariff(?:s)?|"
    r"sanctions?|export controls?|oil|crude|deal|contract|partnership|"
    r"acquisition|merger|guidance|earnings|fda|approval|trial|data center)\b",
    re.I,
)
TRUSTED_DOMAIN_SUFFIXES = (
    ".gov",
    "abc.xyz",
    "apnews.com",
    "aboutamazon.com",
    "amd.com",
    "apple.com",
    "asml.com",
    "bbc.com",
    "bloomberg.com",
    "broadcom.com",
    "cnbc.com",
    "cnn.com",
    "federalreserve.gov",
    "finance.yahoo.com",
    "ft.com",
    "googleblog.com",
    "intel.com",
    "investor.nvidia.com",
    "microsoft.com",
    "nvidia.com",
    "oracle.com",
    "nytimes.com",
    "reuters.com",
    "salesforce.com",
    "scmp.com",
    "sec.gov",
    "treasury.gov",
    "tsmc.com",
    "usembassy.gov",
    "whitehouse.gov",
    "wsj.com",
)
LOW_QUALITY_DOMAIN_SUFFIXES = (
    "ad-hoc-news.de",
    "asatunews.co.id",
    "coincentral.com",
    "eciks.org",
    "finbold.com",
    "heygotrade.com",
    "indexbox.io",
    "indiavision.com",
    "marketbeat.com",
    "mitrade.com",
    "notateslaapp.com",
    "247wallst.com",
    "quiverquant.com",
    "simplywall.st",
    "stocktwits.com",
    "tipranks.com",
    "tradingkey.com",
)
TRUSTED_PUBLISHER_TITLE_RE = re.compile(
    r"\bby\s+reuters\b|\bassociated press\b|\bap news\b|"
    r"\bnikkei asia\b|\bbloomberg\b|\bcnbc\b|\bfinancial times\b|\bft\b|"
    r"\bwall street journal\b|\bwsj\b|\bmarketwatch\b",
    re.I,
)
PREFERRED_SOURCE_DOMAIN_SUFFIXES = (
    "sec.gov",
    "nvidia.com",
    "investor.nvidia.com",
    "federalreserve.gov",
    "treasury.gov",
    "reuters.com",
    "bloomberg.com",
    "cnbc.com",
    "finance.yahoo.com",
    "apnews.com",
    "wsj.com",
    "ft.com",
    "scmp.com",
    "seekingalpha.com",
    "marketwatch.com",
)
WEAK_SOURCE_DOMAIN_SUFFIXES = (
    "coincentral.com",
    "infotechlead.com",
    "marketbeat.com",
    "mashable.com",
    "tomshardware.com",
    "tradingview.com",
)
CONCRETE_EARNINGS_RE = re.compile(
    r"\b(?:eps|revenue|sales|guidance|outlook)\b.*(?:beats?|beat|tops?|raises?|"
    r"raised|boosts?|boosted|cuts?|cut|lowers?|lowered|sees)\b|"
    r"\b(?:beats?|beat|tops?|raises?|raised|boosts?|boosted|cuts?|cut|lowers?|"
    r"lowered|sees)\b.*\b(?:eps|revenue|sales|guidance|outlook)\b|"
    r"\bq[1-4]\b.*\b(?:eps|revenue|sales)\b|"
    r"\$\d+(?:\.\d+)?[BMK]?\b",
    re.I,
)
CORPORATE_MATERIAL_RE = re.compile(
    r"\b(?:layoffs?|job cuts?|cuts? \d+% of jobs|restructur(?:e|ing)|"
    r"bankruptcy|chapter 11|merger|acquisition|acquire|buyout|takeover|"
    r"buyback|repurchase)\b",
    re.I,
)
STRATEGIC_MATERIAL_RE = re.compile(
    r"\b(?:strategic investment|invest(?:s|ed|ment)?|commits?|deal|"
    r"partner(?:s|ed|ship)?|joint development|supply agreement|data center|"
    r"ai infrastructure|physical ai|robot center|robotics?|chip fab|"
    r"semiconductor fabrication)\b.*(?:\$\d+(?:\.\d+)?\s*"
    r"(?:billion|million|b|m)|\bai\b|anthropic|nvidia|openai|microsoft|"
    r"fujitsu|data center|semiconductor|chip|robot)"
    r"|(?:nvidia|microsoft|fujitsu).{0,120}\b(?:physical ai|robot center|"
    r"robotics?|joint development|partner(?:s|ed|ship)?)\b",
    re.I,
)
MARKET_LEADER_ALIASES = {
    "aapl": ("apple", "aapl"),
    "amd": ("amd", "advanced micro devices"),
    "amzn": ("amazon", "aws", "amzn"),
    "asml": ("asml",),
    "avgo": ("broadcom", "avgo"),
    "googl": ("google", "alphabet", "googl", "goog"),
    "intc": ("intel", "intc"),
    "meta": ("meta",),
    "msft": ("microsoft", "azure", "msft"),
    "nvda": ("nvidia", "jensen huang", "nvda"),
    "orcl": ("oracle", "orcl"),
    "qcom": ("qualcomm", "qcom"),
    "tsla": ("tesla", "tsla"),
    "tsm": ("tsmc", "taiwan semiconductor", "tsm"),
}
MATERIAL_PLATFORM_ACTION_RE = re.compile(
    r"\b(?:unveils?|unveiled|announces?|announced|launch(?:es|ed)?|"
    r"introduces?|introduced|debuts?|debuted|make(?:s)? debut|reinvents?|"
    r"reinvented|reveals?|"
    r"revealed|rolls out|rolled out|ships?|shipping|begins? production|"
    r"starts? production|enters? production|full production|mass production|"
    r"samples?|sampling|tape[- ]?out|roadmap)\b",
    re.I,
)
MATERIAL_PLATFORM_CONTEXT_RE = re.compile(
    r"\b(?:gtc|computex|wwdc|build|re:invent|google i/o|developer conference|"
    r"keynote|platform|roadmap|architecture|product|chip|chips|gpu|cpu|"
    r"processor|accelerator|superchip|ai pc|windows pc|pc cpu|ai factory|"
    r"ai factories|data center|cloud|server|rack[- ]scale|rtx|spark|vera|"
    r"rubin|blackwell|cuda|dsx|instinct|trainium|inferentia|tpu|hbm|"
    r"robotics?|physical ai)\b",
    re.I,
)
MATERIAL_PLATFORM_EXCLUDE_RE = re.compile(
    r"\b(?:how to watch|where to watch|watch live|what to expect|preview|"
    r"set to reveal|set to unveil|reportedly set to|rumou?rs?|speculation|"
    r"stock forecast|better buy|should you buy|price target)\b",
    re.I,
)

HARD_EVENT_REASONS = {
    "recall_earnings_guidance",
    "recall_concrete_earnings",
    "recall_trusted_earnings",
    "recall_corporate_transaction",
    "recall_material_corporate_action",
    "recall_material_strategic",
    "rescue_event_linked_mover",
    "rescue_geo_fresh_delta",
}
WATCH_EVENT_REASONS = {
    "recall_concrete_analyst_target",
}
WATCH_ANALYST_SUBJECTS = {
    "aapl",
    "amd",
    "avgo",
    "crwv",
    "googl",
    "meta",
    "msft",
    "nbis",
    "nvda",
    "tsla",
}
CONCRETE_ANALYST_TARGET_RE = re.compile(
    r"\b(?:price target|pt)\b.{0,80}?\$\d+(?:\.\d+)?|"
    r"\$\d+(?:\.\d+)?.{0,80}?\b(?:price target|pt)\b|"
    r"\b(?:raises?|raised|boosts?|boosted|hikes?|hiked|lifts?|lifted|"
    r"cuts?|cut|trims?|trimmed|lowers?|lowered|reduces?|reduced|"
    r"initiates?|initiated|resumes?|resumed)\b"
    r".{0,120}?\b(?:price target|pt|buy|neutral|outperform|overweight|"
    r"underweight|sell|hold)\b",
    re.I,
)


@dataclass(frozen=True)
class SourceReputation:
    domains: tuple[str, ...]
    trusted_domains: tuple[str, ...]
    low_quality_domains: tuple[str, ...]
    trusted_publisher_titles: tuple[str, ...]

    @property
    def trusted_count(self) -> int:
        return len(self.trusted_domains) + len(self.trusted_publisher_titles)

    @property
    def low_quality_count(self) -> int:
        return len(self.low_quality_domains)

    @property
    def has_trusted_source(self) -> bool:
        return self.trusted_count > 0

    @property
    def has_low_quality_source(self) -> bool:
        return self.low_quality_count > 0


@dataclass(frozen=True)
class RescueAssessment:
    rescue_type: str = ""
    reason: str = ""
    grade: str = GRADE_B
    event_quality: str = "hard_event"
    atomic_digest: bool = False
    requires_numeric_fact: bool = False
    risk_flags: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.rescue_type)

    def payload(self) -> dict[str, Any]:
        if not self.enabled:
            return {}
        return {
            "rescue_type": self.rescue_type,
            "rescue_reason": self.reason,
            "atomic_digest": self.atomic_digest,
            "requires_numeric_fact": self.requires_numeric_fact,
        }


@dataclass(frozen=True)
class DispatchDecision:
    event_signature: str
    decision: str
    score: float
    reason: str
    policy: str
    payload: dict[str, Any]

    def decision_id(self, run_id: str) -> str:
        raw = f"{run_id}|{self.event_signature}|{self.policy}"
        return sha256(raw.encode("utf-8")).hexdigest()

    def as_record(self, run_id: str) -> dict[str, Any]:
        return {
            "id": self.decision_id(run_id),
            "run_id": run_id,
            "event_signature": self.event_signature,
            "decision": self.decision,
            "reason": self.reason,
            "policy": self.policy,
            "score": self.score,
            "payload": self.payload,
        }


def _candidate_index(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(candidate["id"]): candidate for candidate in candidates}


def _group_extracted_events(
    extracted: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in extracted:
        event = item.get("event", {})
        signature = str(event.get("signature") or "")
        if not signature:
            continue
        grouped.setdefault(signature, []).append(item)
    return grouped


def _domain_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host.split("@")[-1].split(":")[0]


def _domain_matches(domain: str, suffixes: tuple[str, ...]) -> bool:
    for suffix in suffixes:
        if suffix.startswith("."):
            if domain.endswith(suffix):
                return True
        elif domain == suffix or domain.endswith(f".{suffix}"):
            return True
    return False


def _source_reputation(candidate_rows: list[dict[str, Any]]) -> SourceReputation:
    domains = sorted(
        {
            domain
            for candidate in candidate_rows
            for domain in (_domain_from_url(str(candidate.get("url") or "")),)
            if domain
        }
    )
    trusted_domains = tuple(
        domain for domain in domains if _domain_matches(domain, TRUSTED_DOMAIN_SUFFIXES)
    )
    low_quality_domains = tuple(
        domain
        for domain in domains
        if _domain_matches(domain, LOW_QUALITY_DOMAIN_SUFFIXES)
    )
    trusted_publisher_titles = tuple(
        sorted(
            {
                str(candidate.get("title") or "")
                for candidate in candidate_rows
                if TRUSTED_PUBLISHER_TITLE_RE.search(
                    str(candidate.get("title") or "")
                )
            }
        )
    )
    return SourceReputation(
        domains=tuple(domains),
        trusted_domains=trusted_domains,
        low_quality_domains=low_quality_domains,
        trusted_publisher_titles=trusted_publisher_titles,
    )


def _source_quality_rank(candidate: dict[str, Any] | None) -> tuple[int, str, str]:
    if not candidate:
        return (9, "", "")
    domain = _domain_from_url(str(candidate.get("url") or ""))
    title = str(candidate.get("title") or "")
    if _domain_matches(domain, ("sec.gov", ".gov", "federalreserve.gov", "treasury.gov")):
        rank = 0
    elif _domain_matches(domain, PREFERRED_SOURCE_DOMAIN_SUFFIXES):
        rank = 1
    elif TRUSTED_PUBLISHER_TITLE_RE.search(title):
        rank = 1
    elif _domain_matches(domain, TRUSTED_DOMAIN_SUFFIXES):
        rank = 2
    elif _domain_matches(domain, LOW_QUALITY_DOMAIN_SUFFIXES + WEAK_SOURCE_DOMAIN_SUFFIXES):
        rank = 5
    else:
        rank = 3
    return (rank, domain, title.lower())


def _score_event(
    *,
    event: dict[str, Any],
    evidence_count: int,
    providers: set[str],
    sources: set[str],
    max_confidence: float,
    source_reputation: SourceReputation,
) -> tuple[float, list[str]]:
    payload = event.get("payload", {})
    event_type = str(event.get("event_type") or payload.get("event_type") or "")
    action = str(payload.get("action") or "")

    score = float(BASE_SCORE_BY_TYPE.get(event_type, 30))
    reasons = [f"type:{event_type}:{int(score)}"]

    action_bonus = ACTION_BONUS.get(action, 0)
    if action_bonus:
        score += action_bonus
        reasons.append(f"action:{action}:{action_bonus}")

    if evidence_count > 1:
        evidence_bonus = min(15, (evidence_count - 1) * 5)
        score += evidence_bonus
        reasons.append(f"evidence_count:{evidence_count}:{evidence_bonus}")

    if len(providers) > 1:
        provider_bonus = min(10, (len(providers) - 1) * 5)
        score += provider_bonus
        reasons.append(f"provider_diversity:{len(providers)}:{provider_bonus}")

    if len(sources) > 1:
        source_bonus = min(6, (len(sources) - 1) * 3)
        score += source_bonus
        reasons.append(f"source_diversity:{len(sources)}:{source_bonus}")

    if "official_rss" in providers:
        score += 10
        reasons.append("official_source:10")

    if source_reputation.trusted_count:
        source_bonus = min(10, source_reputation.trusted_count * 5)
        score += source_bonus
        reasons.append(f"trusted_source:{source_reputation.trusted_count}:{source_bonus}")

    if source_reputation.low_quality_count:
        source_penalty = min(25, source_reputation.low_quality_count * 15)
        score -= source_penalty
        reasons.append(
            f"low_quality_source:{source_reputation.low_quality_count}:-{source_penalty}"
        )

    if max_confidence >= 0.85:
        score += 8
        reasons.append("confidence:high:8")
    elif max_confidence >= 0.8:
        score += 4
        reasons.append("confidence:medium:4")

    return min(score, 100.0), reasons


def _combined_title_text(event: dict[str, Any], candidate_rows: list[dict[str, Any]]) -> str:
    chunks = [str(event.get("title") or "")]
    for candidate in candidate_rows:
        chunks.append(str(candidate.get("title") or ""))
    return " ".join(chunk for chunk in chunks if chunk)


def _single_source_risk_flags(
    *,
    evidence_count: int,
    source_reputation: SourceReputation,
) -> tuple[str, ...]:
    flags: list[str] = []
    if source_reputation.has_low_quality_source:
        flags.append("low_quality_source")
    if evidence_count == 1:
        flags.append("single_source")
        if not source_reputation.has_trusted_source:
            flags.append("single_source_untrusted")
    return tuple(flags)


def _geo_fresh_delta_rescue(
    *,
    event_type: str,
    text: str,
    score: float,
    max_confidence: float,
    evidence_count: int,
    source_reputation: SourceReputation,
) -> RescueAssessment:
    if event_type != "geo":
        return RescueAssessment()
    if source_reputation.has_low_quality_source and not source_reputation.has_trusted_source:
        return RescueAssessment()
    if max_confidence < 0.8 or score < 60:
        return RescueAssessment()
    has_trusted_domain = bool(source_reputation.trusted_domains)
    if GEO_RESCUE_WRAPPER_TITLE_RE.search(text) and not has_trusted_domain:
        return RescueAssessment()
    if not (
        GEO_TALKS_HALT_TITLE_RE.search(text)
        or GEO_CONFLICT_TITLE_RE.search(text)
        or GEO_HORMUZ_BLOCK_TITLE_RE.search(text)
    ):
        return RescueAssessment()
    return RescueAssessment(
        rescue_type="geo_fresh_delta",
        reason="rescue_geo_fresh_delta",
        grade=GRADE_A if has_trusted_domain and score >= 82 else GRADE_B,
        atomic_digest=True,
        risk_flags=_single_source_risk_flags(
            evidence_count=evidence_count,
            source_reputation=source_reputation,
        ),
    )


def _event_linked_mover_rescue(
    *,
    event_type: str,
    subject: str,
    action: str,
    text: str,
    max_confidence: float,
    evidence_count: int,
    source_reputation: SourceReputation,
) -> RescueAssessment:
    if event_type != "mover":
        return RescueAssessment()
    if action not in {"shares_up", "shares_down"}:
        return RescueAssessment()
    if subject.lower() not in EVENT_LINKED_MOVER_SUBJECTS:
        return RescueAssessment()
    if source_reputation.has_low_quality_source and not source_reputation.has_trusted_source:
        return RescueAssessment()
    if max_confidence < 0.8:
        return RescueAssessment()
    if not NUMERIC_PCT_MOVE_RE.search(text):
        return RescueAssessment()
    if not EVENT_LINKED_CAUSE_RE.search(text):
        return RescueAssessment()
    return RescueAssessment(
        rescue_type="event_linked_mover",
        reason="rescue_event_linked_mover",
        grade=GRADE_B,
        atomic_digest=True,
        requires_numeric_fact=True,
        risk_flags=_single_source_risk_flags(
            evidence_count=evidence_count,
            source_reputation=source_reputation,
        ),
    )


def _rescue_assessment(
    *,
    event: dict[str, Any],
    candidate_rows: list[dict[str, Any]],
    score: float,
    evidence_count: int,
    max_confidence: float,
    source_reputation: SourceReputation,
) -> RescueAssessment:
    payload = event.get("payload", {})
    event_type = str(event.get("event_type") or payload.get("event_type") or "")
    subject = str(event.get("subject") or payload.get("subject") or "")
    action = str(payload.get("action") or "")
    title_text = _combined_title_text(event, candidate_rows)
    geo_rescue = _geo_fresh_delta_rescue(
        event_type=event_type,
        text=title_text,
        score=score,
        max_confidence=max_confidence,
        evidence_count=evidence_count,
        source_reputation=source_reputation,
    )
    if geo_rescue.enabled:
        return geo_rescue
    return _event_linked_mover_rescue(
        event_type=event_type,
        subject=subject,
        action=action,
        text=title_text,
        max_confidence=max_confidence,
        evidence_count=evidence_count,
        source_reputation=source_reputation,
    )


def _rescue_is_blocked_by_reason(decision_reason: str) -> bool:
    return decision_reason in {
        "reject:generic_subject",
        "reject:low_signal_title",
        "reject:soft_analysis",
    }


def _choose_decision(
    *,
    event: dict[str, Any],
    candidate_rows: list[dict[str, Any]],
    score: float,
    evidence_count: int,
    max_confidence: float,
    source_reputation: SourceReputation,
) -> tuple[str, str, str, list[str]]:
    payload = event.get("payload", {})
    event_type = str(event.get("event_type") or payload.get("event_type") or "")
    subject = str(event.get("subject") or payload.get("subject") or "")
    title = str(event.get("title") or "")
    action = str(payload.get("action") or "")
    risk_flags: list[str] = []

    if subject in GENERIC_SUBJECTS:
        return REJECT, GRADE_C, "reject:generic_subject", ["generic_subject"]

    if LOW_SIGNAL_DISPATCH_TITLE_RE.search(title):
        return REJECT, GRADE_C, "reject:low_signal_title", ["low_signal_title"]

    if _soft_analysis_reason(event_type=event_type, title=title):
        return REJECT, GRADE_C, "reject:soft_analysis", ["soft_analysis"]

    recall_reason = _recall_first_non_geo_reason(
        event_type=event_type,
        subject=subject,
        action=action,
        title=title,
        candidate_rows=candidate_rows,
        score=score,
        max_confidence=max_confidence,
        source_reputation=source_reputation,
    )
    if recall_reason:
        if source_reputation.has_low_quality_source:
            risk_flags.append("low_quality_source")
        if evidence_count == 1:
            risk_flags.append("single_source")
        if (
            evidence_count == 1
            and not source_reputation.has_trusted_source
            and not source_reputation.has_low_quality_source
        ):
            risk_flags.append("single_source_untrusted")
        grade = GRADE_A if score >= 85 and source_reputation.has_trusted_source else GRADE_B
        return SEND_CANDIDATE, grade, f"send_candidate:{recall_reason}", risk_flags

    if (
        event_type == "earnings"
        and evidence_count == 1
        and not source_reputation.has_trusted_source
    ):
        if source_reputation.has_low_quality_source:
            return (
                REVIEW,
                GRADE_C,
                "review:single_low_quality_earnings_source",
                ["single_source", "low_quality_source"],
            )
        return (
            REVIEW,
            GRADE_C,
            "review:single_untrusted_earnings_source",
            ["single_source_untrusted"],
        )

    if max_confidence < 0.8 and evidence_count == 1:
        return REVIEW, GRADE_C, "review:single_low_confidence", ["single_low_confidence"]

    if source_reputation.has_low_quality_source and evidence_count == 1:
        if score >= 55:
            return REVIEW, GRADE_C, "review:single_low_quality_source", ["single_low_quality_source"]
        return REJECT, GRADE_C, "reject:single_low_quality_source", ["single_low_quality_source"]

    if (
        event_type in SINGLE_EVIDENCE_TRUST_REQUIRED_TYPES
        and evidence_count == 1
        and not source_reputation.has_trusted_source
        and score >= 75
    ):
        return REVIEW, GRADE_C, "review:single_source_untrusted", ["single_source_untrusted"]

    if event_type in SINGLE_EVIDENCE_SENSITIVE_TYPES and evidence_count == 1:
        if score >= 78:
            return REVIEW, GRADE_C, "review:sensitive_single_source", ["sensitive_single_source"]
        return REJECT, GRADE_C, "reject:sensitive_single_source", ["sensitive_single_source"]

    if score >= 75:
        grade = GRADE_A if score >= 90 else GRADE_B
        return SEND_CANDIDATE, grade, "send_candidate:score", []
    if score >= 55:
        return REVIEW, GRADE_C, "review:score", []
    return REJECT, GRADE_C, "reject:score", []


def _alias_regex(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias)
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.I)


def _nearest_market_leader_before_action(title: str, action_start: int) -> str:
    prefix = title[:action_start]
    nearest_subject = ""
    nearest_start = -1
    for subject, aliases in MARKET_LEADER_ALIASES.items():
        for alias in aliases:
            for match in _alias_regex(alias).finditer(prefix):
                if match.start() > nearest_start:
                    nearest_subject = subject
                    nearest_start = match.start()
    return nearest_subject


def _subject_alias_starts_title(subject: str, title: str) -> bool:
    for alias in MARKET_LEADER_ALIASES.get(subject, ()):
        match = _alias_regex(alias).search(title)
        if match is not None and match.start() <= 5:
            return True
    return False


def _material_platform_title_matches_subject(subject: str, title: str) -> bool:
    subject_key = subject.lower().strip()
    if subject_key not in MARKET_LEADER_ALIASES:
        return False
    if MATERIAL_PLATFORM_EXCLUDE_RE.search(title):
        return False
    action_match = MATERIAL_PLATFORM_ACTION_RE.search(title)
    if action_match is None:
        return False
    if MATERIAL_PLATFORM_CONTEXT_RE.search(title) is None:
        return False
    if _subject_alias_starts_title(subject_key, title):
        return True
    return _nearest_market_leader_before_action(title, action_match.start()) == subject_key


def _is_material_platform_event(
    *,
    subject: str,
    title: str,
    candidate_rows: list[dict[str, Any]],
    score: float,
    max_confidence: float,
    source_reputation: SourceReputation,
) -> bool:
    if max_confidence < 0.8:
        return False
    if not source_reputation.has_trusted_source and score < 58:
        return False
    titles = [title]
    titles.extend(str(candidate.get("title") or "") for candidate in candidate_rows)
    return any(
        _material_platform_title_matches_subject(subject, item)
        for item in titles
        if item
    )


def _recall_first_non_geo_reason(
    *,
    event_type: str,
    subject: str,
    action: str,
    title: str,
    candidate_rows: list[dict[str, Any]],
    score: float,
    max_confidence: float,
    source_reputation: SourceReputation,
) -> str:
    if source_reputation.has_low_quality_source and not source_reputation.has_trusted_source:
        return ""
    if event_type == "earnings":
        if action in {"guidance_raise", "guidance_cut", "guidance_update"}:
            return "recall_earnings_guidance"
        if action == "earnings_report" and CONCRETE_EARNINGS_RE.search(title):
            return "recall_concrete_earnings"
        if score >= 63 and source_reputation.has_trusted_source:
            return "recall_trusted_earnings"
    if event_type == "corporate_action":
        if action in {"ma", "buyback", "ipo"}:
            return "recall_corporate_transaction"
        if CORPORATE_MATERIAL_RE.search(title) and max_confidence >= 0.8:
            return "recall_material_corporate_action"
    if event_type == "strategic":
        if _is_material_platform_event(
            subject=subject,
            title=title,
            candidate_rows=candidate_rows,
            score=score,
            max_confidence=max_confidence,
            source_reputation=source_reputation,
        ):
            return "recall_material_strategic"
        if action in {
            "investment",
            "strategic_investment",
            "product_platform_launch",
            "platform_roadmap",
            "partnership",
            "supply_deal",
            "policy_risk",
        }:
            if STRATEGIC_MATERIAL_RE.search(title) or source_reputation.has_trusted_source:
                return "recall_material_strategic"
    if event_type == "analyst":
        if (
            action in {"price_target", "upgrade", "downgrade", "analyst_action"}
            and subject.lower() in WATCH_ANALYST_SUBJECTS
            and CONCRETE_ANALYST_TARGET_RE.search(title)
        ):
            return "recall_concrete_analyst_target"
    return ""


def _soft_analysis_reason(*, event_type: str, title: str) -> str:
    if event_type not in {"analyst", "strategic", "mover"}:
        return ""
    if SOFT_ANALYSIS_TITLE_RE.search(title):
        return "soft_analysis_title"
    return ""


def _event_quality_from_reason(reason: str) -> str:
    if reason in HARD_EVENT_REASONS:
        return "hard_event"
    if reason in WATCH_EVENT_REASONS:
        return "watch_item"
    if reason == "soft_analysis":
        return "soft_analysis"
    return ""


def decide_dispatch(
    extracted: list[dict[str, Any]],
    *,
    candidates: list[dict[str, Any]],
    policy: str = POLICY_NAME,
) -> list[DispatchDecision]:
    candidate_by_id = _candidate_index(candidates)
    decisions: list[DispatchDecision] = []

    for signature, group in sorted(_group_extracted_events(extracted).items()):
        ranked_group = sorted(
            group,
            key=lambda item: _source_quality_rank(
                candidate_by_id.get(str(item.get("candidate_id") or ""))
            ),
        )
        first = ranked_group[0]
        event = first["event"]
        candidate_ids = [str(item["candidate_id"]) for item in group]
        ranked_candidate_ids = [str(item["candidate_id"]) for item in ranked_group]
        candidate_rows = [
            candidate_by_id[candidate_id]
            for candidate_id in candidate_ids
            if candidate_id in candidate_by_id
        ]
        providers = {
            str(candidate.get("provider") or "")
            for candidate in candidate_rows
            if str(candidate.get("provider") or "")
        }
        sources = {
            str(candidate.get("source") or "")
            for candidate in candidate_rows
            if str(candidate.get("source") or "")
        }
        source_reputation = _source_reputation(candidate_rows)
        max_confidence = max(float(item.get("confidence") or 0.0) for item in group)
        score, score_reasons = _score_event(
            event=event,
            evidence_count=len(group),
            providers=providers,
            sources=sources,
            max_confidence=max_confidence,
            source_reputation=source_reputation,
        )
        decision, grade, decision_reason, risk_flags = _choose_decision(
            event=event,
            candidate_rows=candidate_rows,
            score=score,
            evidence_count=len(group),
            max_confidence=max_confidence,
            source_reputation=source_reputation,
        )
        rescue = RescueAssessment()
        if decision != SEND_CANDIDATE and not _rescue_is_blocked_by_reason(decision_reason):
            rescue = _rescue_assessment(
                event=event,
                candidate_rows=candidate_rows,
                score=score,
                evidence_count=len(group),
                max_confidence=max_confidence,
                source_reputation=source_reputation,
            )
            if rescue.enabled:
                decision = SEND_CANDIDATE
                grade = rescue.grade
                decision_reason = f"send_candidate:{rescue.reason}"
                risk_flags = sorted(set(risk_flags).union(rescue.risk_flags))
        reason_key = decision_reason.split(":", 1)[-1]
        event_quality = rescue.event_quality if rescue.enabled else _event_quality_from_reason(reason_key)
        reason = ";".join([decision_reason] + score_reasons)
        payload = {
            "policy": policy,
            "score": score,
            "event": {
                "signature": signature,
                "event_type": event["event_type"],
                "subject": event["subject"],
                "effective_date": event["effective_date"],
                "action": event.get("payload", {}).get("action", ""),
                "object": event.get("payload", {}).get("object", ""),
                "title": event.get("title", ""),
                "url": event.get("url", ""),
                "metadata": event.get("metadata", {}),
            },
            "evidence_count": len(group),
            "candidate_ids": candidate_ids,
            "ranked_candidate_ids": ranked_candidate_ids,
            "providers": sorted(providers),
            "sources": sorted(sources),
            "domains": list(source_reputation.domains),
            "trusted_domains": list(source_reputation.trusted_domains),
            "low_quality_domains": list(source_reputation.low_quality_domains),
            "trusted_source_count": source_reputation.trusted_count,
            "low_quality_source_count": source_reputation.low_quality_count,
            "source_tier": "trusted"
            if source_reputation.has_trusted_source
            else "low_quality"
            if source_reputation.has_low_quality_source
            else "untrusted",
            "max_confidence": max_confidence,
            "grade": grade,
            "risk_flags": risk_flags,
            "send_worthy_reason": decision_reason,
            "event_quality": event_quality,
            "hard_event_reason": reason_key if event_quality == "hard_event" else "",
            "soft_analysis_reason": reason_key if event_quality == "soft_analysis" else "",
            "extractor_reasons": sorted({str(item.get("reason") or "") for item in group}),
            "score_reasons": score_reasons,
        }
        payload.update(rescue.payload())
        decisions.append(
            DispatchDecision(
                event_signature=signature,
                decision=decision,
                score=score,
                reason=reason,
                policy=policy,
                payload=payload,
            )
        )

    return decisions
