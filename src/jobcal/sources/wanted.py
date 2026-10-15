"""원티드 공개 JSON API 어댑터 — 기본값 비활성.

벤더링한 fingerprint(vendor/fingerprints/wanted_co_kr/profile.json)에 엔드포인트가
이미 적혀 있어 정찰 비용은 0 이다. 그런데도 기본값을 꺼둔 이유가 두 가지 있다.

1) 회사명으로 좁혀 물어볼 수 없다.
   /api/v4/jobs 에는 회사 필터가 없다. 관심기업만 보려면 목록을 페이징해서
   클라이언트에서 걸러야 한다 — 필요한 것만 가져오는 게 아니라 전체를 받아놓고
   버리는 모양이고, 분량 축에서 사람인 API 와 성격이 완전히 다르다.
   그래서 페이지 예산을 명시적으로 묶고, 예산이 먼저 떨어지면 complete=False 로
   내려 동기화기가 삭제 판정을 건너뛰게 한다.

2) robots.txt·약관을 이 코드가 대신 확인해 줄 수 없다.
   check_robots() 는 '허용' 과 '확인 실패' 를 구분해서 돌려주는데, 무인 cron 에서
   '확인 실패' 를 '허용' 으로 읽고 진행하면 안 된다. 그래서 설정에서 사용자가
   직접 확인했다고 표시(acknowledged_robots)하기 전까지는 돌지 않는다.

상시채용 공고가 대부분이어서 마감일이 없는 경우가 많다. 날짜 없는 공고는
캘린더 일정을 만들지 않고 리포트에만 남긴다 — '발견일' 을 '마감일' 칸에 꽂으면
캘린더의 의미가 깨진다.
"""
from __future__ import annotations

import httpx

from ..models import Posting
from .base import (
    AutomatedAccessBlocked,
    BudgetExceeded,
    FetchResult,
    RateLimiter,
    check_robots,
    detect_softblock,
)

LIST_ENDPOINT = "https://www.wanted.co.kr/api/v4/jobs"
DETAIL_URL = "https://www.wanted.co.kr/wd/{posting_id}"
PAGE_LIMIT = 20          # 프로필에 기록된 페이지 크기


