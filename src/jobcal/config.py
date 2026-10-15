"""설정 로드 — 관심기업 목록은 파일, 자격증명은 환경변수.

자격증명을 설정 파일에 두지 않는 것은 벤더 레포의 규칙("profile.json 은 commit
대상이므로 key·토큰·쿠키는 별도로")을 그대로 가져온 것이다. config/ 는 커밋되고
시크릿은 GitHub Secrets 에서 환경변수로 들어온다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SourceConfig:
    enabled: bool = False
    options: dict = field(default_factory=dict)


@dataclass
class Config:
    calendar_id: str
    companies: list[str]
    sources: dict[str, SourceConfig]
    max_delete_ratio: float = 0.34
    dry_run: bool = False

    @property
    def enabled_sources(self) -> list[str]:
        return [name for name, cfg in self.sources.items() if cfg.enabled]


def load_config(path: str | Path = "config/companies.yml") -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    companies = [str(c).strip() for c in (raw.get("companies") or []) if str(c).strip()]
    if not companies:
        raise ValueError(f"{path} 의 companies 가 비어 있습니다 — 관심기업을 적어주세요")

    sources = {
        name: SourceConfig(
            enabled=bool((block or {}).get("enabled", False)),
            options={k: v for k, v in (block or {}).items() if k != "enabled"},
        )
        for name, block in (raw.get("sources") or {}).items()
    }

    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID") or raw.get("calendar_id") or ""
    if not calendar_id:
        raise ValueError("GOOGLE_CALENDAR_ID 환경변수나 설정의 calendar_id 가 필요합니다")

    return Config(
        calendar_id=calendar_id,
        companies=companies,
        sources=sources,
        max_delete_ratio=float(raw.get("max_delete_ratio", 0.34)),
        dry_run=bool(raw.get("dry_run", False)),
    )


def build_calendar_service():
    """서비스 계정으로 Calendar service 를 만든다.

    개인 Gmail 캘린더도 그 캘린더를 서비스 계정 이메일에 '변경 및 공유 관리'
    권한으로 공유해두면 접근된다. OAuth refresh token 을 cron 에서 갱신하는
    문제를 아예 없애려고 이 방식을 쓴다.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/calendar.events"]

    blob = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if blob:
        info = json.loads(blob)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=scopes
        )
    else:
        key_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
        if not key_file:
            raise ValueError(
                "GOOGLE_SERVICE_ACCOUNT_JSON 또는 GOOGLE_SERVICE_ACCOUNT_FILE 이 필요합니다"
            )
        credentials = service_account.Credentials.from_service_account_file(
            key_file, scopes=scopes
        )

    return build("calendar", "v3", credentials=credentials, cache_discovery=False)
