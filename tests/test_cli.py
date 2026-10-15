"""엔드투엔드 — 수집부터 캘린더 호출까지 한 바퀴를 네트워크 없이 돌린다.

조각마다 테스트가 있어도 배선이 틀리면 운영에서야 드러난다. 여기서 보는 것은
로직이 아니라 '연결' 이다.
"""
from datetime import date, timedelta

import pytest

from jobcal import cli
from jobcal.models import Posting
from jobcal.sources.base import FetchResult


class FakeEvents:
    def __init__(self, recorder, items):
        self.recorder, self.items = recorder, items

    def list(self, **kw):
        return _Static({"items": self.items})

    def insert(self, **kw):
        self.recorder.append(("insert", kw["body"]["summary"]))
        return _Static({})

    def patch(self, **kw):
        self.recorder.append(("patch", kw["eventId"]))
        return _Static({})

    def delete(self, **kw):
        self.recorder.append(("delete", kw["eventId"]))
        return _Static({})


class _Static:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeService:
    def __init__(self, recorder, items=()):
        self._events = FakeEvents(recorder, list(items))

    def events(self):
        return self._events


def posting(posting_id="1", **kw) -> Posting:
    base = dict(
        source="saramin",
        posting_id=posting_id,
        company="PNP시큐어",
        title="서버 응용 개발",
        url=f"https://example.com/{posting_id}",
        deadline=date.today() + timedelta(days=15),
    )
    base.update(kw)
    return Posting(**base)


class StubSource:
    name = "saramin"

    def __init__(self, result: FetchResult):
        self.result = result

    def fetch(self, companies):
        return self.result


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """설정 파일과 가짜 캘린더를 꽂아 cli.main 을 돌릴 수 있게 만든다."""
    config = tmp_path / "companies.yml"
    config.write_text(
        "calendar_id: cal@example.com\n"
        "companies: [PNP시큐어]\n"
        "max_delete_ratio: 0.34\n"
        "sources:\n  saramin:\n    enabled: true\n",
        encoding="utf-8",
    )
    calls: list[tuple] = []

    def install(result: FetchResult, existing_items=()):
        monkeypatch.setattr(cli, "build_sources", lambda cfg: [StubSource(result)])
        monkeypatch.setattr(
            cli, "build_calendar_service", lambda: FakeService(calls, existing_items)
        )
        return calls

    return config, install


def managed_item(p: Posting) -> dict:
    return {
        "id": p.event_id,
        "summary": p.summary,
        "extendedProperties": {"private": {
            "jobcal": "1", "source": p.source, "contentHash": p.content_hash,
        }},
    }


def run(config, extra=()) -> int:
    return cli.main(["--config", str(config), "--report", "/dev/null", *extra])


def test_first_run_creates_events(wired):
    config, install = wired
    calls = install(FetchResult(source="saramin", postings=[posting("1"), posting("2")]))
    assert run(config) == cli.EXIT_OK
    assert [verb for verb, _ in calls] == ["insert", "insert"]


def test_rerun_with_same_data_sends_nothing(wired):
    """멱등성 — 배선까지 포함해서 확인한다."""
    config, install = wired
    postings = [posting("1"), posting("2")]
    calls = install(
        FetchResult(source="saramin", postings=postings),
        existing_items=[managed_item(p) for p in postings],
    )
    assert run(config) == cli.EXIT_OK
    assert calls == []


def test_broken_deadline_stops_before_touching_calendar(wired):
    """검증 실패면 캘린더에 손도 대지 않는다."""
    config, install = wired
    calls = install(FetchResult(source="saramin", postings=[posting("1", deadline=date(1970, 1, 2))]))
    assert run(config) == cli.EXIT_VALIDATION
    assert calls == []


def test_total_collection_failure_stops(wired):
    """모든 소스가 실패하면 0건을 '전부 마감' 으로 읽지 않고 멈춘다."""
    config, install = wired
    failed = FetchResult(source="saramin").fail("네트워크 장애")
    calls = install(failed, existing_items=[managed_item(posting("1"))])
    assert run(config) == cli.EXIT_BLOCKED
    assert calls == []


def test_mass_deletion_is_blocked_end_to_end(wired):
    """공고가 절반 넘게 사라져 보이면 멈춘다 — 수집이 깨진 쪽이 흔하다."""
    config, install = wired
    existing = [managed_item(posting(str(i))) for i in range(10)]
    calls = install(
        FetchResult(source="saramin", postings=[posting("0"), posting("1")]),
        existing_items=existing,
    )
    assert run(config) == cli.EXIT_BLOCKED
    assert not [verb for verb, _ in calls if verb == "delete"]


def test_force_applies_the_blocked_deletion(wired):
    config, install = wired
    existing = [managed_item(posting(str(i))) for i in range(10)]
    calls = install(
        FetchResult(source="saramin", postings=[posting("0"), posting("1")]),
        existing_items=existing,
    )
    assert run(config, ["--force"]) == cli.EXIT_OK
    assert len([verb for verb, _ in calls if verb == "delete"]) == 8


def test_undated_postings_never_reach_the_calendar(wired):
    """상시채용은 CSV 에만 남는다 — 발견일을 마감일로 꽂지 않는다."""
    config, install = wired
    calls = install(FetchResult(source="saramin", postings=[posting("1", deadline=None)]))
    assert run(config) == cli.EXIT_OK
    assert calls == []


def test_dry_run_plans_but_does_not_write(wired):
    config, install = wired
    calls = install(FetchResult(source="saramin", postings=[posting("1")]))
    assert run(config, ["--dry-run"]) == cli.EXIT_OK
    assert calls == []