class WantedSource:
    name = "wanted"

    def __init__(
        self,
        *,
        acknowledged_robots: bool = False,
        max_pages: int = 10,
        delay: float = 1.5,
        max_requests: int | None = 15,
        timeout: float = 15.0,
        check_robots_at_runtime: bool = True,
    ) -> None:
        self.acknowledged_robots = acknowledged_robots
        self.max_pages = max_pages
        self.timeout = timeout
        self.check_robots_at_runtime = check_robots_at_runtime
        self.limiter = RateLimiter(delay=delay, max_requests=max_requests)

    def fetch(self, companies: list[str]) -> FetchResult:
        result = FetchResult(source=self.name)

        if not self.acknowledged_robots:
            return result.fail(
                "wanted 소스가 비활성입니다 — config 의 sources.wanted.acknowledged_robots 를 "
                "true 로 두기 전에 https://www.wanted.co.kr/robots.txt 와 이용약관을 "
                "직접 확인하세요. 이 코드가 대신 판단하지 않습니다"
            )

        if self.check_robots_at_runtime:
            verdict = check_robots(LIST_ENDPOINT, user_agent="*")
            if verdict["error"]:
                # 확인 실패는 허용이 아니다. 무인 실행에서는 멈추는 쪽이 맞다.
                return result.fail(f"robots.txt 확인 실패 — {verdict['error']}")
            if not verdict["allowed"]:
                return result.fail(f"robots.txt 가 이 경로를 금지합니다 — {verdict['robots_url']}")
            if verdict["crawl_delay"]:
                self.limiter.delay = max(self.limiter.delay, float(verdict["crawl_delay"]))

        wanted_names = {c.strip().lower() for c in companies if c.strip()}
        if not wanted_names:
            return result.fail("관심기업 목록이 비어 있습니다")

        exhausted = True
        with httpx.Client(timeout=self.timeout, headers={"Accept": "application/json"}) as client:
            for page in range(self.max_pages):
                try:
                    self.limiter.wait()
                except BudgetExceeded as exc:
                    result.fail(f"요청 상한 도달 — {exc}")
                    exhausted = False
                    break

                params = {
                    "country": "kr",
                    "job_sort": "job.latest_order",
                    "years": -1,
                    "locations": "all",
                    "limit": PAGE_LIMIT,
                    "offset": page * PAGE_LIMIT,
                }
                try:
                    response = client.get(LIST_ENDPOINT, params=params)
                    if response.status_code in (429, 503):
                        self.limiter.backoff()
                        continue

                    # 차단 판정을 raise_for_status 앞에 둔다. 403 을 평범한 HTTP 오류로
                    # 흘리면 '일시적 실패' 처럼 보이고, 그러면 다음 실행에서 또 두드린다.
                    #
                    # min_size=0 인 이유: 벤더 기본값(3000B)은 HTML 페이지 기준이다.
                    # JSON API 는 정상 응답도 작을 수 있어 그대로 쓰면 오탐이 난다.
                    verdict = detect_softblock(
                        response.text,
                        status=response.status_code,
                        cookies=dict(response.cookies),
                        min_size=0,
                    )
                    if verdict["blocked"]:
                        raise AutomatedAccessBlocked(_block_message(response, verdict))

                    response.raise_for_status()
                    self.limiter.reset_errors()
                    # 프로필 메모: 이 API 는 resp.text 가 비어 올 수 있다 — json() 을 쓴다
                    payload = response.json()
                except AutomatedAccessBlocked as exc:
                    # 사다리를 올리지 않는다. 여기서 끝이다.
                    result.fail(str(exc))
                    exhausted = False
                    break
                except BudgetExceeded as exc:
                    result.fail(f"연속 거절 — {exc}")
                    exhausted = False
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    result.fail(f"목록 {page}페이지 실패 — {exc}")
                    exhausted = False
                    break

                rows = payload.get("data") or []
                for raw in rows:
                    posting = self._parse(raw)
                    if posting and posting.company.strip().lower() in wanted_names:
                        result.postings.append(posting)

                if len(rows) < PAGE_LIMIT:
                    break      # 목록 끝까지 봤다
            else:
                # for 가 break 없이 끝났다 = max_pages 를 다 썼는데 목록은 안 끝났다
                exhausted = False

        if not exhausted:
            result.fail(
                "목록을 끝까지 보지 못했습니다 — 이 소스의 결과는 전수가 아니므로 "
                "마감 판정(삭제)에는 쓰지 않습니다"
            )
        return result

    def _parse(self, raw: dict) -> Posting | None:
        posting_id = str(raw.get("id") or "").strip()
        if not posting_id:
            return None
        address = raw.get("address") or {}
        return Posting(
            source=self.name,
            posting_id=posting_id,
            company=str((raw.get("company") or {}).get("name") or "").strip(),
            title=str(raw.get("position") or "").strip(),
            url=DETAIL_URL.format(posting_id=posting_id),
            # 목록 API 는 마감일을 주지 않는다. 상시채용이 많아 대부분 정말로 없다.
            deadline=None,
            location=str(address.get("location") or "").strip() or None,
            job_category=None,
            active=True,
        )


def _block_message(response, verdict: dict) -> str:
    """차단 응답을 '무엇이 왜 막았는가' 로 번역한다.

    메시지가 중요하다. '403 Forbidden' 만 찍히면 다음 사람(또는 내일의 나)이
    재시도·우회를 떠올리지만, 'WAF 가 거절했다' 로 적혀 있으면 멈출 판단이 선다.
    """
    server = (response.headers.get("server") or "").lower()
    via = (response.headers.get("via") or "").lower()
    cdn = None
    if "cloudfront" in server or "cloudfront" in via:
        # CloudFront 의 'Request blocked' 는 오리진 장애나 설정 누락이 아니라
        # AWS WAF 규칙이 요청을 의도적으로 거절한 경우다(AWS 공식 문서).
        cdn = "CloudFront + AWS WAF"
    elif "cloudflare" in server:
        cdn = "Cloudflare"
    elif "akamai" in server or "akamaighost" in server:
        cdn = "Akamai"

    where = f"{cdn} 가" if cdn else "사이트가"
    signals = ", ".join(verdict.get("signals") or []) or f"HTTP {response.status_code}"

    return (
        f"자동 접근이 차단됐습니다 — {where} 이 요청을 거절했습니다 ({signals}). "
        "일시적 오류가 아니라 차단 규칙에 걸린 상태이므로 재시도하지 않고 멈춥니다. "
        "우회는 이용약관 위반과 층이 다른 문제(기술적 우회)라 이 코드가 자동으로 넘지 않습니다. "
        "사람인 공식 API 를 1차 소스로 쓰거나, 꼭 필요하면 원티드에 데이터 이용을 직접 문의하세요"
    )
