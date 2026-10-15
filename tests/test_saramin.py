"""사람인 응답 파싱 테스트.

타임존이 이 파일의 주제다. 한국 공고 마감은 거의 'KST 23:59' 인데, 그 순간의 UTC
날짜는 하루 전이다. UTC 로 변환하면 모든 마감 일정이 하루씩 앞으로 밀리고,
그걸 보고 지원 계획을 세우면 실제로 하루를 잃는다.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from jobcal.sources.saramin import KST, SaraminSource, _to_kst_date


def kst_timestamp(year, month, day, hour=23, minute=59) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=KST).timestamp())


def source() -> SaraminSource:
    return SaraminSource(access_key="test-key")


def test_late_night_kst_deadline_keeps_its_own_date():
    """가장 흔한 마감 시각 23:59 KST 가 그날로 읽히는지."""
    assert _to_kst_date(kst_timestamp(2026, 11, 1, 23, 59)) == date(2026, 11, 1)


def test_early_morning_kst_does_not_slip_to_the_previous_day():
    """날짜가 실제로 밀리는 구간은 KST 자정~오전 9시다.

    GitHub Actions 러너는 UTC 로 돈다. tz 없이 fromtimestamp 를 쓰면 이 구간의
    타임스탬프가 전날로 읽히고, 마감 일정이 하루 앞에 꽂힌다. KST 를 명시해야
    하는 이유가 23:59 가 아니라 이쪽이다.
    """
    stamp = kst_timestamp(2026, 11, 1, 0, 30)
    assert _to_kst_date(stamp) == date(2026, 11, 1)
    # UTC 로 읽으면 전날이 된다 — 우리가 피한 바로 그 결과
    assert datetime.fromtimestamp(stamp, tz=ZoneInfo("UTC")).date() == date(2026, 10, 31)


def test_always_open_posting_has_no_deadline():
    """상시채용은 아주 먼 만료 시각으로 온다 — 2년 뒤 일정은 쓸모가 없으니 날짜 없음으로."""
    far = kst_timestamp(date.today().year + 3, 1, 1)
    assert _to_kst_date(far) is None


def test_posted_at_ignores_the_horizon():
    """게시일은 과거라 지평선 검사를 적용하지 않는다(horizon_days=None)."""
    stamp = kst_timestamp(2026, 1, 15, 9, 0)
    assert _to_kst_date(stamp, horizon_days=None) == date(2026, 1, 15)


def test_garbage_timestamps_become_none():
    for value in (None, "", 0, "0", -1, "없음", "abc"):
        assert _to_kst_date(value) is None


def test_parse_job_maps_fields():
    raw = {
        "id": "48123456",
        "url": "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=48123456",
        "active": "1",
        "company": {"detail": {"name": "피앤피시큐어"}},
        "position": {
            "title": "서버 응용 개발자",
            "location": {"name": "서울 > 송파구"},
            "job-code": {"name": "서버개발자"},
        },
        "expiration-timestamp": str(kst_timestamp(2026, 11, 1)),
        "posting-timestamp": str(kst_timestamp(2026, 10, 1, 9, 0)),
    }
    posting = source()._parse_job(raw, "PNP시큐어")
    assert posting is not None
    assert posting.posting_id == "48123456"
    assert posting.company == "피앤피시큐어"       # 질의어가 아니라 실제 회사명을 쓴다
    assert posting.title == "서버 응용 개발자"
    assert posting.deadline == date(2026, 11, 1)
    assert posting.location == "서울 > 송파구"
    assert posting.active is True
    assert posting.extra["queried_company"] == "PNP시큐어"


def test_closed_posting_is_marked_inactive():
    """active 는 문자열 '0' 으로 온다 — 정수 비교로 짜면 조용히 열린 공고가 된다."""
    raw = {"id": "1", "active": "0", "position": {"title": "x"}, "url": "u"}
    assert source()._parse_job(raw, "회사").active is False


def test_missing_id_is_dropped():
    assert source()._parse_job({"position": {"title": "x"}}, "회사") is None


def test_company_name_falls_back_to_query():
    raw = {"id": "1", "active": "1", "position": {"title": "x"}, "url": "u"}
    assert source()._parse_job(raw, "PNP시큐어").company == "PNP시큐어"


def test_empty_company_list_is_not_trusted():
    """0건과 '수집 실패' 를 구분하는 그 플래그가 실제로 내려가는지 본다."""
    result = source().fetch([])
    assert result.complete is False
    assert result.errors
