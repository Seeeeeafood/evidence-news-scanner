from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from .earnings_facts import augment_earnings_summary_with_contract
from .market_snapshot import render_market_snapshot_lines
from .reports import ReportError, load_decision_rows


DEFAULT_MESSAGE_DECISIONS = {"send_candidate"}
DIGEST_TITLE_MAX_CHARS = 150
MIN_BODY_BASIS_CHARS = 300
MIN_SNIPPET_BASIS_CHARS = 120
MAX_EVIDENCE_TEXT_CHARS = 1400

EVENT_LABELS_KO = {
    "analyst": "애널리스트",
    "corporate_action": "기업 이벤트",
    "earnings": "실적",
    "geo": "지정학",
    "macro": "매크로",
    "mover": "주가 변동",
    "strategic": "전략",
    "theme": "테마",
}

COMPANY_EVENT_TYPES = {
    "analyst",
    "corporate_action",
    "earnings",
    "mover",
    "strategic",
}

ACTION_LABELS_KO = {
    "analyst_action": "애널리스트 액션",
    "buyback": "자사주 매입",
    "conflict": "분쟁",
    "corporate_transaction": "기업 거래",
    "diplomacy": "외교",
    "downgrade": "투자의견 하향",
    "earnings_related": "실적 관련",
    "earnings_report": "실적 발표",
    "earnings_result": "실적 결과",
    "fomc_minutes": "FOMC 의사록",
    "gold_update": "금 가격 변화",
    "guidance_cut": "가이던스 하향",
    "guidance_raise": "가이던스 상향",
    "guidance_update": "가이던스 변경",
    "investment": "투자",
    "ipo": "IPO",
    "ma": "M&A",
    "macro_update": "매크로 변화",
    "mover": "주가 변동",
    "oil_update": "유가 변화",
    "partnership": "파트너십",
    "policy_support": "정책 지원",
    "policy_risk": "정책 리스크",
    "price_target": "목표가 변경",
    "policy_geo": "정책/지정학",
    "rates_update": "금리 변화",
    "sanctions": "제재",
    "sector_pressure": "섹터 압박",
    "sector_rally": "섹터 랠리",
    "semiconductor_pressure": "반도체 압박",
    "shares_down": "주가 하락",
    "shares_up": "주가 상승",
    "strategic_investment": "전략 투자",
    "strategic_update": "전략 업데이트",
    "ai_infrastructure_jv": "AI 인프라 합작",
    "stake_exit": "지분 매각",
    "supply_deal": "공급 계약",
    "tariff_policy": "관세/정책",
    "upgrade": "투자의견 상향",
    "volatility_update": "변동성 확대",
}

SUBJECT_LABELS_KO = {
    "CHINA": "중국",
    "AI_INFRA": "AI 인프라",
    "CPI": "CPI",
    "DXY": "달러지수",
    "FOMC": "FOMC",
    "GOLD": "금",
    "IRAN": "이란",
    "JOBS": "고용",
    "MACRO": "매크로",
    "MIDDLE_EAST": "중동",
    "NORTH_KOREA": "북한",
    "OIL": "원유",
    "PCE": "PCE",
    "RATES": "금리",
    "RED_SEA": "홍해",
    "RUSSIA": "러시아/우크라이나",
    "SAUDI": "사우디",
    "SANCTIONS": "제재",
    "SEMIS": "반도체",
    "SEMICONDUCTORS": "반도체",
    "MEMORY_SEMICONDUCTORS": "메모리 반도체",
    "SOUTH_CHINA_SEA": "남중국해",
    "SPACEX": "SpaceX",
    "TAIWAN": "대만",
    "TARIFFS": "관세",
    "TRUMP_XI": "트럼프-시진핑",
    "USD_KRW": "달러/원",
    "VENEZUELA": "베네수엘라",
    "VIX": "VIX",
    "WHITE_HOUSE": "백악관",
}

POSITIVE_ACTIONS = {
    "buyback",
    "guidance_raise",
    "policy_support",
    "sector_rally",
    "shares_up",
    "strategic_investment",
    "upgrade",
}

NEGATIVE_ACTIONS = {
    "conflict",
    "downgrade",
    "guidance_cut",
    "policy_risk",
    "sanctions",
    "shares_down",
    "stake_exit",
    "tariff_policy",
    "volatility_update",
}

SOURCE_HINTS = (
    (re.compile(r"\breuters\b", re.I), "Reuters"),
    (re.compile(r"\bassociated press\b|\bap news\b", re.I), "AP"),
    (re.compile(r"\bbloomberg\b", re.I), "Bloomberg"),
    (re.compile(r"\bcnbc\b", re.I), "CNBC"),
    (re.compile(r"\bcbsnews\.com\b|\bcbs news\b|\bcbsnews\b", re.I), "CBS"),
    (re.compile(r"\bfinancial times\b|\bft\b", re.I), "FT"),
    (re.compile(r"\bwall street journal\b|\bwsj\b", re.I), "WSJ"),
    (re.compile(r"\bnew york times\b|\bnytimes\b", re.I), "NYT"),
    (re.compile(r"\bnikkei asia\b|\bnikkei\b", re.I), "Nikkei Asia"),
    (re.compile(r"\byahoo finance\b|\byahoo\b", re.I), "Yahoo"),
    (re.compile(r"\bseeking alpha\b", re.I), "Seeking Alpha"),
    (re.compile(r"\bmarketwatch\b", re.I), "MarketWatch"),
    (re.compile(r"\binvestopedia\b", re.I), "Investopedia"),
    (re.compile(r"\bthe street\b|\bthestreet\b", re.I), "TheStreet"),
    (re.compile(r"\bbenzinga\b", re.I), "Benzinga"),
    (re.compile(r"\bsouth china morning post\b|\bscmp\b", re.I), "SCMP"),
    (re.compile(r"\bu\.s\. department of the treasury\b|\btreasury\b", re.I), "Treasury"),
    (re.compile(r"\bfederal reserve\b", re.I), "Federal Reserve"),
)

GENERIC_SOURCE_HINTS = {
    "archive",
    "brave",
    "breaking hint",
    "breaking_hint",
    "fixture",
    "google",
    "google rss",
    "google_rss",
    "news",
    "provider",
    "rss",
    "source",
    "web",
}

