"""Calendar 호출 계층 테스트 — 가짜 service 를 주입해 네트워크 없이 검증한다."""
from datetime import date

import pytest

from jobcal.calendar_sync import (
    CalendarSync,
    ExistingEvent,
    SyncPlan,
    build_plan,
    to_event_body,
)
from jobcal.models import Posting


def posting(posting_id="1", **kw) -> Posting:
    base = dict(
        source="saramin",
        posting_id=posting_id,
        company="PNP시큐어",
        title="서버 응용 개발",
        url="https://example.com/1",
        deadline=date(2026, 11, 1),
        location="서울 > 송파구",
    )
    base.update(kw)
    return Posting(**base)


# ── 일정 본문 ────────────────────────────────────────────────────────────────
def test_all_day_end_date_is_exclusive():
    """종일 일정의 end.date 는 배타적이다 — start 와 같게 두면 일정이 안 보인다."""
    body = to_event_body(posting())
    assert body["start"]["date"] == "2026-11-01"
    assert body["end"]["date"] == "2026-11-02"


def test_body_carries_tag_and_hash():
    private = to_event_body(posting())["extendedProperties"]["private"]
    assert private["jobcal"] == "1"
    assert private["source"] == "saramin"
    assert private["contentHash"] == posting().content_hash


def test_body_sets_reminders_and_stays_free():
    body = to_event_body(posting())
    minutes = {o["minutes"] for o in body["reminders"]["overrides"]}
    assert minutes == {7 * 24 * 60, 24 * 60}
    assert body["transparency"] == "transparent"


def test_undated_posting_is_refused():
    with pytest.raises(ValueError):
        to_event_body(posting(deadline=None))


# ── 가짜 service ─────────────────────────────────────────────────────────────
class FakeRequest:
    def __init__(self, recorder, verb, kwargs, error=None):
        self.recorder, self.verb, self.kwargs, self.error = recorder, verb, kwargs, error

    def execute(self):
        self.recorder.append((self.verb, self.kwargs))
        if self.error:
            raise self.error
        return {}


class FakeHttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = type("R", (), {"status": status})()


class FakeEvents:
    def __init__(self, recorder, pages=None, insert_error=None):
        self.recorder = recorder
        self.pages = pages or [{"items": []}]
        self.insert_error = insert_error
        self._page = 0

    def list(self, **kw):
        # 태그 필터가 실제로 걸려 나가는지도 함께 본다
        assert kw["privateExtendedProperty"] == ["jobcal=1"]
        page = self.pages[self._page]
        self._page = min(self._page + 1, len(self.pages) - 1)
        return _Static(page)

    def insert(self, **kw):
        return FakeRequest(self.recorder, "insert", kw, error=self.insert_error)

    def patch(self, **kw):
        return FakeRequest(self.recorder, "patch", kw)

    def delete(self, **kw):
        return FakeRequest(self.recorder, "delete", kw)


class _Static:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeService:
    def __init__(self, recorder, pages=None, insert_error=None):
        self._events = FakeEvents(recorder, pages, insert_error)

    def events(self):
        return self._events


# ── 적용 ─────────────────────────────────────────────────────────────────────
def test_noop_plan_sends_no_requests():
    calls = []
    syncer = CalendarSync(FakeService(calls), "cal@example.com")
    target = posting()
    plan = build_plan(
        [target],
        [ExistingEvent(target.event_id, "saramin", target.content_hash)],
        complete_sources={"saramin"},
    )
    syncer.apply(plan)
    assert calls == []


def test_blocked_plan_is_not_applied():
    calls = []
    syncer = CalendarSync(FakeService(calls), "cal@example.com")
    plan = SyncPlan(delete=[ExistingEvent("x" * 10, "saramin", "h")], blocked="너무 많이 지웁니다")
    outcome = syncer.apply(plan)
    assert outcome["applied"] is False
    assert calls == []


def test_force_overrides_the_block():
    calls = []
    syncer = CalendarSync(FakeService(calls), "cal@example.com")
    plan = SyncPlan(delete=[ExistingEvent("x" * 10, "saramin", "h")], blocked="너무 많이 지웁니다")
    outcome = syncer.apply(plan, force=True)
    assert outcome["applied"] is True
    assert [verb for verb, _ in calls] == ["delete"]


def test_dry_run_sends_nothing():
    calls = []
    syncer = CalendarSync(FakeService(calls), "cal@example.com", dry_run=True)
    syncer.apply(SyncPlan(create=[posting()]))
    assert calls == []


def test_conflict_on_insert_falls_back_to_patch():
    """409 는 '이미 있다' 다. 실패로 세지 말고 갱신으로 넘겨야 멱등성이 유지된다."""
    calls = []
    service = FakeService(calls, insert_error=FakeHttpError(409))
    syncer = CalendarSync(service, "cal@example.com")
    outcome = syncer.apply(SyncPlan(create=[posting()]))
    assert [verb for verb, _ in calls] == ["insert", "patch"]
    assert outcome["created"] == 0
    assert outcome["updated"] == 1
    assert outcome["failed"] == 0


def test_patch_body_omits_id():
    """patch 에 id 를 같이 보내면 거절된다."""
    calls = []
    syncer = CalendarSync(FakeService(calls), "cal@example.com")
    syncer.apply(SyncPlan(update=[posting()]))
    _, kwargs = calls[0]
    assert "id" not in kwargs["body"]
    assert kwargs["eventId"] == posting().event_id


def test_list_managed_filters_by_tag_and_reads_hash():
    calls = []
    target = posting()
    pages = [{
        "items": [{
            "id": target.event_id,
            "summary": "[PNP시큐어] 서버 응용 개발 — 마감",
            "extendedProperties": {"private": {
                "jobcal": "1", "source": "saramin", "contentHash": target.content_hash,
            }},
        }]
    }]
    syncer = CalendarSync(FakeService(calls, pages=pages), "cal@example.com")
    managed = syncer.list_managed()
    assert len(managed) == 1
    assert managed[0].source == "saramin"
    assert managed[0].content_hash == target.content_hash
