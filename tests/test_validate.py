"""검증 게이트 테스트 — 벤더링한 validate_values 가 캘린더 앞에서 제 역할을 하는지."""
from datetime import date, timedelta

from jobcal.models import Posting
from jobcal.validate import calendar_ready, validate


def posting(**kw) -> Posting:
    base = dict(
        source="saramin",
        posting_id="1",
        company="PNP시큐어",
        title="서버 응용 개발",
        url="https://example.com/1",
        deadline=date.today() + timedelta(days=20),
    )
    base.update(kw)
    return Posting(**base)


def test_healthy_batch_passes():
    issues, _ = validate([posting(), posting(posting_id="2")])
    assert issues == []


def test_empty_title_is_caught():
    issues, _ = validate([posting(title="")])
    assert any("title" in i for i in issues)


def test_epoch_zero_style_breakage_is_caught():
    """타임스탬프 단위를 틀리면(밀리초를 초로 읽으면) 1970년이 나온다."""
    issues, _ = validate([posting(deadline=date(1970, 1, 2))])
    assert any("deadline_ordinal" in i for i in issues)


def test_absurdly_far_deadline_is_caught():
    """반대로 초를 밀리초로 읽으면 수천 년 뒤가 나온다."""
    issues, _ = validate([posting(deadline=date.today() + timedelta(days=5000))])
    assert any("deadline_ordinal" in i for i in issues)


def test_undated_postings_do_not_trip_the_deadline_rule():
    """원티드처럼 상시채용이 대부분인 소스는 deadline 이 전건 None 인 게 정상이다."""
    issues, _ = validate([posting(source="wanted", deadline=None)])
    assert issues == []


def test_contact_email_raises_a_pii_warning():
    """공고 본문에 담당자 이메일이 섞여 들어오는 경로가 실제로 있다."""
    _, warnings = validate([posting(title="서버 개발 (문의 hr@example.com)")])
    assert any("이메일" in w for w in warnings)


def test_calendar_ready_filters_undated_and_closed():
    batch = [
        posting(posting_id="1"),
        posting(posting_id="2", deadline=None),      # 상시채용 — 일정 없음
        posting(posting_id="3", active=False),       # 마감됨 — 일정 없음
    ]
    ready = calendar_ready(batch)
    assert [p.posting_id for p in ready] == ["1"]