OIL_TEXT_PATTERN = re.compile(r"\b(oil|crude|brent|wti)\b|유가|원유", re.I)
UP_MOVE_PATTERN = re.compile(
    r"\b(rise|rises|rising|rose|jump|jumps|jumped|surge|surges|surged|"
    r"rebound|rebounds|rebounded|gain|gains|gained|climb|climbs|climbed|"
    r"up|higher)\b|상승|급등|반등|오름",
    re.I,
)
DOWN_MOVE_PATTERN = re.compile(
    r"\b(fall|falls|fell|fallen|drop|drops|dropped|slide|slides|slid|"
    r"slip|slips|slipped|ease|eases|eased|plunge|plunges|plunged|"
    r"down|lower)\b|하락|급락|내림|약세",
    re.I,
)

TITLE_SUFFIX_RE = re.compile(
    r"\s+(?:By\s+[A-Z][A-Za-z .&]+|"
    r"\|\s*(?:Reuters|AP News|Bloomberg|CNBC|Financial Times|FT|"
    r"Wall Street Journal|WSJ|The New York Times|NYT|Investing\.com)|"
    r"-\s+(?:Reuters|AP News|Bloomberg|CNBC|Financial Times|FT|"
    r"Wall Street Journal|WSJ|The New York Times|NYT|Investing\.com))\s*$",
    re.I,
)

SPECIFIC_TITLE_SUMMARIES = (
    (
        re.compile(r"world markets feel .*us[-–]iran war", re.I),
        "미-이란 전쟁 장기화 — 글로벌 시장 부담 확대",
    ),
    (
        re.compile(r"russia.*energy exports.*iran war", re.I),
        "러시아 에너지 수출 확대 — 이란전 충격을 일부 흡수",
    ),
    (
        re.compile(r"xi cautions trump over taiwan.*summit", re.I),
        "트럼프-시진핑 회담 — 시진핑, 대만 오판 시 충돌 경고",
    ),
    (
        re.compile(r"iran war.*inflation.*trump.*meets xi", re.I),
        "이란전·인플레 부담 — 미중 정상회담에서 트럼프 협상력 약화",
    ),
    (
        re.compile(r"spies, sanctions, cyberattacks.*china.*u\.s\.", re.I),
        "미중 갈등 심화 — 정보전·제재·사이버 공격이 정상회담 이면에서 지속",
    ),
    (
        re.compile(r"day one of trump.*china visit.*xi.*taiwan.*trade", re.I),
        "트럼프 방중 첫날 — 시진핑 대만 경고, 무역·이란전 논의",
    ),
    (
        re.compile(r"unblock hormuz.*rubio.*china.*iran", re.I),
        "루비오, 중국에 호르무즈 압박 요구 — 이란 견제 불응 시 수출 차질 경고",
    ),
    (
        re.compile(r"china.*xi offered to help broker peace with iran", re.I),
        "시진핑, 이란 평화 중재 제안 — 미중 회담의 지정학 의제로 부상",
    ),
    (
        re.compile(r"treasury warns.*sanctions.*china.*teapot", re.I),
        "미 재무부, 중국 독립 정유사 제재 리스크 경고",
    ),
    (
        re.compile(r"sanctions.*irgc oil", re.I),
        "IRGC 원유 네트워크 대상 신규 제재 발표",
    ),
    (
        re.compile(r"trump in china.*xi.*iran war.*trade.*taiwan", re.I),
        "트럼프-시진핑 회담 — 이란전·무역·대만 무기판매가 핵심 의제",
    ),
    (
        re.compile(r"asia markets.*hot us inflation.*iran ceasefire", re.I),
        "아시아 증시 약세 — 미국 PPI와 이란 휴전 불안이 동시 부담",
    ),
    (
        re.compile(r"stocks open mixed.*producer inflation.*china trip", re.I),
        "미 증시 혼조 — 생산자물가와 트럼프 방중 이슈 주시",
    ),
    (
        re.compile(r"rubio.*china.*iran.*hormuz|hormuz.*rubio.*china.*iran", re.I),
        "루비오, 중국에 호르무즈 압박 요구 — 이란 견제 불응 시 수출 차질 경고",
    ),
    (
        re.compile(
            r"crude oil drops?.*us.*iran deal.*reopen strait|"
            r"us inches toward iran deal.*reopen strait.*crude oil drops?",
            re.I,
        ),
        "미국-이란 딜 기대에 원유 하락 — 호르무즈 재개 가능성 반영",
    ),
    (
        re.compile(
            r"(?:abraham accords?.{0,180}(?:iran|tehran).{0,180}"
            r"(?:deal|agreement|peace|ceasefire|condition|require|demand))|"
            r"(?:(?:iran|tehran).{0,180}(?:deal|agreement|peace|ceasefire|condition|require|demand)"
            r".{0,180}abraham accords?)",
            re.I,
        ),
        "트럼프, 이란딜 조건에 아브라함 협정 서명 요구 — 중동 6개국 참여 조건으로 협상 불확실성 확대",
    ),
    (
        re.compile(r"xi.*warn(?:ed|s)?.*taiwan|taiwan.*xi.*warn", re.I),
        "트럼프-시진핑 회담 — 시진핑, 대만 오판 시 충돌 경고",
    ),
    (
        re.compile(r"russia.*cushion(?:ing)?.*commodity markets.*iran", re.I),
        "러시아 에너지 수출 확대 — 이란전 충격을 일부 흡수",
    ),
    (
        re.compile(r"trump[- ]xi.*summit.*leverage.*iran.*trade.*taiwan", re.I),
        "트럼프-시진핑 회담 — 이란전·무역·대만 현안에서 협상력 시험",
    ),
    (
        re.compile(
            r"iran.*global pressure point.*energy markets.*sanctions.*"
            r"(?:maritime security|china)",
            re.I,
        ),
        "미중 정상회담, 이란전·에너지·제재 리스크가 핵심 변수로 부상",
    ),
    (
        re.compile(
            r"trump.*xi.*(?:play up|emphasiz(?:e|ed|es)).*stability.*"
            r"(?:without resolving|differences remain|tensions)",
            re.I,
        ),
        "트럼프-시진핑 회담 — 안정 메시지에도 무역·대만·이란 이견은 미해결",
    ),
    (
        re.compile(
            r"trump.*xi.*(?:declare success|stability).*"
            r"(?:differences remain|iran.*taiwan)",
            re.I,
        ),
        "트럼프-시진핑 회담 — 성과를 강조했지만 이란·대만 이견은 지속",
    ),
    (
        re.compile(
            r"uber.*higher bid.*delivery hero.*(?:€|eur)?\s*11\.5\s*bn.*offer rebuffed",
            re.I,
        ),
        "Uber, Delivery Hero 인수 제안 상향 검토 — EUR11.5B 제안 거절 후 추가 bid 논의",
    ),
    (
        re.compile(
            r"delivery hero.*uber.*buyout offer.*stake increase.*19\.5%",
            re.I,
        ),
        "Uber, Delivery Hero 인수 제안 확인 — 지분 19.5% 확대 후 buyout offer 부각",
    ),
)

