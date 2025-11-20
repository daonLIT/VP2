# app/services/tts_service.py
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메모리 캐시: {case_id: {run_no: [turns]}}
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_DIALOG_CACHE: Dict[str, Dict[int, List[dict]]] = {}


def cache_run_dialog(case_id: str, run_no: int, turns: List[dict]) -> None:
    """라운드별 대화를 캐시에 저장"""
    if case_id not in _DIALOG_CACHE:
        _DIALOG_CACHE[case_id] = {}
    _DIALOG_CACHE[case_id][run_no] = turns
    logger.info(f"[TTS_CACHE] cached: case_id={case_id}, run_no={run_no}, turns={len(turns)}")


def get_cached_dialog(case_id: str, run_no: int) -> Optional[List[dict]]:
    """캐시에서 특정 라운드 대화 조회"""
    return _DIALOG_CACHE.get(case_id, {}).get(run_no)


def clear_case_dialog_cache(case_id: str) -> None:
    """특정 케이스의 모든 대화 캐시 제거"""
    if case_id in _DIALOG_CACHE:
        del _DIALOG_CACHE[case_id]
        logger.info(f"[TTS_CACHE] cleared: case_id={case_id}")


def get_all_runs_for_case(case_id: str) -> List[int]:
    """특정 케이스의 모든 라운드 번호 반환"""
    if case_id not in _DIALOG_CACHE:
        return []
    return sorted(_DIALOG_CACHE[case_id].keys())