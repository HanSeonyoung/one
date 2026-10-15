"""python -m jobcal — 수집 → 검증 → 캘린더 동기화 한 바퀴.

무인 cron 에서 돌기 때문에 모든 분기는 '물어보지 않고 멈추거나 진행' 으로 끝나야
한다. 벤더 레포의 확인 게이트(차단 만나면 사용자에게 묻기)는 여기서 '중단하고
0 아닌 종료코드로 알리기' 로 바뀐다. 물어볼 사람이 없는데 묻는 자리를 두면
결국 조용히 진행되는 쪽으로 기울기 때문이다.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from .calendar_sync import CalendarSync, build_plan
from .config import build_calendar_service, load_config
from .models import dedupe
from .sources.base import FetchResult
from .validate import calendar_ready, validate

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
)
logger = logging.getLogger("jobcal")

EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_BLOCKED = 3
EXIT_NO_SOURCE = 4


def build_sources(config) -> list:
    import os

    built = []
    for name in config.enabled_sources:
        options = dict(config.sources[name].options)
        if name == "saramin":
            from .sources.saramin import SaraminSource

            built.append(SaraminSource(
                access_key=os.environ.get("SARAMIN_ACCESS_KEY", ""), **options
            ))
        elif name == "wanted":
            from .sources.wanted import WantedSource

            built.append(WantedSource(**options))
        else:
            logger.warning("알 수 없는 소스 '%s' — 건너뜁니다", name)
    return built


def write_report(postings, path: Path) -> None:
    """수집 결과를 CSV 로 남긴다 — 캘린더에 안 들어간 건(상시채용)도 여기엔 남는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [p.to_row() for p in postings]
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobcal", description="관심기업 채용공고 → Google Calendar")
    parser.add_argument("--config", default="config/companies.yml")
    parser.add_argument("--dry-run", action="store_true", help="계획만 출력하고 캘린더는 안 건드린다")
    parser.add_argument("--force", action="store_true", help="대량삭제 가드를 무시한다")
    parser.add_argument("--report", default="output/postings.csv")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    sources = build_sources(config)
    if not sources:
        logger.error("활성 소스가 없습니다 — config 의 sources 를 확인하세요")
        return EXIT_NO_SOURCE

    # ── 수집 ──────────────────────────────────────────────────────────────
    results: list[FetchResult] = []
    for source in sources:
        logger.info("수집 시작 — %s (관심기업 %d곳)", source.name, len(config.companies))
        result = source.fetch(config.companies)
        for error in result.errors:
            logger.warning("[%s] %s", source.name, error)
        logger.info(
            "수집 종료 — %s: %d건 (전수신뢰=%s)", source.name, len(result.postings), result.complete
        )
        results.append(result)

    all_postings = dedupe([p for r in results for p in r.postings])
    complete_sources = {r.source for r in results if r.complete}

    if not all_postings and not complete_sources:
        logger.error("모든 소스가 실패했습니다 — 캘린더를 건드리지 않고 종료합니다")
        return EXIT_BLOCKED

    write_report(all_postings, Path(args.report))

    # ── 검증 ──────────────────────────────────────────────────────────────
    issues, warnings = validate(all_postings)
    for warning in warnings:
        logger.warning("PII 경고 — %s", warning)
    if issues:
        for issue in issues:
            logger.error("검증 실패 — %s", issue)
        logger.error("캘린더를 건드리지 않고 종료합니다 — 잘못된 일정이 들어가는 것이 더 나쁩니다")
        return EXIT_VALIDATION

    desired = calendar_ready(all_postings)
    undated = len(all_postings) - len(desired)
    if undated:
        logger.info("마감일 없는 공고 %d건은 일정에서 제외했습니다 (CSV 에는 남습니다)", undated)

    # ── 동기화 ────────────────────────────────────────────────────────────
    dry_run = args.dry_run or config.dry_run
    service = build_calendar_service()
    syncer = CalendarSync(service, config.calendar_id, dry_run=dry_run)
    existing = syncer.list_managed()

    plan = build_plan(
        desired,
        existing,
        complete_sources=complete_sources,
        max_delete_ratio=config.max_delete_ratio,
    )
    logger.info("동기화 계획 — %s", plan.summary_line())
    if plan.delete_skipped:
        logger.info(
            "삭제 보류 %d건 — 해당 소스가 이번 실행에서 전수를 가져오지 못했습니다",
            len(plan.delete_skipped),
        )

    outcome = syncer.apply(plan, force=args.force)
    if not outcome.get("applied") and plan.blocked and not args.force:
        return EXIT_BLOCKED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
