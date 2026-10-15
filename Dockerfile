# 멀티스테이지 — 빌드 산물과 런타임을 분리한다.
# 이미지에 들어가는 건 설치된 패키지와 설정뿐이고, 자격증명은 들어가지 않는다.

FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml ./
COPY src/ ./src/
RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --upgrade pip \
    && /venv/bin/pip install --no-cache-dir .

FROM python:3.12-slim AS runtime
# 기본적으로 Chromium 을 받지 않는다 — 사람인·원티드는 평문 JSON 이라 필요 없다.
# 브라우저 렌더링이 필요한 소스를 붙이면 '.[crawl]' 설치와 scrapling install 을
# 여기에 추가한다. 그때는 이미지가 수백 MB 커진다.
ENV PATH="/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

RUN useradd --create-home --uid 10001 jobcal
COPY --from=build /venv /venv

# src/ 를 런타임에 복사하지 않는다. 벤더 모듈까지 패키지 안에 들어가 있으므로
# /venv 의 설치물만으로 완결된다. 소스를 또 깔면 어느 쪽이 import 되는지가
# 모호해지고, 바로 그 모호함이 벤더 경로 버그를 늦게 발견한 원인이었다.
WORKDIR /app
COPY --chown=jobcal:jobcal config/ ./config/
USER jobcal

# 설치물만으로 import 가 되는지 빌드 시점에 확인한다.
# 여기서 실패하면 이미지가 만들어지지 않으므로, 깨진 패키징이 배포까지 가지 않는다.
RUN python -c "import jobcal.cli, jobcal.sources.base, jobcal.vendor.crawler_utils; print('import OK')"

# 기본은 dry-run 이다. 자격증명 없이 docker run 했을 때 캘린더가 바뀌면 안 된다.
ENTRYPOINT ["python", "-m", "jobcal"]
CMD ["--dry-run"]