RAW_ENGLISH_WORD_RE = re.compile(r"\b[A-Za-z]{4,}\b")
AMOUNT_TOKEN_RE = re.compile(
    r"\$\s*\d+(?:\.\d+)?\s*(?:billion|million|thousand|B|M|K)?",
    re.I,
)
REVENUE_TERM_RE = re.compile(r"\b(?:revenue|sales|quarterly revenue)\b", re.I)
EPS_TERM_RE = re.compile(r"\b(?:eps|earnings per share)\b", re.I)
GUIDANCE_TERM_RE = re.compile(r"\b(?:guidance|outlook|forecast|sees)\b", re.I)
BUYBACK_TERM_RE = re.compile(r"\b(?:buyback|repurchase|authorization)\b", re.I)

ROUGH_TRANSLATIONS = (
    (re.compile(r"\bU\.S\.\b|\bUS\b", re.I), "미국"),
    (re.compile(r"\bChina's Xi\b|\bXi\b", re.I), "시진핑"),
    (re.compile(r"\bTrump\b", re.I), "트럼프"),
    (re.compile(r"\bIran\b", re.I), "이란"),
    (re.compile(r"\bChina\b", re.I), "중국"),
    (re.compile(r"\bTaiwan\b", re.I), "대만"),
    (re.compile(r"\bRussia\b", re.I), "러시아"),
    (re.compile(r"\bUkraine\b", re.I), "우크라이나"),
    (re.compile(r"\bHormuz\b", re.I), "호르무즈"),
    (re.compile(r"\bFed\b", re.I), "Fed"),
    (re.compile(r"\bTreasury\b", re.I), "재무부"),
    (re.compile(r"\bwar\b", re.I), "전쟁"),
    (re.compile(r"\bceasefire\b", re.I), "휴전"),
    (re.compile(r"\bsummit\b", re.I), "정상회담"),
    (re.compile(r"\btalks?\b", re.I), "회담"),
    (re.compile(r"\bmeeting\b", re.I), "회동"),
    (re.compile(r"\bsanctions?\b", re.I), "제재"),
    (re.compile(r"\btariffs?\b|\bduties\b", re.I), "관세"),
    (re.compile(r"\btrade\b", re.I), "무역"),
    (re.compile(r"\binflation\b", re.I), "인플레"),
    (re.compile(r"\boil\b|\bcrude\b", re.I), "원유"),
    (re.compile(r"\bmarkets?\b", re.I), "시장"),
    (re.compile(r"\bstocks?\b", re.I), "주식"),
    (re.compile(r"\brates?\b|\byields?\b", re.I), "금리"),
    (re.compile(r"\bhike\b|\bhikes\b|\braise\b|\braises\b", re.I), "인상"),
    (re.compile(r"\bcut\b|\bcuts\b", re.I), "인하"),
    (re.compile(r"\bwarns?\b|\bcautions?\b", re.I), "경고"),
    (re.compile(r"\brejects?\b", re.I), "거부"),
    (re.compile(r"\boffered to help\b", re.I), "지원 제안"),
    (re.compile(r"\bweigh\b|\bweighs\b", re.I), "부담"),
    (re.compile(r"\bclash\b|\bclashes\b", re.I), "충돌"),
)


def _label(value: object) -> str:
    return str(value or "").replace("_", " ").strip()


