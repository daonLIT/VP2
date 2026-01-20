# VP/scripts/dump_case_json.py
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from app.db.session import SessionLocal
from app.services.agent.orchestrator_react import run_orchestrated, _ensure_stream


# =========================
# ✅ 여기만 수정하면 됨
# =========================
OFFENDER_ID = 1          # 피싱범 id
VICTIM_ID = 1            # 피해자 id

MAX_TURNS = 15           # 한 라운드 최대 턴 수 (피싱범+피해자 교환 포함 구조면 orchestrator 기준에 맞춰 유지)
ROUND_LIMIT = 5          # UI 제한(max 3) 걸려있으면 3 이하로
USE_TAVILY = False       # 필요하면 True

# JSON 저장 폴더 (상대경로 가능)
DUMP_DIR = "C:/LIT_VP2/VP/scripts/case_json"
# =========================


async def main():
    # 저장 폴더 생성
    dump_dir = os.getenv("VP_CASE_DUMP_DIR", DUMP_DIR)
    Path(dump_dir).mkdir(parents=True, exist_ok=True)

    # ✅ stream_id를 미리 만들고, asyncio loop 안에서 stream을 먼저 생성해야 함
    stream_id = str(uuid.uuid4())
    _ensure_stream(stream_id)

    payload = {
        "offender_id": OFFENDER_ID,
        "victim_id": VICTIM_ID,

        "max_turns": MAX_TURNS,
        "round_limit": ROUND_LIMIT,
        "use_tavily": USE_TAVILY,

        # ✅ orchestrator_react.py에 추가한 덤프 옵션
        "dump_case_json": True,
        "dump_dir": dump_dir,
        # ✅ orchestrator_react 내부 SSE/loop 의존성 때문에 필요
        "stream_id": stream_id,
    }

    def _work():
        with SessionLocal() as db:
            return run_orchestrated(db, payload)
    result = await asyncio.to_thread(_work)

    # 콘솔 출력
    print("\n=== RESULT ===")
    print(result)

    # 저장 경로 출력
    artifact_path = result.get("artifact_path") if isinstance(result, dict) else None
    if artifact_path:
        print(f"\n[OK] Saved JSON: {artifact_path}")
    else:
        print("\n[WARN] artifact_path not found. dump_case_json 옵션/코드 적용 여부를 확인하세요.")


if __name__ == "__main__":
    asyncio.run(main())
