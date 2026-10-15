# jobcal-sync

관심기업 채용공고의 **마감일을 Google Calendar 에 멱등 동기화**한다. 매일 한 번 돌면서 새 공고는 일정으로 만들고, 내용이 바뀐 공고는 갱신하고, 마감된 공고는 지운다. 같은 입력으로 두 번 돌려도 아무 일도 일어나지 않는다.

```
관심기업 목록 ──▶ 수집 어댑터 ──▶ 정규화(Posting) ──▶ 검증 ──▶ Calendar 동기화
 (config)         사람인 API         멱등 키 부여      쓰기 전 게이트   insert/patch/delete
                  원티드 API                                            + 대량삭제 가드
```

## CSV 가져오기를 자동화하지 않은 이유

Google Calendar 설정의 "CSV 가져오기" 는 UI 전용 흐름이라 대응하는 API 가 없고, 무엇보다 **생성만** 한다. 같은 파일을 두 번 넣으면 일정이 전부 중복되고, 기존 일정을 수정하거나 마감된 공고를 지우는 개념이 없다. "업데이트 자동화" 라는 요구와 정면으로 충돌한다.

그래서 CSV 를 캘린더에 밀어넣지 않는다. 수집 결과를 **원하는 상태(desired state)** 로 보고, 실행마다 캘린더의 현재 상태와 비교해 차이만 반영한다. CSV 는 사람이 보는 리포트로만 남는다(`output/postings.csv`).

## 설계 핵심 셋

**1. event id 를 우리가 정한다.** Calendar API 는 클라이언트 지정 id(base32hex)를 받는다. `sha1(source:posting_id)` 를 id 로 쓰면 `insert` 가 409 를 돌려주는 것만으로 "이미 있다" 가 판정된다. 별도 상태 DB 나 커밋되는 state 파일이 필요 없고 **캘린더 자체가 상태**가 된다. id 는 공고 식별자만으로 만들고 제목·마감일은 넣지 않는다 — 공고 제목이 수정될 때마다 새 일정이 생기고 옛 일정이 남는 게 중복의 주 경로다.

**2. 우리가 만든 일정만 건드린다.** `extendedProperties.private.jobcal = "1"` 로 태그하고 조회도 그 태그로만 한다. 사용자가 손으로 넣은 일정은 이 코드에 보이지 않는다.

**3. 삭제는 전수를 믿을 수 있는 소스에 대해서만 한다.** 이게 가장 중요하다. 수집이 반쯤 실패해 0건이 돌아온 걸 "전부 마감" 으로 읽으면 캘린더가 통째로 비워진다. 크롤링 기반 동기화에서 가장 흔하고 가장 아픈 실패다. 그래서 어댑터는 공고 목록과 함께 `complete` 플래그를 돌려주고, 내려간 소스의 일정은 삭제 대상에서 아예 빠진다. 그 위에 **한 실행에서 기존 일정의 34% 넘게 지우려 하면 중단**하는 가드를 한 겹 더 뒀다. 공고가 하루에 3분의 1 넘게 한꺼번에 마감되는 일보다 수집이 깨진 쪽이 흔하다.

## 수집 소스

**사람인 공식 Open API (1차, 기본 활성).** 두 필드 때문에 이쪽이 먼저다. `active` 가 공고의 열림·닫힘을 직접 알려주고 `expiration-timestamp` 가 마감일을 준다. 크롤링으로는 "공고가 사라졌다" 와 "요청이 실패했다" 가 같은 모양(0건)으로 보이는데, 이 API 는 둘을 구분해 준다. 그리고 `keywords` 로 **회사별로 좁혀 질의**할 수 있어서 전체를 긁어와 걸러내지 않는다 — 관심기업 20곳이면 하루 20회 호출이고, 1일 상한 500회에 여유가 크다.

access-key 는 이용신청 → 승인 → 앱 등록 순서다. 개인 신청을 배제하는 문구는 없지만 승인제이므로 신청해 봐야 확정된다. 용도가 애매하면 api@saramin.co.kr 에 먼저 물어보는 쪽이 빠르다.

