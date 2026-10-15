"""사람인 공식 Open API 어댑터.

여기가 1차 소스인 이유는 두 필드 때문이다:
  active               — 공고가 열려 있는지를 API 가 직접 말해준다
  expiration-timestamp — 마감일. 캘린더 일정 날짜가 바로 이 값이다

크롤링으로는 "공고가 사라졌다" 와 "요청이 실패했다" 가 같은 모양(0건)으로 보인다.
이 API 는 둘을 구분해 주므로, 실패를 마감으로 오인해 일정을 지우는 사고가
구조적으로 막힌다. 그래서 크롤링보다 이쪽을 먼저 쓴다.

출처 표기: Powered by 취업 사람인 (https://www.saramin.co.kr)
호출 상한: 채용정보 API 1일 최대 500회 (2026-10 기준 공식 안내)
주의: API 가 베타라 인터페이스가 바뀔 수 있다 — 파싱을 _parse_job 한 곳에 모아둔 이유다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from ..models import Posting
from .base import BudgetExceeded, FetchResult, RateLimiter

ENDPOINT = "https://oapi.saramin.co.kr/job-search"
KST = ZoneInfo("Asia/Seoul")

# 마감일이 이보다 멀면 '상시채용(채용시 마감)' 으로 본다.
# 사람인은 상시 공고에 아주 먼 만료 시각을 넣는다. 그걸 그대로 날짜로 쓰면
# 2년 뒤에 일정이 하나 꽂히고 아무 쓸모가 없다 — 차라리 날짜 없음으로 둔다.
ALWAYS_OPEN_HORIZON_DAYS = 365


class SaraminSource:
    name = "saramin"

    def __init__(
        self,
        access_key: str,
        *,
        count: int = 110,          # 공식 상한
        max_pages: int = 2,        # 회사당 최대 페이지 — 관심기업 조회에 2면 충분하다
        delay: float = 1.0,
        max_requests: int | None = 200,
        directhire_only: bool = True,
        timeout: float = 15.0,
    ) -> None:
        if not access_key:
            raise ValueError("SARAMIN_ACCESS_KEY 가 비어 있습니다")
        self.access_key = access_key
        self.count = min(count, 110)
        self.max_pages = max_pages
        self.directhire_only = directhire_only
        self.timeout = timeout
        # max_requests 에 기본값을 둔 것은 벤더 원본 철학과 다르다. 원본은 '사용자가
        # 직접 정하라' 고 비워둔다. 여기서는 무인 cron 이라 물어볼 사람이 없으므로
        # 안전한 쪽으로 기본값을 두고, 호출자가 올릴 수 있게 열어둔다.
        self.limiter = RateLimiter(delay=delay, max_requests=max_requests)

    # ── 수집 ──────────────────────────────────────────────────────────────
    def fetch(self, companies: list[str]) -> FetchResult:
        result = FetchResult(source=self.name)
        if not companies:
            return result.fail("관심기업 목록이 비어 있습니다")

        with httpx.Client(timeout=self.timeout, headers={"Accept": "application/json"}) as client:
            for company in companies:
                try:
                    postings = self._fetch_company(client, company)
                except BudgetExceeded as exc:
                    # 상한에 닿은 것은 '덜 가져온' 상태다. 전수로 믿으면 안 된다.
                    return result.fail(f"요청 상한 도달 — {exc}")
                except httpx.HTTPError as exc:
                    result.fail(f"{company}: HTTP 오류 — {exc}")
                    continue
                except ValueError as exc:
                    result.fail(f"{company}: 응답 해석 실패 — {exc}")
                    continue
                result.postings.extend(postings)

        return result

    def _fetch_company(self, client: httpx.Client, company: str) -> list[Posting]:
        collected: list[Posting] = []
        for page in range(self.max_pages):
            self.limiter.wait()
            params = {
                "access-key": self.access_key,
                "keywords": company,
                "fields": "expiration-date",
                "count": self.count,
                "start": page,
                "sort": "pd",
            }
            if self.directhire_only:
                # 헤드헌팅·파견 공고를 뺀다. 관심기업 마감일 추적에는 노이즈다.
                params["sr"] = "directhire"

            response = client.get(ENDPOINT, params=params)
            if response.status_code in (429, 503):
                self.limiter.backoff()
                continue
            response.raise_for_status()
            self.limiter.reset_errors()

            payload = response.json()
            if "jobs" not in payload:
                # 에러 응답은 {"code":..,"message":..} 모양으로 온다
                raise ValueError(str(payload)[:200])

            jobs = payload["jobs"].get("job") or []
            if isinstance(jobs, dict):     # 1건이면 리스트가 아니라 객체로 올 수 있다
                jobs = [jobs]

            for raw in jobs:
                posting = self._parse_job(raw, company)
                if posting is not None:
                    collected.append(posting)

            if len(jobs) < self.count:
                break      # 마지막 페이지

        return collected

    # ── 파싱 ──────────────────────────────────────────────────────────────
    def _parse_job(self, raw: dict, queried_company: str) -> Posting | None:
        """사람인 job 객체 하나를 Posting 으로. 베타 API 라 변경 지점을 여기로 모은다."""
        posting_id = str(raw.get("id") or "").strip()
        if not posting_id:
            return None

        position = raw.get("position") or {}
        company_name = (
            ((raw.get("company") or {}).get("detail") or {}).get("name")
            or queried_company
        ).strip()

        return Posting(
            source=self.name,
            posting_id=posting_id,
            company=company_name,
            title=str(position.get("title") or "").strip(),
            url=str(raw.get("url") or "").strip(),
            deadline=_to_kst_date(raw.get("expiration-timestamp")),
            location=((position.get("location") or {}).get("name") or "").strip() or None,
            job_category=((position.get("job-code") or {}).get("name") or "").strip() or None,
            posted_at=_to_kst_date(raw.get("posting-timestamp"), horizon_days=None),
            # active 는 문자열 "1"/"0" 으로 올 수 있다
            active=str(raw.get("active", "1")).strip() == "1",
            extra={"queried_company": queried_company},
        )


def _to_kst_date(value, horizon_days: int | None = ALWAYS_OPEN_HORIZON_DAYS) -> date | None:
    """Unix timestamp → KST 기준 날짜.

    UTC 로 변환하면 안 된다. 한국 공고 마감은 거의 'KST 23:59' 인데, 그 순간의
    UTC 날짜는 하루 전이다. 그대로 쓰면 모든 마감 일정이 하루씩 앞으로 밀린다.
    """
    if value in (None, "", 0, "0"):
        return None
    try:
        stamp = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None

    try:
        converted = datetime.fromtimestamp(stamp, tz=KST).date()
    except (OverflowError, OSError, ValueError):
        return None

    if horizon_days is not None:
        today = datetime.now(tz=KST).date()
        if converted > today + timedelta(days=horizon_days):
            return None      # 상시채용 — 날짜 없음으로 둔다
    return converted
