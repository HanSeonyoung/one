"""수집 어댑터 공통 계약.

어댑터는 '정규화된 Posting 리스트' 와 '수집이 신뢰할 만한가' 를 함께 돌려준다.
두 번째가 핵심이다 — 0건이 돌아왔을 때 "공고가 없다" 와 "수집이 실패했다" 를
구분하지 못하면 동기화기가 캘린더를 전부 비워버린다. 그 사고를 막는 경계가 여기다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..models import Posting
from ..vendor.crawler_utils import (
    BudgetExceeded,
    RateLimiter,
    check_robots,
    detect_softblock,
)

__all__ = [
    "BudgetExceeded",
    "RateLimiter",
    "check_robots",
    "detect_softblock",
    "FetchResult",
    "JobSource",
    "AutomatedAccessBlocked",
]


class AutomatedAccessBlocked(Exception):
    """사이트가 자동 접근을 거절했다 (WAF·봇 탐지·CAPTCHA).

    이건 '더 세게 시도하면 되는' 오류가 아니다. 벤더 레포는 이 지점에서 사다리를
    더 올리지 않고 사용자에게 한 번 묻는데, 무인 cron 에는 물어볼 사람이 없다.
    그래서 여기서는 묻지 않고 멈춘다 — 자동 우회는 ToS 위반과 층이 다른 문제이고
    (기술적 우회), 무인으로 넘을 판단을 코드가 대신 할 자리가 아니다.
    """


@dataclass
class FetchResult:
    """한 소스의 수집 결과.

    complete=False 는 "이 소스의 결과를 전수로 믿지 말라" 는 뜻이다. 동기화기는
    이 플래그가 내려간 소스에 대해서는 삭제 판정을 건너뛴다 — 안 보이는 공고를
    마감으로 오인하지 않기 위해서다.
    """

    source: str
    postings: list[Posting] = field(default_factory=list)
    complete: bool = True
    errors: list[str] = field(default_factory=list)

    def fail(self, message: str) -> "FetchResult":
        self.complete = False
        self.errors.append(message)
        return self


class JobSource(Protocol):
    """모든 수집 어댑터가 만족해야 하는 인터페이스."""

    name: str

    def fetch(self, companies: list[str]) -> FetchResult:
        """관심기업 목록을 받아 공고를 수집한다.

        companies 는 '필터' 가 아니라 '질의' 다. 전체를 긁어와 걸러내는 게 아니라
        회사별로 좁혀 물어본다 — 분량 축에서 이 차이가 크다.
        """
        ...
