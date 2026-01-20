# VoicePhish Simulator (VPSim)

보이스피싱 시뮬레이션을 위한 AI 기반 대화 시뮬레이터입니다.

---

## 🚀 빠른 실행 (권장)

```bash
# 1) 소스 코드 다운로드
git clone https://github.com/yoonmo01/VP.git
cd VP
```

### 1-1) PostgreSQL 준비 (처음 한 번만)

**옵션 A: 로컬 PostgreSQL 설치 후 DB/유저 생성**

Linux (systemd 기준)
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
1) PostgreSQL 설치 후 “SQL Shell (psql)” 실행  
2) 아래 명령 실행:
```sql
CREATE USER vpuser WITH PASSWORD '0320';
CREATE DATABASE voicephish OWNER vpuser;
```

**옵션 B: Docker로 간편 실행**
```bash
docker run -d --name vpsim-postgres   -e POSTGRES_USER=vpuser   -e POSTGRES_PASSWORD=0320   -e POSTGRES_DB=voicephish   -p 5432:5432   postgres:16
```

> 확인:
> ```bash
> psql -h localhost -U vpuser -d voicephish -c "\dt"
> ```
> (처음엔 테이블이 비어있어도 정상입니다. 실행 스크립트가 시드하며 생성합니다.)

---

### 2) 백엔드 환경변수 설정 (.env 생성)

`VP/.env` 파일을 만들고 아래 예시를 붙여 넣으세요.  
⚠️ 프론트엔드는 **별도의 `.env` 파일이 필요하지 않습니다.**

```ini
# ── Database ──────────────────────────
DATABASE_URL=postgresql+psycopg2://<유저명>:<비밀번호>@<호스트>:<포트>/<DB이름>
(예시:DATABASE_URL=postgresql+psycopg2://vpuser:0320@localhost:5432/voicephish)

# ── LLM Keys ──────────────────────────
OPENAI_API_KEY=sk-xxxx
GOOGLE_API_KEY=AIza-xxxx   # (Gemini 피해자 시 필요)

# ── App ───────────────────────────────
APP_ENV=dev
API_PREFIX=/api

# 역할별 모델명
ATTACKER_MODEL=gpt-4.1-mini
VICTIM_MODEL=gemini-2.5-flash-lite
ADMIN_MODEL=o4-mini

# 피해자 프로바이더: openai | gemini
VICTIM_PROVIDER=openai

# (선택) 라운드/턴 제한
MAX_OFFENDER_TURNS=15
MAX_VICTIM_TURNS=15
```

---

### 3) 실행

```bash
./run-local.sh
```
```python
백앤드:
cd VP
uvicorn app.main:app --reload

mcp서버:
cd VP
uvicorn vp_mcp.mcp_server.server:app --reload --port 5177

프론트엔드:
cd VP/FE
npm run dev
```

> 스크립트가 **백엔드/프론트엔드 의존성 설치 → DB 시드(테이블/샘플데이터) → 서버 실행**까지 자동으로 처리합니다.  
> (프론트 `.env` 없이 동작합니다—`window.location.origin` 기반으로 API 주소를 사용)

---

### 접속 주소
- 프론트엔드: http://localhost:5173  
- 백엔드 API: http://127.0.0.1:8000  
- API 문서: http://127.0.0.1:8000/docs  

---

## ⚙️ 환경 설정 (상세)

### 1. Python 가상환경

```bash
# venv
python3 -m venv venv
source venv/bin/activate

# 또는 conda
conda create -n vpsim python=3.11
conda activate vpsim
```

### 2. 백엔드 의존성 설치

```bash
pip install -r requirements.txt
```

(필요시 개별 설치)  
```bash
pip install fastapi uvicorn sqlalchemy psycopg2-binary pydantic pydantic-settings python-dotenv
```

### 3. 데이터베이스 수동 설정 (옵션)

```bash
# PostgreSQL DB/유저 생성
sudo -u postgres createdb voicephish
sudo -u postgres createuser vpuser
```

### 4. 데이터 시드

```bash
python seed.py
```

### 5. 백엔드 실행

```bash
uvicorn app.main:app --reload --port 8000
```

### 6. 프론트엔드 실행

```bash
cd FE
npm install
npm run dev
```

---

## 📁 프로젝트 구조

```
VP/
├── app/                    # FastAPI 백엔드
│   ├── core/             # 설정 및 핵심 기능
│   ├── db/               # 데이터베이스 모델 및 세션
│   ├── routers/          # API 라우터
│   ├── services/         # 비즈니스 로직
│   └── schemas/          # Pydantic 스키마
├── FE/                   # React 프론트엔드
│   ├── src/              # 소스 코드
│   ├── public/           # 정적 파일
│   └── package.json      # Node.js 의존성
├── seeds/                # 샘플 데이터
├── requirements.txt      # Python 의존성
├── .env                  # 환경 변수 파일 (DB URL, API key, 시뮬레이션 기본값 등 설정)
├── run-local.sh          # 통합 실행 스크립트 (Linux/macOS/WSL)
└── seed.py               # 데이터 시드 스크립트
```

---

## 🔧 주요 기능
- 시나리오 유형: 기관 사칭형 / 가족·지인 사칭 / 대출사기형  
- 시뮬레이션 모드: AI 에이전트 없음 / 관리자 개입  

---

## 🐛 문제 해결

### DB 연결 오류
```bash
# PostgreSQL 서비스 상태 확인
sudo systemctl status postgresql

# 데이터베이스 연결 테스트
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

- **공격자(시나리오)**: 8개
- **피해자**: 6개
- **대화 턴**: 최대 200턴


## 🚀 배포 (프로덕션)

```bash
# 프론트 빌드
cd FE
npm run build

# 백엔드 실행
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## 📝 라이선스

이 프로젝트는 연구 및 교육 목적으로 제작되었습니다.