def _truncate(value: str, max_chars: int = DIGEST_TITLE_MAX_CHARS) -> str:
    text = " ".join(value.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _clean_title(title: str) -> str:
    text = str(title or "").strip()
    text = text.replace("–", "-").replace("—", "-")
    text = text.strip("'\"‘’“” ")
    text = TITLE_SUFFIX_RE.sub("", text).strip()
    return " ".join(text.split())


def _normalize_amount_token(value: str) -> str:
    text = " ".join(str(value or "").replace(" ", "").split())
    text = re.sub(r"(?i)billion$", "B", text)
    text = re.sub(r"(?i)million$", "M", text)
    text = re.sub(r"(?i)thousand$", "K", text)
    return text


def _combined_evidence_text(row: dict[str, Any]) -> str:
    chunks = [
        str(row.get("title") or ""),
        str(row.get("body_text") or ""),
    ]
    for item in row.get("evidence_items") or []:
        if not isinstance(item, dict):
            continue
        chunks.extend(
            [
                str(item.get("title") or ""),
                str(item.get("summary") or ""),
                str(item.get("body_text") or ""),
            ]
        )
    return " ".join(chunk for chunk in chunks if chunk)


def _amount_near(text: str, pattern: re.Pattern[str]) -> str:
    for match in pattern.finditer(text):
        window_start = max(0, match.start() - 90)
        window = text[window_start : match.end() + 90]
        amount_matches = list(AMOUNT_TOKEN_RE.finditer(window))
        if amount_matches:
            keyword_mid = match.start() - window_start + (match.end() - match.start()) / 2
            amount_match = min(
                amount_matches,
                key=lambda amount: abs(
                    amount.start() + (amount.end() - amount.start()) / 2 - keyword_mid
                ),
            )
            return _normalize_amount_token(amount_match.group(0))
    return ""


def _earnings_numeric_facts(row: dict[str, Any]) -> list[str]:
    if str(row.get("event_type") or "") != "earnings":
        return []
    text = _combined_evidence_text(row)
    facts: list[str] = []
    for label, pattern in (
        ("매출", REVENUE_TERM_RE),
        ("EPS", EPS_TERM_RE),
        ("가이던스", GUIDANCE_TERM_RE),
        ("자사주", BUYBACK_TERM_RE),
    ):
        amount = _amount_near(text, pattern)
        if amount:
            fact = f"{label} {amount}"
            if fact not in facts:
                facts.append(fact)
    return facts


def _augment_summary_with_numeric_facts(summary: str, row: dict[str, Any]) -> str:
    contracted = augment_earnings_summary_with_contract(row, summary)
    if contracted != summary:
        return contracted
    facts = [
        fact
        for fact in _earnings_numeric_facts(row)
        if fact.split(" ", 1)[-1] not in summary
    ]
    if not facts:
        return summary
    addition = " / ".join(facts[:2])
    candidate = f"{summary}; {addition}"
    return _truncate(candidate, 180)


def _is_generic_source_hint(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(value or "").strip().lower())
    normalized = normalized.replace("-", "_")
    normalized_without_date = re.sub(
        r"\s+(?:\d{1,2}/\d{1,2}|\d{4}_\d{2}_\d{2}|\d{4}-\d{2}-\d{2})$",
        "",
        normalized,
    )
    if normalized in GENERIC_SOURCE_HINTS or normalized_without_date in GENERIC_SOURCE_HINTS:
        return True
    return bool(
        re.fullmatch(
            r"(?:earn|geo|macro|strat|move|anal|ma|brave|google(?: rss)?|"
            r"rss|web|source|provider|news|breaking_?hint)"
            r"(?:\s+(?:\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2}))?",
            normalized,
        )
    )


def _source_hint(title: str) -> str:
    for pattern, label in SOURCE_HINTS:
        if pattern.search(title):
            return label
    return ""


def _source_hint_for_row(row: dict[str, Any]) -> str:
    editorial = row.get("llm_editorial")
    if isinstance(editorial, dict):
        source_hint = str(editorial.get("source_hint") or "").strip()
        if (
            source_hint
            and "http://" not in source_hint
            and "https://" not in source_hint
            and not _is_generic_source_hint(source_hint)
        ):
            return source_hint[:80]
    if str(row.get("object") or "") == "iran_deal_conditions":
        for item in row.get("evidence_items") or []:
            if not isinstance(item, dict):
                continue
            evidence_text = " ".join(
                str(item.get(key) or "")
                for key in ("title", "summary", "body_text")
            )
            if not re.search(r"abraham accords?", evidence_text, re.I):
                continue
            hint = _source_hint(
                " ".join(
                    str(item.get(key) or "")
                    for key in ("source", "url", "title", "summary")
                )
            )
            if hint:
                return hint
    chunks = [str(row.get("title") or "")]
    for value in row.get("sources") or []:
        chunks.append(str(value or ""))
    for item in row.get("evidence_items") or []:
        if not isinstance(item, dict):
            continue
        chunks.append(str(item.get("source") or ""))
        chunks.append(str(item.get("url") or ""))
        chunks.append(str(item.get("title") or ""))
        chunks.append(str(item.get("summary") or ""))
    return _source_hint(" ".join(chunks))


def _rough_translate_title(title: str) -> str:
    text = _clean_title(title)
    for pattern, replacement in ROUGH_TRANSLATIONS:
        text = pattern.sub(replacement, text)
    return _truncate(text)


def _specific_summary_from_title(title: str) -> str:
    cleaned = _clean_title(title)
    for pattern, summary in SPECIFIC_TITLE_SUMMARIES:
        if pattern.search(cleaned):
            return summary
    return ""


def _looks_like_raw_english(text: str) -> bool:
    words = RAW_ENGLISH_WORD_RE.findall(text)
    return len(words) >= 4


def _controlled_title_fallback(
    *,
    event_type: str,
    subject: str,
    action: str,
) -> str:
    subject_key = subject.upper()
    actions = set(_split_actions(action))
    if event_type == "geo" and subject_key == "TRUMP_XI":
        if actions & {"policy_geo", "diplomacy", "tariff_policy"}:
            return "트럼프-시진핑 회담 — 무역·대만·이란 의제에서 핵심 이견 지속"
        if "conflict" in actions:
            return "트럼프-시진핑 회담 — 대만·이란 리스크가 협상 부담으로 부각"
    if event_type == "geo" and subject_key == "IRAN":
        if "sanctions" in actions:
            return "이란 제재 이슈 — 원유·해운 네트워크 압박 지속"
        if actions & {"policy_geo", "diplomacy", "conflict"}:
            return "이란 이슈 — 협상·유가·호르무즈 변수가 시장 부담으로 지속"
        if "conflict" in actions:
            return "이란 리스크 — 에너지·해상 안보 부담 지속"
        if "policy_geo" in actions:
            return "이란 이슈 — 호르무즈·핵·제재 의제가 시장 변수로 지속"
    if event_type == "geo" and subject_key == "CHINA" and "sanctions" in actions:
        return "미중 갈등 — 제재·기술·안보 긴장 지속"
    return _default_korean_summary(
        event_type=event_type,
        subject=subject,
        action=action,
    )


def _clean_evidence_text(value: object) -> str:
    text = _clean_title(str(value or ""))
    text = re.sub(r"\b#[A-Za-z0-9_]+\b", "", text)
    return _truncate(text, MAX_EVIDENCE_TEXT_CHARS)


def _meaningful_evidence_texts(row: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_items = row.get("evidence_items")
    if not isinstance(evidence_items, list):
        return []
    seen: set[str] = set()
    results = []
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        summary = _clean_evidence_text(item.get("summary"))
        if len(summary) < 40:
            continue
        fingerprint = summary.lower()[:160]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        results.append(
            {
                "source": item.get("source") or "",
                "provider": item.get("provider") or "",
                "summary": summary,
                "summary_chars": len(summary),
            }
        )
    return results


def _select_summary_basis(row: dict[str, Any]) -> dict[str, Any]:
    body_text = _clean_evidence_text(row.get("body_text"))
    if len(body_text) >= MIN_BODY_BASIS_CHARS:
        return {
            "basis": "body",
            "basis_chars": len(body_text),
            "source_count": 1,
            "text": body_text,
            "items": [],
        }

    evidence_texts = _meaningful_evidence_texts(row)
    snippet_text = _truncate(
        " ".join(item["summary"] for item in evidence_texts),
        MAX_EVIDENCE_TEXT_CHARS,
    )
    if len(snippet_text) >= MIN_SNIPPET_BASIS_CHARS:
        return {
            "basis": "snippet",
            "basis_chars": len(snippet_text),
            "source_count": len(evidence_texts),
            "text": snippet_text,
            "items": evidence_texts,
        }

    title = _clean_title(str(row.get("title") or ""))
    return {
        "basis": "title",
        "basis_chars": len(title),
        "source_count": 1 if title else 0,
        "text": title,
        "items": [],
    }


def _event_label_ko(value: object) -> str:
    raw = str(value or "").strip()
    return EVENT_LABELS_KO.get(raw, _label(raw).upper())


def _action_label_ko(value: object) -> str:
    raw = str(value or "").strip()
    return ACTION_LABELS_KO.get(raw, _label(raw))


def _subject_label_ko(value: object) -> str:
    raw = str(value or "").strip()
    upper = raw.upper()
    return SUBJECT_LABELS_KO.get(upper, upper or "미확인")


def _market_marker(event_type: str, action: str) -> str:
    if action in POSITIVE_ACTIONS:
        return "🟢"
    if action in NEGATIVE_ACTIONS:
        return "🔴"
    if event_type == "geo" and action in {"conflict", "sanctions", "tariff_policy"}:
        return "🔴"
    return ""


def _price_reaction(row: dict[str, Any]) -> dict[str, Any]:
    reaction = row.get("price_reaction")
    if isinstance(reaction, dict):
        return reaction
    metadata = row.get("event_metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("price_reaction"), dict):
        return metadata["price_reaction"]
    return {}


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _snapshot_quote(market_snapshot: dict[str, Any] | None, key: str) -> dict[str, Any]:
    if not isinstance(market_snapshot, dict):
        return {}
    values = market_snapshot.get("values")
    if not isinstance(values, dict):
        return {}
    quote = values.get(key)
    if not isinstance(quote, dict):
        return {}
    if quote.get("status") not in {"ok", "stale"}:
        return {}
    if _safe_float(quote.get("value")) is None:
        return {}
    return quote


def _movement_direction_from_text(text: str) -> str:
    has_up = bool(UP_MOVE_PATTERN.search(text))
    has_down = bool(DOWN_MOVE_PATTERN.search(text))
    if has_up and not has_down:
        return "up"
    if has_down and not has_up:
        return "down"
    return ""


def _snapshot_oil_direction(
    market_snapshot: dict[str, Any] | None,
) -> tuple[str, dict[str, Any], float] | None:
    candidates: list[tuple[str, dict[str, Any], float]] = []
    for key in ("brent", "wti"):
        quote = _snapshot_quote(market_snapshot, key)
        if not quote:
            continue
        pct = _safe_float(quote.get("change_pct"))
        if pct is None:
            continue
        candidates.append((key, quote, pct))
    if not candidates:
        return None
    key, quote, pct = max(candidates, key=lambda item: abs(item[2]))
    if abs(pct) < 3.0:
        return None
    return key, quote, pct


def _apply_snapshot_contradiction_guard(
    summary: str,
    row: dict[str, Any],
    market_snapshot: dict[str, Any] | None,
) -> str:
    if str(row.get("event_type") or "") not in {"geo", "macro"}:
        return summary
    text = " ".join([summary, str(row.get("title") or "")])
    if not OIL_TEXT_PATTERN.search(text):
        return summary
    row_direction = _movement_direction_from_text(text)
    snapshot_move = _snapshot_oil_direction(market_snapshot)
    if not row_direction or snapshot_move is None:
        return summary
    key, quote, pct = snapshot_move
    snapshot_direction = "up" if pct > 0 else "down"
    if row_direction == snapshot_direction:
        return summary
    label = "Brent" if key == "brent" else "WTI"
    value = _safe_float(quote.get("value"))
    if value is None:
        return summary
    sign = "+" if pct > 0 else ""
    if snapshot_direction == "up":
        return (
            f"{label} 원유 ${value:.1f} ({sign}{pct:.2f}%) — "
            "현재 유가 반등으로 기존 하락 보도와 방향 충돌"
        )
    return (
        f"{label} 원유 ${value:.1f} ({pct:.2f}%) — "
        "현재 유가 하락으로 기존 상승 보도와 방향 충돌"
    )


def _split_actions(action: str) -> list[str]:
    return [part.strip() for part in re.split(r"[+/,]", action) if part.strip()]


def _action_bias(event_type: str, action: str) -> str:
    actions = set(_split_actions(action))
    if event_type == "earnings" and "earnings_report" in actions:
        return "price_verdict"
    if actions & NEGATIVE_ACTIONS:
        return "negative"
    if actions & POSITIVE_ACTIONS:
        return "positive"
    return "neutral"


def _price_guarded_llm_marker(marker: str, row: dict[str, Any]) -> str:
    if marker not in {"🟢", "🔴"}:
        return marker
    reaction = _price_reaction(row)
    if reaction.get("status") != "ok":
        return marker
    direction = str(reaction.get("direction") or "").strip()
    pct_change = _safe_float(reaction.get("pct_change"))
    absolute_move = abs(pct_change) if pct_change is not None else 0.0
    if marker == "🔴" and direction in {"up", "flat"}:
        return "🟡"
    if marker == "🟢" and direction == "down" and absolute_move >= 2.0:
        return "🟡"
    return marker


def _market_marker_for_row(row: dict[str, Any]) -> str:
    event_type = str(row.get("event_type") or "").strip()
    action = str(row.get("action") or "").strip()
    llm_annotation = _valid_llm_annotation(row)
    if llm_annotation is not None:
        marker = _llm_marker(llm_annotation.get("market_marker"))
        if event_type in COMPANY_EVENT_TYPES:
            return _price_guarded_llm_marker(marker, row)
        return marker

    marker = _market_marker(event_type, action)
    if event_type not in COMPANY_EVENT_TYPES:
        return marker

    reaction = _price_reaction(row)
    if reaction.get("status") != "ok":
        return "🟡" if marker in {"🟢", "🔴"} else marker

    direction = str(reaction.get("direction") or "").strip()
    bias = _action_bias(event_type, action)
    if direction == "up":
        return "🟡" if bias == "negative" else "🟢"
    if direction == "down":
        return "🟡" if bias == "positive" else "🔴"
    if direction == "flat":
        return "🟡" if marker in {"🟢", "🔴"} or bias != "neutral" else ""
    return marker


def _llm_marker(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw == "red":
        return "🔴"
    if raw == "green":
        return "🟢"
    if raw == "none":
        return "🟡"
    return ""


def _valid_llm_annotation(row: dict[str, Any]) -> dict[str, Any] | None:
    annotation = row.get("llm_annotation")
    if not isinstance(annotation, dict):
        return None
    summary = str(annotation.get("summary_ko") or "").strip()
    if not summary:
        return None
    if annotation.get("market_marker") not in {"red", "green", "none"}:
        return None
    if annotation.get("confidence") not in {"high", "medium", "low"}:
        return None
    if annotation.get("basis") not in {"body", "snippet", "title"}:
        return None
    return annotation


def _default_korean_summary(
    *,
    event_type: str,
    subject: str,
    action: str,
) -> str:
    subject_label = _subject_label_ko(subject)
    action_label = _action_label_ko(action)
    actions = set(_split_actions(action))
    if event_type == "geo":
        if len(actions) > 1:
            return f"{subject_label} 지정학 이슈 — 복수 변수 동시 진행"
        if "conflict" in actions:
            return f"{subject_label} 관련 분쟁 리스크가 커지며 시장 부담 확대"
        if "sanctions" in actions:
            return f"{subject_label} 관련 제재 이슈로 지정학 리스크 부각"
        if "tariff_policy" in actions:
            return f"{subject_label} 관련 관세/정책 리스크 부각"
        if "diplomacy" in actions:
            return f"{subject_label} 관련 외교 이벤트 진행"
        if "policy_geo" in actions:
            return f"{subject_label} 관련 정책·지정학 이벤트 진행"
    if event_type == "earnings":
        if action == "guidance_raise":
            return f"{subject_label} 실적 이후 가이던스 상향"
        if action == "guidance_cut":
            return f"{subject_label} 실적 이후 가이던스 하향"
        return f"{subject_label} 실적 관련 신규 이슈"
    if event_type == "corporate_action":
        if action == "ipo":
            return f"{subject_label} IPO 추진"
        if action == "buyback":
            return f"{subject_label} 자사주 매입 발표"
        if action == "ma":
            return f"{subject_label} M&A 또는 전략 거래 발표"
    if event_type == "analyst":
        return f"{subject_label} 애널리스트 의견 또는 목표가 변경"
    if event_type == "strategic":
        if action == "partnership":
            return f"{subject_label} 파트너십 발표"
        if action == "policy_support":
            return f"{subject_label} 정책 지원 또는 보조금 수혜"
        if action == "policy_risk":
            return f"{subject_label} 정책 리스크"
        if action == "supply_deal":
            return f"{subject_label} 공급 계약 또는 공급망 이슈"
        if action == "investment":
            return f"{subject_label} 투자 또는 확장 이슈"
        return f"{subject_label} 전략 투자 또는 파트너십 이슈"
    if event_type == "mover":
        if action == "shares_up":
            return f"{subject_label} 주가 상승 모멘텀 감지"
        if action == "shares_down":
            return f"{subject_label} 주가 하락 리스크 감지"
    if event_type == "macro":
        if action == "fomc_minutes":
            return f"{subject_label} 의사록 공개"
        return f"{subject_label} 매크로 지표 변화 감지"
    if action_label:
        return f"{subject_label} 관련 {action_label} 뉴스"
    return f"{subject_label} 관련 중요 뉴스"


def _merged_actions(row: dict[str, Any]) -> list[str]:
    raw = row.get("merged_actions")
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    actions: list[str] = []
    for value in raw:
        action = str(value or "").strip()
        if not action or action in seen:
            continue
        seen.add(action)
        actions.append(action)
    return actions


def _merged_company_summary(row: dict[str, Any]) -> str:
    actions = _merged_actions(row)
    if len(actions) < 2:
        return ""
    event_type = str(row.get("event_type") or "").strip()
    if event_type not in {"earnings", "corporate_action", "strategic", "mover"}:
        return ""
    subject = str(row.get("subject") or "").strip()
    subject_label = _subject_label_ko(subject)
    action_labels = [_action_label_ko(action) for action in actions[:3]]
    action_text = "·".join(label for label in action_labels if label)
    titles = row.get("merged_titles")
    title = ""
    if isinstance(titles, list) and titles:
        title = str(titles[0] or "")
    if not title:
        title = str(row.get("title") or "")
    detail = _summary_from_title(
        title=title,
        event_type=event_type,
        subject=subject,
        action=actions[0],
    )
    if " — " in detail:
        detail = detail.split(" — ", 1)[1]
    return _truncate(f"{subject_label} {action_text} — {detail}")


def _summary_from_title(
    *,
    title: str,
    event_type: str,
    subject: str,
    action: str,
) -> str:
    lowered = _clean_title(title).lower()
    specific = _specific_summary_from_title(title)
    if specific:
        return specific
    if "raises guidance" in lowered or "raised guidance" in lowered:
        return f"{_subject_label_ko(subject)} 실적 이후 가이던스 상향"
    if "cuts guidance" in lowered or "lowered guidance" in lowered:
        return f"{_subject_label_ko(subject)} 실적 이후 가이던스 하향"
    if "upgrade" in lowered or "upgraded" in lowered:
        return f"{_subject_label_ko(subject)} 투자의견 상향"
    if "downgrade" in lowered or "downgraded" in lowered:
        return f"{_subject_label_ko(subject)} 투자의견 하향"
    if event_type == "geo" and len(_split_actions(action)) > 1:
        return _controlled_title_fallback(
            event_type=event_type,
            subject=subject,
            action=action,
        )
    if _clean_title(title):
        subject_label = _subject_label_ko(subject)
        action_label = _action_label_ko(action)
        topic = f"{subject_label} {action_label}".strip()
        rendered_title = _rough_translate_title(title)
        if _looks_like_raw_english(rendered_title):
            return _controlled_title_fallback(
                event_type=event_type,
                subject=subject,
                action=action,
            )
        return f"{topic} — {rendered_title}"
    return _default_korean_summary(
        event_type=event_type,
        subject=subject,
        action=action,
    )


def _summary_for_row(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    llm_annotation = _valid_llm_annotation(row)
    if llm_annotation is not None:
        basis_name = f"llm_{llm_annotation['basis']}"
        summary = augment_earnings_summary_with_contract(
            row,
            str(llm_annotation["summary_ko"]).strip(),
        )
        return (
            summary,
            {
                "basis": basis_name,
                "basis_chars": len(summary),
                "source_count": 1,
                "text": summary,
                "items": [],
                "llm": True,
                "llm_confidence": llm_annotation["confidence"],
                "llm_market_marker": llm_annotation["market_marker"],
            },
        )

    event_type = str(row.get("event_type") or "").strip()
    subject = str(row.get("subject") or "").strip()
    action = str(row.get("action") or "").strip()
    title = str(row.get("title") or "").strip()
    basis = _select_summary_basis(row)
    basis_text = str(basis.get("text") or "")
    if event_type == "geo" and str(row.get("object") or "") == "iran_deal_conditions":
        return (
            "트럼프, 이란딜 조건에 아브라함 협정 서명 요구 — 중동 6개국 참여 조건으로 협상 불확실성 확대",
            basis,
        )
    merged_summary = _merged_company_summary(row)
    if merged_summary:
        return _augment_summary_with_numeric_facts(merged_summary, row), basis

    if basis["basis"] != "title":
        if basis["basis"] == "body":
            specific = _specific_summary_from_title(basis_text)
            if specific:
                return specific, basis
        title_specific = _specific_summary_from_title(title)
        if title_specific:
            return title_specific, basis
        if basis["basis"] != "body":
            specific = _specific_summary_from_title(basis_text)
            if specific:
                return specific, basis

    return (
        _augment_summary_with_numeric_facts(
            _summary_from_title(
                title=title,
                event_type=event_type,
                subject=subject,
                action=action,
            ),
            row,
        ),
        basis,
    )


def _quality_bucket(row: dict[str, Any]) -> str:
    grade = str(row.get("grade") or "").strip().upper()
    if grade in {"A", "B"}:
        return grade
    score = float(row.get("score") or 0)
    if score >= 90:
        return "A"
    return "B"


def _run_time_label(run: dict[str, Any] | None) -> str:
    if not run:
        return ""
    as_of = str(run.get("as_of") or "").strip()
    if not as_of:
        return ""
    try:
        parsed = datetime.fromisoformat(as_of)
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    return parsed.astimezone(ZoneInfo("Asia/Seoul")).strftime("%H:%M")


def _digest_item_text(
    row: dict[str, Any],
    *,
    market_snapshot: dict[str, Any] | None = None,
) -> str:
    marker = _market_marker_for_row(row)
    marker_prefix = f"{marker} " if marker else ""
    summary, _basis = _summary_for_row(row)
    summary = _apply_snapshot_contradiction_guard(summary, row, market_snapshot)
    source = _source_hint_for_row(row)
    source_suffix = f" ({source})" if source else ""
    return f"• [{_quality_bucket(row)}] {marker_prefix}{summary}{source_suffix}"


def _row_atomic_digest(row: dict[str, Any]) -> bool:
    if bool(row.get("atomic_digest")):
        return True
    metadata = row.get("event_metadata")
    return isinstance(metadata, dict) and bool(metadata.get("atomic_digest"))


def _digest_story_key(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    if _row_atomic_digest(row):
        return None
    event_type = str(row.get("event_type") or "").strip()
    if event_type != "geo":
        return None
    subject = str(row.get("subject") or "").strip().lower()
    action = str(row.get("action") or "").strip().lower()
    story_object = str(row.get("object") or "").strip().lower()
    if not subject or not action or not story_object or story_object.startswith("title_"):
        return None
    return event_type, subject, action, story_object


def _dedupe_digest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    key_to_index: dict[tuple[str, str, str, str], int] = {}
    for row in rows:
        key = _digest_story_key(row)
        if key is None:
            selected.append(row)
            continue
        existing_index = key_to_index.get(key)
        if existing_index is None:
            key_to_index[key] = len(selected)
            selected.append(row)
            continue
        existing = selected[existing_index]
        candidate_rank = (
            str(row.get("effective_date") or ""),
            float(row.get("score") or 0),
            int(row.get("evidence_count") or 0),
        )
        existing_rank = (
            str(existing.get("effective_date") or ""),
            float(existing.get("score") or 0),
            int(existing.get("evidence_count") or 0),
        )
        if candidate_rank > existing_rank:
            selected[existing_index] = row
    return _prune_broad_geo_rows(selected)


SPECIFIC_IRAN_GEO_OBJECTS = {
    "hormuz_toll_regime",
    "iran_deal_conditions",
    "iran_energy_supply",
    "iran_sanctions",
    "sanctions_enforcement",
}
BROAD_IRAN_GEO_OBJECTS = {
    "hormuz_ceasefire_talks",
    "iran_ceasefire_talks",
    "iran_policy_geo",
}


def _is_broad_iran_geo_row(row: dict[str, Any]) -> bool:
    if str(row.get("event_type") or "") != "geo":
        return False
    if str(row.get("subject") or "").lower() != "iran":
        return False
    if str(row.get("action") or "").lower() not in {"policy_geo", "diplomacy"}:
        return False
    return str(row.get("object") or "").lower() in BROAD_IRAN_GEO_OBJECTS


def _prune_broad_geo_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    has_specific_iran_story = any(
        str(row.get("event_type") or "") == "geo"
        and str(row.get("subject") or "").lower() == "iran"
        and str(row.get("object") or "").lower() in SPECIFIC_IRAN_GEO_OBJECTS
        for row in rows
    )
    if not has_specific_iran_story:
        return rows
    return [row for row in rows if not _is_broad_iran_geo_row(row)]


def _format_exclusion_counts(exclusion_counts: dict[str, int] | None) -> str:
    if not exclusion_counts:
        return ""
    labels = [
        ("duplicate", "중복"),
        ("contract_blocked", "계약"),
        ("editorial_dropped", "편집"),
        ("theme_dropped", "테마"),
        ("stale_preview_dropped", "프리뷰"),
        ("snapshot_conflict_dropped", "스냅샷충돌"),
        ("summary_rejected", "요약검증"),
        ("final_publish_dropped", "최종검증"),
    ]
    parts = []
    for key, label in labels:
        count = int(exclusion_counts.get(key) or 0)
        if count > 0:
            parts.append(f"{label} {count}")
    return " · ".join(parts)


def compose_digest_message(
    rows: list[dict[str, Any]],
    *,
    run: dict[str, Any] | None = None,
    skipped_previously_sent: int = 0,
    market_snapshot: dict[str, Any] | None = None,
    exclusion_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    display_rows = _dedupe_digest_rows(rows)
    skipped_total = skipped_previously_sent + max(0, len(rows) - len(display_rows))
    exclusion_counts = dict(exclusion_counts or {})
    exclusion_counts.setdefault("duplicate", skipped_total)
    time_label = _run_time_label(run)
    header = "📰 미국증시 뉴스"
    if time_label:
        header += f" ({time_label} KST)"

    lines = [header, ""]
    if display_rows:
        lines.extend(
            _digest_item_text(row, market_snapshot=market_snapshot) for row in display_rows
        )
    else:
        lines.append("✅ 특이사항 없음 (검증 통과 신규 기준)")
        exclusion_summary = _format_exclusion_counts(exclusion_counts)
        if exclusion_summary:
            lines.append(f"↳ 제외: {exclusion_summary}")

    snapshot_lines = render_market_snapshot_lines(market_snapshot)
    if snapshot_lines:
        lines.extend(["", *snapshot_lines])

    bucket_counts = Counter(_quality_bucket(row) for row in display_rows)
    summary_bases = [_summary_for_row(row)[1] for row in display_rows]
    basis_counts = Counter(str(basis["basis"]) for basis in summary_bases)
    event_counts = Counter(
        _event_label_ko(row.get("event_type"))
        for row in display_rows
        if row.get("event_type")
    )
    event_summary = " ".join(
        f"{event_type}:{count}" for event_type, count in sorted(event_counts.items())
    )
    qc = (
        f"[QC] V2 A:{bucket_counts.get('A', 0)} "
        f"B:{bucket_counts.get('B', 0)} 신규총:{len(display_rows)} "
        f"중복-제외:{skipped_total}"
    )
    lines.extend(["", qc])
    if event_summary:
        lines.append(f"EVENTS: {event_summary}")

    return {
        "index": 1,
        "decision_id": None,
        "event_signature": None,
        "run_id": run.get("id") if run else None,
        "decision": "digest",
        "score": None,
        "event_type": "digest",
        "subject": "market",
        "action": "digest",
        "effective_date": "",
        "evidence_count": sum(int(row.get("evidence_count") or 0) for row in display_rows),
        "providers": sorted(
            {
                provider
                for row in display_rows
                for provider in (row.get("providers") or [])
                if provider
            }
        ),
        "title": header,
        "url": "",
        "market_marker": "",
        "market_snapshot": market_snapshot,
        "summary_basis_counts": dict(sorted(basis_counts.items())),
        "summary_evidence": [
            {
                "event_signature": row.get("event_signature"),
                "basis": basis["basis"],
                "basis_chars": basis["basis_chars"],
                "source_count": basis["source_count"],
                "evidence_contract": row.get("evidence_contract"),
                "price_reaction": _price_reaction(row),
            }
            for row, basis in zip(display_rows, summary_bases)
        ],
        "exclusion_counts": dict(sorted(exclusion_counts.items())),
        "parse_mode": None,
        "text": "\n".join(lines),
    }


def compose_message(row: dict[str, Any], *, index: int) -> dict[str, Any]:
    event_type_raw = str(row.get("event_type") or "").strip()
    action_raw = str(row.get("action") or "").strip()
    subject_raw = str(row.get("subject") or "").strip()
    event_type = _event_label_ko(event_type_raw)
    subject = _subject_label_ko(subject_raw)
    action = _action_label_ko(action_raw)
    score = float(row.get("score") or 0)
    evidence_count = int(row.get("evidence_count") or 0)
    title = str(row.get("title") or "").strip()
    url = str(row.get("url") or "").strip()
    effective_date = str(row.get("effective_date") or "").strip()

    llm_annotation = _valid_llm_annotation(row)
    marker = _market_marker_for_row(row)
    prefix = f"{marker} " if marker else ""
    headline = f"{prefix}{index}. [{event_type}] {subject}"
    if action:
        headline += f" - {action}"

    summary, basis = _summary_for_row(row)
    lines = [headline]
    lines.append(summary)

    return {
        "index": index,
        "decision_id": row.get("decision_id"),
        "event_signature": row.get("event_signature"),
        "run_id": row.get("run_id"),
        "decision": row.get("decision"),
        "score": score,
        "event_type": row.get("event_type"),
        "subject": row.get("subject"),
        "action": row.get("action"),
        "effective_date": effective_date,
        "evidence_count": evidence_count,
        "providers": row.get("providers"),
        "title": title,
        "url": url,
        "market_marker": marker,
        "summary_basis": basis["basis"],
        "summary_basis_chars": basis["basis_chars"],
        "summary_source_count": basis["source_count"],
        "evidence_contract": row.get("evidence_contract"),
        "price_reaction": _price_reaction(row),
        "llm_annotation": llm_annotation,
        "parse_mode": None,
        "text": "\n".join(lines),
    }


def load_message_preview(
    db_path: Path,
    *,
    run_id: str = "latest",
    decisions: set[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    report = load_decision_rows(
        db_path,
        run_id=run_id,
        decisions=decisions or DEFAULT_MESSAGE_DECISIONS,
        limit=limit,
    )
    messages = [
        compose_message(row, index=index)
        for index, row in enumerate(report["rows"], start=1)
    ]
    return {
        "run": report["run"],
        "message_count": len(messages),
        "decision_counts": report["decision_counts"],
        "messages": messages,
    }


def render_message_preview(preview: dict[str, Any], *, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(preview, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if output_format != "markdown":
        raise ReportError(f"unsupported message preview format: {output_format}")

    run = preview["run"]
    lines = [
        "# News Scanner V2 Message Preview",
        "",
        f"- run_id: `{run.get('id', '')}`",
        f"- as_of: `{run.get('as_of', '')}`",
        f"- status: `{run.get('status', '')}`",
        f"- messages: `{preview['message_count']}`",
        "",
    ]
    for message in preview["messages"]:
        lines.extend(
            [
                f"## {message['index']}. [{_label(message['event_type']).upper()}] "
                f"{_label(message['subject']).upper()}",
                "",
                "```text",
                message["text"],
                "```",
                "",
            ]
        )
    return "\n".join(lines)
