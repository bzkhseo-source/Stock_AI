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
render.yaml   Render 배포 설정 (Blueprint)
.github/      GitHub Actions 배치 예약 실행
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

## 6. 배포 (Render + Neon, 무료)

구조: **웹 서버 = Render** (무료, 싱가포르 리전) · **DB = Neon PostgreSQL** (무료) · **수집·분석 배치 = PC 또는 GitHub Actions**.
웹 서버는 미리 계산된 결과만 읽으므로 가벼운 `requirements-web.txt`만 설치합니다.

### 6-1. Neon DB 만들기
1. neon.com 가입 → **New Project** → 리전 **AWS Asia Pacific (Singapore)** 선택
2. **Connection string**을 복사합니다 (`postgresql://...neon.tech/neondb?sslmode=require...`).
3. PC의 `.env`에 `DATABASE_URL=복사한 값`을 넣습니다.

### 6-2. 운영 DB에 데이터 넣기 (PC에서)
```powershell
.\.venv\Scripts\Activate.ps1
python -m jobs.run init
python -m jobs.run backfill --days 260   # 최초 1회 (KRX 로그인 없으면 20~40분)
python -m jobs.run daily
```

### 6-3. Render에 웹 서버 배포
1. render.com 가입 (GitHub 계정으로 로그인) → **New → Blueprint** → `Stock_AI` 저장소 선택
2. 저장소의 `render.yaml`을 읽어 설정이 자동으로 채워집니다. 직접 입력할 값은 두 가지입니다.
   - `DATABASE_URL`: Neon 연결 문자열
   - `INVITE_CODES`: 베타 초대 코드 (예: `wae-beta-1101`, 쉼표로 여러 개)
3. **Apply** → 빌드가 끝나면 `https://waeollatji-xxxx.onrender.com` 주소가 생깁니다.
4. `SECRET_KEY`는 Render가 자동으로 만듭니다. `APP_ENV=production`이라 초대 코드·세션 키·DB가 없으면 서버가 시작되지 않습니다 (실수로 공개되는 것 방지).

무료 웹 서비스는 15분간 접속이 없으면 잠들고, 다음 접속 때 깨어나는 데 약 1분 걸립니다.
GitHub `main`에 push하면 Render가 자동으로 다시 배포합니다.

### 6-4. 매일 자동 수집 (GitHub Actions)
저장소 Settings → Secrets and variables → Actions에 `DATABASE_URL`, `DART_API_KEY`, `KRX_ID`, `KRX_PW`, `GEMINI_API_KEY`를 등록하면
`.github/workflows/batch.yml`이 평일 16:40(장 마감 후)과 매시 뉴스 수집을 실행합니다. Actions 탭에서 **Run workflow**로 직접 실행할 수도 있습니다.
GitHub Actions 서버는 해외에 있어 KRX 접속이 막힐 수 있습니다. 첫 실행 기록을 확인하고, 막히면 PC 작업 스케줄러로 `python -m jobs.run daily`를 실행합니다.

### 6-5. 용량
Neon 무료 플랜은 프로젝트당 1GB입니다. 전 종목 1년치 시세와 지표는 수백 MB 수준이라 베타 기간에는 충분합니다.

## 7. 화면 스타일 수정 후

템플릿에 새 Tailwind 클래스를 쓰면 CSS를 다시 만듭니다 (Node.js 필요).

```powershell
npx tailwindcss@3.4.17 -c tools/tailwind.config.js -i tools/input.css -o app/static/app.css --minify
```

## 8. 데이터 사용 주의

pykrx·FinanceDataReader·yfinance는 비공식 라이브러리이고, Google 뉴스 RSS의 상업적 이용 조건도 확인이 필요합니다. 비공개 베타에서만 사용하고, 정식 출시(유료화) 전에 재배포가 허용된 데이터 소스로 교체하고 법률 검토를 받습니다.
