"""정규화 스키마 — 수집 소스가 달라도 이 아래로는 같은 모양이 흐른다.

소스별 어댑터가 서로 다른 응답을 전부 Posting 하나로 눌러서 내놓는다.
어댑터를 빼거나 더해도 검증기와 동기화기는 손대지 않는다는 게 이 경계의 요점이다.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

# Google Calendar 가 허용하는 클라이언트 지정 event id 는 base32hex 다 —
# 문자 'a'~'v' 와 숫자 '0'~'9', 길이 5~1024. sha1 의 16진 다이제스트(0-9a-f)는
# 이 집합의 부분집합이라 변환 없이 그대로 쓸 수 있다.
_EVENT_ID_RE = re.compile(r"^[a-v0-9]{5,1024}$")
_EVENT_ID_PREFIX = "jobcal"  # j,o,b,c,a,l 모두 a-v 범위 안


@dataclass(frozen=True)
class Posting:
    """채용공고 한 건. 캘린더 일정 한 건과 1:1 로 대응한다."""

    source: str          # "saramin" | "wanted" | ...
    posting_id: str      # 소스 안에서의 공고 고유 번호
    company: str
    title: str
    url: str
    deadline: date | None = None      # 마감일. None 이면 상시채용이거나 미공개
    location: str | None = None
    job_category: str | None = None
    posted_at: date | None = None
    active: bool = True               # 소스가 "열려 있다" 고 말하는가
    extra: dict = field(default_factory=dict, compare=False, repr=False)

    # ── 멱등 키 ───────────────────────────────────────────────────────────
    @property
    def event_id(self) -> str:
        """결정적 Calendar event id.

        (source, posting_id) 만으로 만든다 — 제목이나 마감일이 바뀌어도 같은 공고는
        같은 id 를 유지해야 하기 때문이다. 내용 변경은 content_hash 가 따로 잡는다.
        """
        seed = f"{self.source}:{self.posting_id}".encode("utf-8")
        event_id = _EVENT_ID_PREFIX + hashlib.sha1(seed).hexdigest()
        assert _EVENT_ID_RE.match(event_id), f"base32hex 위반: {event_id}"
        return event_id

    @property
    def content_hash(self) -> str:
        """일정 본문에 실제로 들어가는 값들만의 해시.

        이게 같으면 patch 를 보내지 않는다. 호출 수를 줄이려는 것도 있지만,
        그보다 동기화 로그에 '바뀐 것' 만 남게 하려는 목적이 크다 —
        매일 전건 patch 가 찍히면 정작 바뀐 공고를 못 찾는다.
        """
        parts = [
            self.company,
            self.title,
            self.url,
            self.deadline.isoformat() if self.deadline else "",
            self.location or "",
        ]
        return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()

    # ── 캘린더 표현 ───────────────────────────────────────────────────────
    @property
    def summary(self) -> str:
        return f"[{self.company}] {self.title}"

    def to_row(self) -> dict:
        """검증기(validate_values)와 CSV 출력이 함께 쓰는 평탄한 행.

        벤더링한 validate_values 는 dict 리스트를 받으므로 여기서 모양을 맞춘다.
        마감일은 ordinal(정수)로 넘겨 min/max 범위 검사를 그대로 받게 한다.
        """
        return {
            "source": self.source,
            "posting_id": self.posting_id,
            "company": self.company,
            "title": self.title,
            "url": self.url,
            "deadline": self.deadline.isoformat() if self.deadline else "",
            "deadline_ordinal": self.deadline.toordinal() if self.deadline else None,
            "location": self.location or "",
            "job_category": self.job_category or "",
            "active": int(self.active),
        }


def dedupe(postings: list[Posting]) -> list[Posting]:
    """event_id 기준 중복 제거.

    같은 공고가 두 소스에서 잡히는 일은 event_id 가 source 를 포함하므로 걸러지지
    않는다 — 그건 의도다. 여기서 막는 것은 한 소스 안에서 페이지네이션이 겹쳐
    같은 공고가 두 번 들어오는 경우다(offset 페이징에서 흔하다).
    """
    seen: dict[str, Posting] = {}
    for posting in postings:
        seen.setdefault(posting.event_id, posting)
    return list(seen.values())