**원티드 공개 JSON API (2차, 기본 비활성).** 실측해 보면 이 엔드포인트는 CloudFront + AWS WAF 뒤에 있고 `403 ERROR / Request blocked` 로 거절된다. AWS 문서는 이 메시지를 **WAF 규칙이 요청을 의도적으로 차단한 경우**로 못 박는다 — 오리진 장애나 설정 누락이 아니다. 데이터센터 IP 대역은 WAF 가 흔히 막는 대상이라 GitHub Actions·Codespaces 에서는 켜도 거의 통하지 않는다.

코드는 이 상황을 일시적 오류로 흘리지 않는다. 차단을 만나면 `AutomatedAccessBlocked` 로 즉시 멈추고(페이지를 더 넘기지 않는다), 누가 막았는지를 로그에 적고, 그 소스의 일정은 마감 판정에서 제외한다. 자동 우회는 하지 않는다 — 약관 위반과 기술적 우회는 층이 다른 문제이고, 무인 cron 이 그 판단을 대신할 자리가 아니다. 데이터가 꼭 필요하면 원티드에 직접 문의하는 쪽이 맞는 경로다.

켜기 전에 `robots.txt` 와 이용약관을 직접 확인하고 `acknowledged_robots` 를 `true` 로 바꿔야 돈다. 이 코드가 대신 판단하지 않는다. 비활성이 기본값인 데는 기술적 이유도 있다 — 회사명으로 좁혀 질의할 수 없어서 목록을 페이징하며 클라이언트에서 걸러야 하고, 그건 필요한 것만 가져오는 게 아니라 전체를 받아놓고 버리는 모양이다. 페이지 예산이 먼저 떨어지면 `complete=False` 로 내려 삭제 판정에서 제외한다. 상시채용이 대부분이라 마감일 없는 공고가 많고, 그건 일정을 만들지 않고 CSV 에만 남는다.

**잡코리아는 넣지 않았다.** API 가 있지만 "공공기관 또는 학교를 대상으로 제공하는 서비스" 로 명시돼 있고 개인은 심사 후 거절될 수 있다. 공채달력은 iframe 임베드라 데이터가 아니다. 결정적으로 **IP 주소를 등록해 호출 링크를 받는 방식**이어서, GitHub Actions 러너처럼 IP 가 매번 바뀌는 환경에서는 승인을 받아도 쓸 수 없다.

## 레포 배치 — 먼저 확인할 것

`.github/`, `src/`, `config/`, `pyproject.toml` 이 **레포 루트에 바로** 있어야 한다. 한 단계 아래(`<repo>/jobcal-sync/.github/...`)에 있으면 GitHub 이 워크플로를 아예 등록하지 않아 Actions 탭이 빈 채로 남는다. 그런데도 로컬 `pytest` 는 그 폴더 안에서 돌면 통과하므로, "로컬은 되는데 Actions 만 조용하다" 가 이 배치 문제의 전형적인 증상이다.

```bash
git ls-files | grep '\.github/'
# 기대: .github/workflows/ci.yml
#       .github/workflows/sync.yml
# 잘못됨: jobcal-sync/.github/workflows/sync.yml
```

워크플로는 둘로 나뉘어 있다. `ci.yml` 은 **푸시마다** 돌고 시크릿이 필요 없어서 사람인 승인을 기다리는 동안에도 초록불이 뜬다. `sync.yml` 은 하루 한 번(cron)과 수동 실행(`Run workflow`)만 돈다. `schedule` 과 `Run workflow` 버튼은 둘 다 **기본 브랜치에 있는 워크플로만** 적용되므로, 기능 브랜치에 두면 영원히 돌지 않는다.

### cron 에 대해 알아둘 것

`schedule` 은 GitHub Actions 의 정식 이벤트다. 다만 문서가 명시한 제약이 있다. 기본 브랜치에서만 발동하고, 최소 간격은 5분이고, 지정이 없으면 UTC 로 해석된다(이 레포는 `timezone: "Asia/Seoul"` 을 명시해 환산을 없앴다). 매시 정각에 부하가 몰려 실행이 밀리거나 심하면 큐의 작업이 버려질 수 있어서 분을 정각에서 띄우라는 것이 공식 권고이고, 그래서 `:17` 로 뒀다. public 레포에서는 60일간 활동이 없으면 자동 비활성화되므로 그때는 수동으로 다시 켜야 한다.

