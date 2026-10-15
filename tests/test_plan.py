"""동기화 계획 테스트 — 이 프로젝트에서 가장 중요한 테스트다.

여기서 막는 사고는 '수집이 깨진 날 캘린더가 통째로 비워지는 것' 이다.
크롤링 기반 캘린더 동기화에서 가장 흔하고 가장 아픈 실패다.
"""
from datetime import date

from jobcal.calendar_sync import ExistingEvent, build_plan
from jobcal.models import Posting


def posting(posting_id: str, *, source="saramin", title="서버 개발") -> Posting:
    return Posting(
        source=source,
        posting_id=posting_id,
        company="PNP시큐어",
        title=title,
        url=f"https://example.com/{posting_id}",
        deadline=date(2026, 11, 1),
    )


def existing_for(p: Posting) -> ExistingEvent:
    return ExistingEvent(event_id=p.event_id, source=p.source, content_hash=p.content_hash)


def test_first_run_creates_everything():
    plan = build_plan([posting("1"), posting("2")], [], complete_sources={"saramin"})
    assert len(plan.create) == 2
    assert not plan.update and not plan.delete


def test_second_run_is_a_noop():
    """같은 입력으로 다시 돌리면 아무 호출도 나가지 않아야 한다 — 멱등성의 정의."""
    postings = [posting("1"), posting("2")]
    existing = [existing_for(p) for p in postings]
    plan = build_plan(postings, existing, complete_sources={"saramin"})
    assert plan.is_noop
    assert len(plan.unchanged) == 2


def test_changed_content_becomes_update_not_duplicate():
    before = posting("1")
    after = posting("1", title="서버 개발(경력 3년+)")
    plan = build_plan([after], [existing_for(before)], complete_sources={"saramin"})
    assert len(plan.update) == 1
    assert not plan.create      # 같은 event_id 니까 새로 만들지 않는다


def test_vanished_posting_is_deleted_when_source_is_trustworthy():
    gone = posting("1")
    plan = build_plan([], [existing_for(gone)], complete_sources={"saramin"})
    assert len(plan.delete) == 1


def test_vanished_posting_is_kept_when_source_failed():
    """수집이 전수를 못 가져온 소스의 일정은 지우지 않는다.

    안 보이는 것과 마감된 것은 다르다. 이 구분이 없으면 네트워크가 한 번 흔들린
    날 캘린더가 비워진다.
    """
    gone = posting("1")
    plan = build_plan([], [existing_for(gone)], complete_sources=set())
    assert not plan.delete
    assert len(plan.delete_skipped) == 1


def test_mass_deletion_is_blocked():
    """기존 일정의 3분의 1을 넘게 지우려 하면 멈춘다."""
    existing = [existing_for(posting(str(i))) for i in range(10)]
    survivors = [posting(str(i)) for i in range(5)]      # 5/10 = 50% 삭제
    plan = build_plan(survivors, existing, complete_sources={"saramin"})
    assert plan.blocked is not None
    assert "50%" in plan.blocked


def test_small_deletion_passes_the_guard():
    existing = [existing_for(posting(str(i))) for i in range(10)]
    survivors = [posting(str(i)) for i in range(8)]      # 2/10 = 20%
    plan = build_plan(survivors, existing, complete_sources={"saramin"})
    assert plan.blocked is None
    assert len(plan.delete) == 2


def test_guard_ignores_skipped_deletions():
    """삭제 보류분은 비율 계산에 들어가지 않아야 한다 — 지우지도 않는데 가드를 때리면 안 된다."""
    trustworthy = [existing_for(posting(str(i))) for i in range(2)]
    untrusted = [existing_for(posting(str(i), source="wanted")) for i in range(8)]
    # 수집 결과가 0건이고 saramin 만 전수 신뢰 — saramin 2건만 삭제 대상이 된다.
    # 보류분 8건까지 비율에 넣으면 100% 로 계산돼 가드에 걸려버린다.
    plan = build_plan([], trustworthy + untrusted, complete_sources={"saramin"})
    assert plan.blocked is None
    assert len(plan.delete) == 2
    assert len(plan.delete_skipped) == 8


def test_other_sources_do_not_collide():
    """같은 회사의 같은 직무라도 소스가 다르면 별개 일정이다 — 의도된 동작."""
    a = posting("1", source="saramin")
    b = posting("1", source="wanted")
    plan = build_plan([a, b], [], complete_sources={"saramin", "wanted"})
    assert len(plan.create) == 2
