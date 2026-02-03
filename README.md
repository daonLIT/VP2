# VoicePhish Simulator (VPSim)

보이스피싱(전화금융사기) 시뮬레이션을 위한 **AI 기반 대화 시뮬레이터**입니다.

---

## 🚀 빠른 시작(권장)

```bash
# 1) 소스 코드 다운로드
git clone https://github.com/yoonmo01/VP2.git
cd VP2
```

### 1-1) PostgreSQL 준비(최초 1회)

**옵션 A: 로컬에 PostgreSQL 설치 후 DB/유저 생성**

Linux (systemd)

```bash
sudo systemctl enable --now postgresql
sudo -u postgres psql -c "CREATE USER vpuser WITH PASSWORD '0320';"
sudo -u postgres psql -c "CREATE DATABASE voicephish OWNER vpuser;"
```

macOS (Homebrew)

```bash
brew services start postgresql
psql postgres -c "CREATE USER vpuser WITH PASSWORD '0320';"
psql postgres -c "CREATE DATABASE voicephish OWNER vpuser;"
```

Windows

1. PostgreSQL을 설치하고 “SQL Shell (psql)” 실행
2. 아래 명령 실행:

```sql
CREATE USER vpuser WITH PASSWORD '0320';
CREATE DATABASE voicephish OWNER vpuser;
```

**옵션 B: Docker로 간단하게 구성**

```bash
docker run -d --name vpsim-postgres \
  -e POSTGRES_USER=vpuser \
  -e POSTGRES_PASSWORD=0320 \
  -e POSTGRES_DB=voicephish \
  -p 5432:5432 \
  postgres:16
```

> 확인:
>
> ```bash
> psql -h localhost -U vpuser -d voicephish -c "\dt"
> ```
>
> (처음에는 테이블이 없어도 정상입니다. 설정 스크립트가 테이블 생성 및 시드 데이터를 넣습니다.)

---

### 2) 환경 변수(여러 개의 `.env` 파일 사용)

이 프로젝트는 여러 개의 `.env` 파일을 사용합니다.

> ⚠️ 실제 비밀값(API 키, 자격 증명 JSON 경로 등)은 커밋하지 마세요.
> 공유 시에는 플레이스홀더 또는 `.env.example` 파일을 사용하세요.

#### 2-1) 메인 설정: `VP2/.env` (권장)

`VP2/.env`를 만들고 아래 예시를 붙여 넣으세요.
프론트엔드는 보통 **별도의 `.env` 파일이 필요 없습니다.**

```ini
# ── 데이터베이스 ───────────────────────────────────────
# psycopg (v3) 예시:
DATABASE_URL=postgresql+psycopg://<user>:<password>@127.0.0.1:5432/<db>?connect_timeout=5

# (선택) 레거시 예시:
# DATABASE_URL=postgresql+psycopg2://<user>:<password>@localhost:5432/<db>

# ── LLM 키 ──────────────────────────
# OpenAI만 사용할 경우 OPENAI_API_KEY만 있으면 충분합니다.
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# 추후 victim/provider를 Gemini로 전환할 때만 필요
GOOGLE_API_KEY=AIza-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ── 앱 ───────────────────────────────
APP_ENV=dev
API_PREFIX=/api

# 역할별 모델 이름
ATTACKER_MODEL=gpt-4.1
VICTIM_MODEL=gemini-2.5-flash-lite
ADMIN_MODEL=gpt-4.1-mini
AGENT_MODEL=gpt-4.1-mini

# MCP 엔드포인트
MCP_HTTP_URL=http://127.0.0.1:5177/mcp

# (선택) 라운드/턴 제한
MAX_OFFENDER_TURNS=15
MAX_VICTIM_TURNS=15

# (선택) Gemini / GCP 사용 시 Google 자격 증명
GOOGLE_APPLICATION_CREDENTIALS=C:/path/to/your-credentials.json

# ── 감정 주입(Emotion Injection) ──────────────────────────────
EMOTION_ENABLED=1           # 1=ON, 0=OFF
EMOTION_PAIR_MODE=none      # 예: none | prev_offender
EMOTION_MODEL_ID=LimYeri/HowRU-KoELECTRA-Emotion-Classifier
EMOTION_MAX_LENGTH=512
EMOTION_BATCH_SIZE=16
EMOTION_DEBUG_INPUT=1
```

#### 2-2) 백엔드 오버라이드: `VP/app/.env` (선택)

백엔드 전용 값만 가볍게 덮어쓰고 싶다면 `VP/app/.env`를 생성하세요:

```ini
MCP_HTTP_URL=http://127.0.0.1:5177/mcp

EMOTION_PAIR_MODE=prev_offender
EMOTION_MODEL_ID=LimYeri/HowRU-KoELECTRA-Emotion-Classifier
EMOTION_MAX_LENGTH=512
EMOTION_BATCH_SIZE=16
EMOTION_DEBUG_INPUT=1
```

#### 참고 사항

* 공격자/사기범은 고정된 id에 의존하지 않습니다. 공격자 페르소나/시나리오는 시드 데이터/설정 로직에서 선택됩니다.
* 프론트엔드는 보통 `.env` 없이 동작하며, `window.location.origin`에서 파생된 API Base URL을 사용합니다.

