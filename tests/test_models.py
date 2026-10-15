from datetime import date

from jobcal.models import Posting, dedupe


def make(**kw) -> Posting:
    base = dict(
        source="saramin",
        posting_id="12345",
        company="PNP시큐어",
        title="서버 응용 개발",
        url="https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=12345",
        deadline=date(2026, 11, 1),
    )
    base.update(kw)
    return Posting(**base)


def test_event_id_is_valid_base32hex():
    """Calendar 는 'a'~'v' 와 '0'~'9' 만, 길이 5~1024 만 받는다."""
    event_id = make().event_id
    assert 5 <= len(event_id) <= 1024
    assert set(event_id) <= set("abcdefghijklmnopqrstuv0123456789")


def test_event_id_is_stable_across_content_changes():
    """제목이나 마감일이 바뀌어도 같은 공고는 같은 일정이어야 한다.

    이게 깨지면 공고 제목이 수정될 때마다 새 일정이 생기고 옛 일정이 남는다 —
    중복 일정이 쌓이는 경로가 정확히 이것이다.
    """
    original = make()
    renamed = make(title="서버 응용 개발(경력)", deadline=date(2026, 12, 25))
    assert original.event_id == renamed.event_id


def test_event_id_differs_by_source_and_posting():
    assert make().event_id != make(posting_id="99999").event_id
    assert make().event_id != make(source="wanted").event_id


def test_content_hash_tracks_visible_fields():
    """일정 본문에 보이는 값이 바뀌면 해시도 바뀌어야 patch 가 발생한다."""
    original = make()
    assert original.content_hash == make().content_hash
    assert original.content_hash != make(title="다른 직무").content_hash
    assert original.content_hash != make(deadline=date(2026, 12, 1)).content_hash
    assert original.content_hash != make(location="서울 > 송파구").content_hash


def test_content_hash_ignores_invisible_fields():
    """일정에 안 나타나는 값 때문에 매일 patch 가 나가면 로그가 쓸모없어진다."""
    assert make().content_hash == make(posted_at=date(2026, 1, 1)).content_hash


def test_dedupe_collapses_repeated_postings():
    """offset 페이징이 겹쳐 같은 공고가 두 번 들어오는 경우를 막는다."""
    collapsed = dedupe([make(), make(), make(posting_id="67890")])
    assert len(collapsed) == 2


def test_to_row_exposes_ordinal_for_range_check():
    row = make().to_row()
    assert row["deadline_ordinal"] == date(2026, 11, 1).toordinal()
    assert make(deadline=None).to_row()["deadline_ordinal"] is None