즉 "정확히 그 시각" 을 보장하는 스케줄러가 아니다. 마감일 캘린더에는 그래도 충분하다 — 하루 한 번 돌아 마감 7일 전과 1일 전 알림을 걸어두는 용도이고, 실행이 30분 밀려도 결과가 같다. 분 단위 정확성이 필요한 작업이라면 Actions cron 은 맞는 도구가 아니다.

## 설정

### 1. 캘린더와 서비스 계정 연결

OAuth refresh token 을 cron 에서 갱신하는 문제를 없애려고 서비스 계정을 쓴다. 개인 Gmail 캘린더도 **그 캘린더를 서비스 계정 이메일에 공유**해두면 접근된다.

1. Google Cloud 콘솔에서 프로젝트를 만들고 **Google Calendar API** 를 활성화한다.
2. 서비스 계정을 만들고 JSON 키를 발급받는다.
3. Google Calendar 에서 대상 캘린더의 **설정 > 특정 사용자와 공유** 에 서비스 계정 이메일(`...iam.gserviceaccount.com`)을 추가하고 권한을 **"변경 및 공유 관리"** 로 준다.
4. 같은 설정 화면의 **캘린더 통합** 에서 캘린더 ID 를 복사한다.

3번을 빼먹으면 인증은 되는데 404 가 난다 — 서비스 계정에게 그 캘린더가 존재하지 않는 상태다.

### 2. 관심기업과 시크릿

관심기업은 `config/companies.yml` 에 적는다(커밋된다). 자격증명은 환경변수로만 들어온다.

| 환경변수 | 용도 |
| --- | --- |
| `GOOGLE_CALENDAR_ID` | 대상 캘린더 ID |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 서비스 계정 키 JSON 전문 |
| `SARAMIN_ACCESS_KEY` | 사람인 Open API access-key |
| `SLACK_WEBHOOK_URL` | (선택) 실패 알림 |

GitHub Actions 에서는 같은 이름의 Secret 으로 넣는다.

## 실행

```bash
pip install -e '.[dev]'

# 처음에는 반드시 dry-run 으로. 계획만 로그에 찍고 캘린더는 건드리지 않는다.
python -m jobcal --dry-run

# 확인했으면 실제 적용
python -m jobcal

# 대량삭제 가드에 걸렸고, 확인 결과 정말 마감된 것이라면
python -m jobcal --force
```

종료 코드는 무인 실행에서 상태를 구분하려고 나눠 뒀다.

| 코드 | 의미 | 해야 할 일 |
| --- | --- | --- |
| 0 | 정상 | 없음 |
| 2 | 검증 실패 — 수집 값이 그럴듯하지 않다 | 로그의 필드명을 보고 파싱 확인. 캘린더는 안 건드렸다 |
| 3 | 대량삭제 차단 또는 전 소스 실패 | 공고가 정말 마감됐는지 확인 후 `--force` |
| 4 | 활성 소스 없음 | `config` 의 `sources` 확인 |

Docker 로도 돌아간다. 기본 CMD 가 `--dry-run` 이라 자격증명 없이 실행해도 캘린더가 바뀌지 않는다.

```bash
docker build -t jobcal-sync .
docker run --rm --env-file .env jobcal-sync          # dry-run
docker run --rm --env-file .env jobcal-sync --help   # 인자를 주면 덮어쓴다
```

## 무인 실행에서 바꾼 것