---

### 3) 실행

```bash
./run-local.sh
```

또는 개별 실행:

```bash
Backend:
cd VP2_EN
python -m uvicorn app.main:app --reload

MCP server:
cd VP2_EN
python -m uvicorn vp_mcp.mcp_server.server:app --reload --port 5177

Frontend:
cd VP2_EN/FE
npm run dev
```

> 이 스크립트는 **백엔드/프론트엔드 의존성 설치 → DB 시딩(테이블/샘플 데이터) → 서버 실행**을 자동으로 처리합니다.
> (프론트 `.env` 없이도 동작하며 `window.location.origin` 기반 API Base URL을 사용합니다.)

---

### 접속 URL

* 프론트엔드: [http://localhost:5173](http://localhost:5173)
* 백엔드 API: [http://127.0.0.1:8000](http://127.0.0.1:8000)
* API 문서: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

---

## ⚙️ 환경 설정(상세)

### 1) Python 가상환경

```bash
# venv
python3 -m venv venv
source venv/bin/activate

# 또는 conda
conda create -n vpsim python=3.11
conda activate vpsim
```

### 2) 백엔드 의존성 설치

```bash
pip install -r requirements.txt
```

(필요 시 개별 설치)

```bash
pip install fastapi uvicorn sqlalchemy psycopg2-binary pydantic pydantic-settings python-dotenv
```

### 3) 수동 DB 설정(선택)

```bash
# PostgreSQL DB/user 생성
sudo -u postgres createdb voicephish
sudo -u postgres createuser vpuser
```

### 4) 시드 데이터 삽입

```bash
python seed.py
```

### 5) 백엔드 실행

```bash
uvicorn app.main:app --reload --port 8000
```

### 6) 프론트엔드 실행

```bash
cd FE
npm install
npm run dev
```

---

## 📁 프로젝트 구조

```text
VP2_EN/
├── app/                              # FastAPI 백엔드(API, 시뮬레이션 로직, 서비스)
│   ├── core/                         # 설정, 구성, 로깅
│   ├── db/                           # DB 모델, 세션, ORM 베이스
│   ├── routers/                      # API 라우터(엔드포인트)
│   ├── schemas/                      # Pydantic 스키마(요청/응답 모델)
│   ├── services/                     # 비즈니스 로직(시뮬레이션, 프롬프트, 에이전트, 감정, HMM, TTS)
│   ├── static/                       # 백엔드가 서빙하는 정적 파일(이미지, 에셋)
│   └── utils/                        # 공용 유틸(의존성, id, 페이지네이션, 타입 등)
│
├── FE/                               # React 프론트엔드(Vite)
│   ├── src/                          # 프론트 소스(페이지/컴포넌트/훅)
│   ├── public/                       # 그대로 제공되는 정적 에셋
│   └── package.json                  # Node.js 의존성 & 스크립트
│
├── vp_mcp/                           # MCP 서버 패키지(마이크로 컨트롤 패널 서버)
│   └── mcp_server/                   # MCP 서버 구현(routes/tools/prompts)
│
├── scripts/                          # 유틸 스크립트(데이터셋 추출, 감정 라벨링 실행기 등)
├── seeds/                            # 시드/샘플 데이터(공격자, 피해자, 시나리오 템플릿)
├── tests/                            # 테스트(헬스체크, 시뮬레이션 스텁 등)
│
├── .env                              # 메인 env(권장, 프로젝트 전역 설정)
├── app/.env                          # 백엔드 오버라이드 env(선택, 백엔드 전용)
│
├── requirements.txt                  # Python 의존성(최소)
├── requirements_lock.txt             # Python 의존성(고정/락 버전)
├── run-local.sh                      # 통합 실행 스크립트(백엔드 + mcp + 프론트)
├── seed.py                           # DB 시드 스크립트(테이블/샘플 데이터)
├── run_cycle.py                      # 사이클 러너(배치 시뮬레이션/반복 실행)
├── mcp_sim.db                        # 로컬 sqlite DB 파일(이 구성이면 사용될 수 있음)
└── README.md                         # 프로젝트 문서
```

---

## 🔧 주요 기능

* 시나리오 유형: 기관 사칭 / 가족·지인 사칭 / 대출 사기
* 시뮬레이션 모드: AI 에이전트 없음 / admin-in-the-loop(관리자 개입) 모드

---

## 🐛 문제 해결

### DB 연결 오류

```bash
# PostgreSQL 서비스 상태 확인
sudo systemctl status postgresql

# DB 연결 테스트
psql -h localhost -U vpuser -d voicephish
```

### 포트 충돌

```bash
# 사용 중인 포트 확인
netstat -tlnp | grep -E "(8000|5173)"

# 프로세스 종료
pkill -f "uvicorn app.main:app"
pkill -f "vite --host 0.0.0.0"
```

---

## 📊 샘플 데이터

* **공격자(시나리오)**: 8
* **피해자**: 6
* **대화 턴**: 최대 200턴

---

## 🚀 배포(프로덕션)

```bash
# 프론트 빌드
cd FE
npm run build

# 백엔드 실행
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## 📝 라이선스

```text
본 프로젝트는 연구 및 교육 목적을 위해 제작되었습니다.
```
