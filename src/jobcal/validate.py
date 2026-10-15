"""캘린더 쓰기 직전의 마지막 게이트.

벤더링한 validate_values()/detect_pii() 를 그대로 쓴다. 원본은 엑셀 출력을 염두에
두고 쓰였지만, 실은 이쪽이 더 절실하다 — 엑셀은 사람이 열어보고 이상하면 알아채는데
캘린더는 잘못된 일정이 조용히 꽂히고 아무도 안 본다. 조용한 오답이 크래시보다 나쁘다는
원본 주석이 여기서 그대로 성립한다.
"""
from __future__ import annotations

from datetime import date, timedelta

from .models import Posting
from .vendor.crawler_utils import detect_pii, validate_values

# 마감일이 과거이거나 1년 넘게 먼 값은 파싱이 깨진 신호다.
# 타임스탬프 단위를 틀리면(초↔밀리초) 1970년이나 수천년 뒤가 나온다 — 그걸 여기서 잡는다.
PAST_TOLERANCE_DAYS = 1       # 어제까지는 허용 (실행 시점과 마감 시각의 경계 문제)
FUTURE_HORIZON_DAYS = 365


def _base_schema() -> dict:
    return {
        "company": {"type": "str", "required": True, "max_empty_ratio": 0.0},
        "title": {"type": "str", "required": True, "max_empty_ratio": 0.0},
        "url": {"type": "str", "required": True, "max_empty_ratio": 0.0},
        # 한 소스씩 돌리면 전건 동일한 게 정상이다 — 중복률 검사만 면제한다.
        "source": {"type": "str", "required": True, "allow_uniform": True},
        "active": {"type": "int", "required": True, "allow_uniform": True},
    }


def _deadline_schema(today: date) -> dict:
    return {
        "deadline_ordinal": {
            "type": "int",
            "required": True,
            "min": (today - timedelta(days=PAST_TOLERANCE_DAYS)).toordinal(),
            "max": (today + timedelta(days=FUTURE_HORIZON_DAYS)).toordinal(),
            "allow_uniform": True,
        }
    }


def validate(postings: list[Posting], *, today: date | None = None) -> tuple[list[str], list[str]]:
    """(치명적 문제, 경고) 를 돌려준다.

    치명적 문제가 하나라도 있으면 호출자는 캘린더를 건드리지 않는다. 경고는 보고만 한다.
    """
    today = today or date.today()
    rows = [p.to_row() for p in postings]

    issues = validate_values(rows, _base_schema()) if rows else []

    # 마감일 검사는 날짜가 있는 건에만 적용한다. 원티드처럼 상시채용이 대부분인
    # 소스는 deadline 이 전건 None 인 게 정상이고, 그걸 '빈값 비율 초과' 로
    # 잡으면 경고가 노이즈가 된다.
    dated = [r for r in rows if r.get("deadline_ordinal") is not None]
    if dated:
        issues += validate_values(dated, _deadline_schema(today))

    warnings = detect_pii(rows) if rows else []
    return issues, warnings


def calendar_ready(postings: list[Posting]) -> list[Posting]:
    """캘린더 일정으로 만들 수 있는 것만 남긴다.

    마감일이 없으면 일정을 만들지 않는다. 발견일을 마감일 칸에 꽂으면 캘린더가
    거짓말을 하게 되고, 그 일정을 보고 지원 계획을 세우면 실제로 손해가 난다.
    """
    return [p for p in postings if p.active and p.deadline is not None]
