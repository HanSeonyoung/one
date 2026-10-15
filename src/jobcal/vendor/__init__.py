"""벤더링한 서드파티 모듈 — 원문 그대로 보관한다.

패키지 '안' 에 두는 것이 중요하다. 예전에는 레포 루트의 vendor/ 에 두고
__file__ 기준 상대경로를 sys.path 에 밀어넣었는데, 그 경로는 레포 루트에서
editable 설치했을 때만 맞는다. 일반 설치나 Docker 에서는 패키지만
site-packages 로 복사되고 vendor/ 는 따라가지 않아 ModuleNotFoundError 가 났다.

여기 두면 설치물에 함께 들어가므로 editable·일반 설치·Docker 에서 동일하게 import 된다.
"""
