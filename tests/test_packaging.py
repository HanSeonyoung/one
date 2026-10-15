"""패키징 회귀 테스트 — 설치물에 벤더 모듈이 실제로 들어갔는지.

이 파일이 생긴 이유: 처음에는 벤더 모듈을 레포 루트 vendor/ 에 두고
__file__ 기준 상대경로를 sys.path 에 넣었다. editable 설치에서는 통했고 테스트도
전부 통과했지만, Codespaces 에서 일반 설치로 돌리자 터졌다.

    File ".../site-packages/jobcal/sources/base.py", line 18
    from crawler_utils import BudgetExceeded, RateLimiter, check_robots
    ModuleNotFoundError: No module named 'crawler_utils'

테스트가 소스 트리를 직접 import 하고 있어서 '설치했을 때' 를 아무도 안 봤다.
그래서 pytest 의 pythonpath 를 없애 설치된 패키지를 쓰게 하고, 이 파일을 뒀다.
"""
from importlib import resources

import pytest


def test_vendored_module_is_a_package_submodule():
    """패키지 안에서 import 돼야 한다 — sys.path 조작에 의존하면 안 된다."""
    from jobcal.vendor import crawler_utils

    assert crawler_utils.__name__ == "jobcal.vendor.crawler_utils"


def test_vendored_helpers_are_the_ones_we_rely_on():
    from jobcal.vendor.crawler_utils import (
        BudgetExceeded,
        RateLimiter,
        check_robots,
        detect_pii,
        validate_values,
    )

    assert issubclass(BudgetExceeded, Exception)
    assert RateLimiter(delay=0.0).delay >= RateLimiter.MIN_DELAY   # 하한이 살아 있는지
    assert callable(check_robots) and callable(detect_pii) and callable(validate_values)


def test_license_ships_with_the_package():
    """MIT 라이선스 전문이 설치물에 함께 들어가야 한다 — package-data 설정 확인."""
    text = resources.files("jobcal.vendor").joinpath("LICENSE.web-crawler").read_text(
        encoding="utf-8"
    )
    assert "MIT" in text


def test_wanted_fingerprint_ships_with_the_package():
    import json

    raw = resources.files("jobcal.vendor").joinpath(
        "fingerprints/wanted_co_kr/profile.json"
    ).read_text(encoding="utf-8")
    profile = json.loads(raw)
    assert profile["domain"] == "wanted.co.kr"


def test_entry_module_imports_without_path_tricks():
    """python -m jobcal 이 밟는 import 사슬을 그대로 따라가 본다.

    cli → sources.base → vendor.crawler_utils 가 전부 패키지 상대 import 로
    풀려야 한다. 여기서 터지면 설치 환경에서도 터진다.
    """
    import jobcal.cli
    import jobcal.sources.base
    import jobcal.sources.saramin
    import jobcal.sources.wanted
    import jobcal.validate

    assert callable(jobcal.cli.main)


def test_no_module_reaches_for_top_level_crawler_utils():
    """최상위 crawler_utils 를 import 하는 코드가 남아 있지 않은지 소스에서 확인."""
    import jobcal

    package_root = resources.files(jobcal)
    offenders = []
    for path in package_root.rglob("*.py"):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import crawler_utils", "from crawler_utils")):
                offenders.append(f"{path.name}: {stripped}")
    assert offenders == [], f"벤더 모듈을 최상위로 import 하는 곳이 남아 있습니다: {offenders}"


def test_sys_path_is_not_mutated_by_import():
    """import 부수효과로 sys.path 를 건드리지 않아야 한다."""
    import sys

    before = list(sys.path)
    import jobcal.sources.base  # noqa: F401

    assert sys.path == before


@pytest.mark.parametrize("name", ["jobcal.models", "jobcal.config", "jobcal.calendar_sync"])
def test_core_modules_import_standalone(name):
    """벤더 모듈 없이도 import 되는 층은 그대로 유지한다 — 의존 방향 확인."""
    __import__(name)