수집 모듈을 가져온 [web-crawler](https://github.com/byungjunjang/web-crawler) 는 **자동 접근 차단을 만나면 사용자에게 한 번 묻는** 게 핵심 안전장치다. cron 에는 물어볼 사람이 없다. 그래서 이 프로젝트는 그 게이트를 **"묻기" 가 아니라 "중단하고 0 아닌 종료코드로 알리기"** 로 바꿨다. 사다리 4단 이상(curl_cffi 그리드·StealthyFetcher·Chrome CDP)은 아예 쓰지 않는다. 물어볼 자리를 무인 환경에 그대로 두면 결국 조용히 진행되는 쪽으로 기울기 때문이다.

같은 이유로 `scrapling[fetchers]` 는 선택 의존성(`.[crawl]`)으로 뺐다. 사람인·원티드는 평문 JSON 이라 Chromium 이 필요 없고, CI 가 매번 수백 MB 를 받지 않는다.

## 한계

- 사람인 API 는 베타라 인터페이스가 바뀔 수 있다. 파싱은 `_parse_job` 한 곳에 모아 뒀다.
- 상시채용 공고는 마감일이 없어 일정이 생기지 않는다. CSV 리포트에는 남는다.
- 회사명 질의는 `keywords` 기반이라 동명 기업이나 자회사가 섞여 들어올 수 있다. `config` 의 회사명을 공고에 실제로 찍히는 상호로 맞추는 쪽이 정확하다.
- 캘린더 한 개를 전제한다. 직무별로 나누려면 `calendar_id` 를 소스·직무별로 분리해야 한다.

## 출처 표기

채용 데이터 중 사람인에서 온 것: **Powered by 취업 사람인** — https://www.saramin.co.kr

이 프로젝트는 **개인이 만든 비공식 도구**이며 (주)사람인과 제휴·후원·제작 관계가 없다. 사람인이 공개한 Open API 를 이용신청·승인 절차를 거쳐 사용할 뿐이다. 공고 데이터를 재배포하거나 상업적으로 이용하지 않고, 공고 상세는 캘린더 일정 본문의 사람인 원문 URL 로 연결한다 — 사람인 API 이용자 제한사항(일 500회 상한, 재판매·대가 청구 금지, 제공주체 및 제휴관계 오인 행위 금지)을 따른다.

access-key 는 이용신청 승인 후 1년이 보유·이용기간이므로, 1년 뒤 갱신이 필요한지 확인해 두는 편이 좋다.

`src/jobcal/vendor/crawler_utils.py` 는 [byungjunjang/web-crawler](https://github.com/byungjunjang/web-crawler) 의 `scripts/utils.py` 를 MIT 라이선스로 그대로 가져온 것이다(원문 라이선스는 같은 디렉터리의 `LICENSE.web-crawler`). `RateLimiter`/`BudgetExceeded`(요청 간격·총량·연속실패 상한), `check_robots()`(허용과 확인 실패를 구분하는 robots 판정), `detect_pii()`(담당자명·이메일 유입 감지), `validate_values()`(값 타입·범위·빈값·중복률 검사)를 쓴다. `src/jobcal/vendor/fingerprints/wanted_co_kr/profile.json` 도 같은 레포에서 가져왔다.

벤더 모듈을 레포 루트가 아니라 **패키지 안**에 둔 것은 의도다. 처음에는 루트 `vendor/` 에 두고 `__file__` 기준 상대경로를 `sys.path` 에 넣었는데, 그 경로는 레포 루트에서 editable 설치했을 때만 맞는다. 일반 설치나 Docker 에서는 패키지만 site-packages 로 복사되고 `vendor/` 는 따라가지 않아 `ModuleNotFoundError: No module named 'crawler_utils'` 가 났다. 패키지 안에 두면 설치물에 함께 들어가므로 editable·일반 설치·Docker 가 동일하게 동작한다.

벤더링한 파일은 수정하지 않는다. 동작을 바꿀 필요가 있으면 `src/jobcal/` 쪽에서 감싼다. 상위 변경을 반영할 때는 파일을 통째로 교체하고 테스트를 다시 돌린다.

## 테스트

```bash
pip install -e '.[dev]'     # 설치가 선행이다 — pytest 에 pythonpath 를 두지 않았다
python -m pytest -q
```

`pytest` 설정에 `pythonpath` 를 일부러 두지 않았다. 소스 트리를 직접 import 하면 "설치했을 때 import 되는가" 를 아무도 검사하지 않게 되고, 실제로 그 틈으로 위의 벤더 경로 버그가 테스트를 전부 통과한 채 Codespaces 까지 갔다. 지금은 테스트가 설치된 패키지를 쓰고, `tests/test_packaging.py` 가 그 회귀를 지킨다. CI 도 같은 이유로 editable 이 아닌 일반 설치를 쓰고, 레포 밖에서 `python -m jobcal --help` 를 한 번 돌려 본다.

가장 중요한 테스트는 `tests/test_plan.py` 다. 멱등성(같은 입력 → 호출 0건), 수집 실패 시 삭제 보류, 대량삭제 가드가 거기 있다. `tests/test_saramin.py` 는 타임존을 다룬다 — GitHub Actions 러너는 UTC 로 돌고, KST 자정~오전 9시 구간의 타임스탬프를 tz 없이 읽으면 마감일이 하루 앞으로 밀린다.
