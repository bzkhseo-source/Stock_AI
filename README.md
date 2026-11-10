# 왜올랐지 — AI 주식 트렌드 분석 플랫폼 (비공개 베타)

국내 상장사의 급등락 원인, 테마 노출도, 리스크를 데이터로 보여주는 투자 참고 정보 서비스입니다.
FastAPI + Jinja2 + HTMX로 만든 반응형 웹이며, PWA로 홈 화면에 설치할 수 있습니다.

> 투자 참고 정보이며 매수·매도를 권유하지 않습니다.

## 1. 바로 실행해 보기 (API 키 없이, 데모 데이터)

Windows PowerShell 기준입니다. Python 3.11 이상이 필요합니다.

```powershell
cd C:\Users\seo\Desktop\Stock_AI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python -m jobs.run demo          # 가상 회사 25개 + 가상 시세로 분석까지 실행
uvicorn app.main:app --reload    # http://127.0.0.1:8000 접속
```

데모 화면 상단에 노란 띠로 "데모 데이터"가 표시됩니다. 회사·시세·공시·뉴스는 모두 가상입니다.

## 2. 실제 데이터로 전환

1. `.env.example`을 `.env`로 복사하고 키를 채웁니다.

   | 항목 | 발급처 | 없으면 |
   |---|---|---|
   | `KRX_ID`, `KRX_PW` | data.krx.co.kr 회원가입 | 시가총액·종목 목록이 비어 수집 실패로 기록됨 |
   | `DART_API_KEY` | opendart.fss.or.kr | 공시·재무·노출도 검증 생략 |
   | `GEMINI_API_KEY` | Google AI Studio | 노출도 검증과 "AI 해석" 요약 생략 (나머지는 동작) |
   | `DATABASE_URL` | Neon | 비우면 로컬 SQLite (`data/app.db`) |

2. 데모 데이터를 지우고 실제 데이터를 적재합니다.

```powershell
del data\app.db                          # 데모 DB 삭제 (SQLite인 경우)
python -m jobs.run init                  # 테이블 + 테마 사전
python -m jobs.run backfill --days 260   # 최초 1회: 약 1년치 시세 (시간이 걸립니다)
python -m jobs.run daily                 # 이후 매일 장 마감 후
python -m jobs.run news                  # 뉴스 (1시간 간격 권장)
python -m jobs.run verify --limit 10     # 테마 노출도 검증 (DART + Gemini)
```

`python -m jobs.run`의 각 단계는 ✔/✘로 결과를 보여주고, 웹의 **기준·정보** 화면 아래에도 실행 기록이 남습니다.

## 3. 폴더 구조

```
app/          웹 서버 (main.py), 화면 조회 (queries.py), 템플릿, 정적 파일(PWA)
collectors/   수집기: prices_kr(pykrx·FDR), prices_global(yfinance), dart, news(Google 뉴스 RSS)
analysis/     지표(metrics), 점수(scoring), 테마(themes), 노출도(exposure), 원인 추적(explain), 리스크(risk)
llm/          Gemini 호출 계층 (일일 상한, 공급사 교체 지점)
jobs/         배치 진입점 run.py, 데모 데이터 demo.py
db/           모델, 세션, upsert
data/         themes.yaml (테마 사전), theme_members_manual.csv (수동 검토 종목)
tests/        단위 테스트 (pytest)
tools/        Tailwind CSS 빌드 설정
deploy/       GitHub Actions 배치 예약 실행 파일 (.github/workflows/ 로 옮겨서 사용)
```

## 4. 테마와 종목 연결 방식

종목은 아래 우선순위로 테마에 연결됩니다.

1. **수동 검토** — `data/theme_members_manual.csv`에 직접 적은 종목 (가장 신뢰)
2. **사업보고서** — `jobs.run verify`가 DART 사업보고서에서 테마 매출 비중을 계산. AI가 고른 근거 문장이 원문에 실제로 있는지 코드가 대조합니다.
3. **뉴스 동반 등장** — 테마 키워드 뉴스 제목에 2회 이상 나온 종목 → "테마 편입주" (사업 연관성 미검증)

초기에는 3번만 있으므로, 테마 사전(`data/themes.yaml`)을 검토하고 대표 종목을 `theme_members_manual.csv`에 넣어 두면 품질이 빠르게 올라갑니다.

```csv
theme_id,code,relation,evidence,evidence_url
semiconductor,000660,직접 관련주,HBM 생산,https://dart.fss.or.kr/...
```

## 5. 베타 운영

- `.env`의 `INVITE_CODES`에 초대 코드를 넣으면 이메일 + 초대 코드로만 접속할 수 있습니다. 비워두면 로그인 없이 열리므로 **배포할 때는 반드시 설정**하세요.
- 베타 사용자는 모든 기능을 씁니다. 화면의 `PRO` 배지는 정식 출시 후 유료가 될 수 있는 기능 표시입니다. 기능별 등급은 `config.py`의 `FEATURES`에서 바꿉니다.
- 화면 조회는 `usage_events`, 의견은 `feedback` 테이블에 쌓입니다.

## 6. 배포

- **웹**: 무료 PaaS에 `uvicorn app.main:app --host 0.0.0.0 --port $PORT`로 실행. 환경 변수에 `.env` 값 등록.
- **배치**: `deploy/github-actions-batch.yml`을 `.github/workflows/batch.yml`로 옮긴 뒤 GitHub 비공개 저장소에 올리고 Secrets에 키를 등록하면, 평일 장 마감 후와 매시 뉴스 수집이 자동 실행됩니다. 웹과 배치가 같은 Neon DB를 씁니다.
- GitHub Actions 서버는 해외에 있어 KRX 접속이 막힐 수 있습니다. 첫 실행 결과를 확인하고, 막히면 배치는 개인 PC 작업 스케줄러로 돌립니다.

## 7. 화면 스타일 수정 후

템플릿에 새 Tailwind 클래스를 쓰면 CSS를 다시 만듭니다 (Node.js 필요).

```powershell
npx tailwindcss@3.4.17 -c tools/tailwind.config.js -i tools/input.css -o app/static/app.css --minify
```

## 8. 데이터 사용 주의

pykrx·FinanceDataReader·yfinance는 비공식 라이브러리이고, Google 뉴스 RSS의 상업적 이용 조건도 확인이 필요합니다. 비공개 베타에서만 사용하고, 정식 출시(유료화) 전에 재배포가 허용된 데이터 소스로 교체하고 법률 검토를 받습니다.
