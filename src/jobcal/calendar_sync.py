"""Google Calendar 멱등 동기화.

핵심 설계 셋:

1) event id 를 우리가 정한다.
   Calendar API 는 클라이언트 지정 id(base32hex)를 받는다. (source, posting_id)
   해시를 id 로 쓰면 insert 가 409 를 주는 것만으로 "이미 있다" 가 판정된다.
   별도 상태 저장소(DB·커밋되는 state 파일)가 필요 없고, 캘린더 자체가 상태다.

2) 우리가 만든 일정만 건드린다.
   extendedProperties.private.jobcal = "1" 로 태그하고, 조회할 때도 그 태그로만
   가져온다. 사용자가 손으로 넣은 일정은 이 코드에 보이지 않는다.

3) 삭제는 '전수로 믿을 수 있는 소스' 에 대해서만 한다.
   수집이 반쯤 실패해 0건이 돌아온 걸 '전부 마감' 으로 읽으면 캘린더가 통째로
   비워진다. 크롤링 기반 동기화에서 가장 흔한 사고가 이것이다. 그래서
   FetchResult.complete=False 인 소스의 일정은 삭제 대상에서 아예 빠지고,
   그 위에 삭제 비율 상한을 한 겹 더 둔다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from .models import Posting

logger = logging.getLogger(__name__)

TAG_KEY = "jobcal"
TAG_VALUE = "1"
CALENDAR_TZ = "Asia/Seoul"

# 한 번의 실행에서 기존 일정의 이 비율을 넘게 지우려 하면 멈춘다.
# 정상적인 날이라면 하루에 사라지는 공고는 전체의 일부다. 절반이 사라졌다는 것은
# 공고가 마감된 게 아니라 수집이 깨졌을 가능성이 훨씬 높다.
DEFAULT_MAX_DELETE_RATIO = 0.34

# 알림: 7일 전 / 1일 전 (종일 일정 기준, 분 단위)
DEFAULT_REMINDERS = (7 * 24 * 60, 24 * 60)


@dataclass(frozen=True)
class ExistingEvent:
    event_id: str
    source: str
    content_hash: str
    summary: str = ""


@dataclass
class SyncPlan:
    create: list[Posting] = field(default_factory=list)
    update: list[Posting] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    delete: list[ExistingEvent] = field(default_factory=list)
    delete_skipped: list[ExistingEvent] = field(default_factory=list)
    blocked: str | None = None

    @property
    def is_noop(self) -> bool:
        return not (self.create or self.update or self.delete)

    def summary_line(self) -> str:
        return (
            f"신규 {len(self.create)} · 변경 {len(self.update)} · "
            f"유지 {len(self.unchanged)} · 삭제 {len(self.delete)} "
            f"(삭제보류 {len(self.delete_skipped)})"
        )


def build_plan(
    desired: list[Posting],
    existing: list[ExistingEvent],
    *,
    complete_sources: set[str],
    max_delete_ratio: float = DEFAULT_MAX_DELETE_RATIO,
) -> SyncPlan:
    """순수 함수 — 네트워크를 타지 않는다. 동기화 로직의 테스트 지점이 여기다."""
    plan = SyncPlan()
    existing_by_id = {e.event_id: e for e in existing}
    desired_by_id = {p.event_id: p for p in desired}

    for event_id, posting in desired_by_id.items():
        current = existing_by_id.get(event_id)
        if current is None:
            plan.create.append(posting)
        elif current.content_hash != posting.content_hash:
            plan.update.append(posting)
        else:
            plan.unchanged.append(event_id)

    for event_id, current in existing_by_id.items():
        if event_id in desired_by_id:
            continue
        if current.source in complete_sources:
            plan.delete.append(current)
        else:
            # 그 소스가 이번 실행에서 전수를 못 가져왔다 — 안 보인다고 마감은 아니다.
            plan.delete_skipped.append(current)

    if plan.delete and existing:
        ratio = len(plan.delete) / len(existing)
        if ratio > max_delete_ratio:
            plan.blocked = (
                f"삭제 대상이 기존 일정의 {ratio:.0%} ({len(plan.delete)}/{len(existing)}) 입니다 — "
                f"상한 {max_delete_ratio:.0%} 를 넘었습니다. 공고가 한꺼번에 마감된 것보다 "
                "수집이 깨진 쪽이 가능성이 높아 중단합니다. 확인 후 --force 로 다시 실행하세요"
            )
    return plan


def to_event_body(posting: Posting, *, reminders=DEFAULT_REMINDERS) -> dict:
    """Posting → Calendar 종일 일정.

    종일 일정의 end.date 는 '배타적' 이다. 하루짜리 일정은 end = start + 1 이어야
    하고, start 와 같게 두면 일정이 아예 안 보이거나 길이 0 으로 들어간다.
    """
    if posting.deadline is None:
        raise ValueError(f"마감일 없는 공고는 일정으로 만들지 않는다: {posting.event_id}")

    description_lines = [posting.url, "", f"회사: {posting.company}"]
    if posting.location:
        description_lines.append(f"지역: {posting.location}")
    if posting.job_category:
        description_lines.append(f"직무: {posting.job_category}")
    description_lines += ["", f"출처: {posting.source}", "jobcal-sync 가 자동 생성한 일정입니다."]

    return {
        "id": posting.event_id,
        "summary": f"{posting.summary} — 마감",
        "description": "\n".join(description_lines),
        "start": {"date": posting.deadline.isoformat()},
        "end": {"date": (posting.deadline + timedelta(days=1)).isoformat()},
        "transparency": "transparent",     # 바쁨으로 잡지 않는다
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": m} for m in reminders],
        },
        "extendedProperties": {
            "private": {
                TAG_KEY: TAG_VALUE,
                "source": posting.source,
                "postingId": posting.posting_id,
                "contentHash": posting.content_hash,
            }
        },
    }


class CalendarSync:
    """service 를 주입받는다 — 테스트에서 가짜를 넣을 수 있게 하려는 것이다."""

    def __init__(self, service, calendar_id: str, *, dry_run: bool = False):
        self.service = service
        self.calendar_id = calendar_id
        self.dry_run = dry_run

    # ── 조회 ──────────────────────────────────────────────────────────────
    def list_managed(self) -> list[ExistingEvent]:
        """우리가 만든 일정만 전부 가져온다(페이지네이션 포함)."""
        events: list[ExistingEvent] = []
        page_token = None
        while True:
            response = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    privateExtendedProperty=[f"{TAG_KEY}={TAG_VALUE}"],
                    showDeleted=False,
                    singleEvents=True,
                    maxResults=2500,
                    pageToken=page_token,
                )
                .execute()
            )
            for item in response.get("items", []):
                private = (item.get("extendedProperties") or {}).get("private") or {}
                events.append(
                    ExistingEvent(
                        event_id=item["id"],
                        source=private.get("source", ""),
                        content_hash=private.get("contentHash", ""),
                        summary=item.get("summary", ""),
                    )
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                return events

    # ── 적용 ──────────────────────────────────────────────────────────────
    def apply(self, plan: SyncPlan, *, force: bool = False) -> dict:
        if plan.blocked and not force:
            logger.error("동기화 중단 — %s", plan.blocked)
            return {"applied": False, "reason": plan.blocked}

        counts = {"created": 0, "updated": 0, "deleted": 0, "failed": 0}
        if self.dry_run:
            logger.info("[dry-run] %s", plan.summary_line())
            return {"applied": False, "reason": "dry-run", "plan": plan.summary_line()}

        for posting in plan.create:
            body = to_event_body(posting)
            try:
                self.service.events().insert(
                    calendarId=self.calendar_id, body=body
                ).execute()
                counts["created"] += 1
            except Exception as exc:                      # noqa: BLE001
                # 같은 id 가 이미 있으면 409 다. 다른 실행과 겹쳤다는 뜻이므로
                # 생성 대신 갱신으로 넘긴다 — 이게 멱등성의 마지막 보루다.
                if _is_conflict(exc):
                    if self._patch(posting, counts):
                        counts["updated"] += 1
                else:
                    logger.warning("생성 실패 %s — %s", posting.event_id, exc)
                    counts["failed"] += 1

        for posting in plan.update:
            if self._patch(posting, counts):
                counts["updated"] += 1

        for event in plan.delete:
            try:
                self.service.events().delete(
                    calendarId=self.calendar_id, eventId=event.event_id
                ).execute()
                counts["deleted"] += 1
            except Exception as exc:                      # noqa: BLE001
                logger.warning("삭제 실패 %s — %s", event.event_id, exc)
                counts["failed"] += 1

        logger.info("동기화 완료 — %s", counts)
        return {"applied": True, **counts}

    def _patch(self, posting: Posting, counts: dict) -> bool:
        body = to_event_body(posting)
        body.pop("id", None)      # patch 에는 id 를 넣지 않는다
        try:
            self.service.events().patch(
                calendarId=self.calendar_id, eventId=posting.event_id, body=body
            ).execute()
            return True
        except Exception as exc:                          # noqa: BLE001
            logger.warning("갱신 실패 %s — %s", posting.event_id, exc)
            counts["failed"] += 1
            return False


def _is_conflict(exc: Exception) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status == 409 or "409" in str(exc)
