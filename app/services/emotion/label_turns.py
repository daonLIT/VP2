#VP/app/services/emotion/label_turns.py
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple
import json
import os
from app.services.emotion.howru_koelectra import EmotionItem, emotion_service


PairMode = Literal["none", "prev_offender", "prev_victim", "thoughts", "prev_offender+thoughts", "prev_victim+thoughts"]
HmmAttachMode = Literal["per_victim_turn", "last_victim_turn_only"]


def _norm_role(turn: Dict[str, Any]) -> str:
    return str(turn.get("role") or turn.get("speaker") or turn.get("actor") or "").strip().lower()


def _is_victim(turn: Dict[str, Any]) -> bool:
    role = _norm_role(turn)
    return role in ("victim", "피해자", "user", "사용자")


def _is_offender(turn: Dict[str, Any]) -> bool:
    role = _norm_role(turn)
    return role in ("offender", "scammer", "가해자", "사기범")

def _strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        s = s.split("```", 1)[-1]  # 앞부분 제거
        # 맨 끝 ``` 제거
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()

def _normalize_quotes(s: str) -> str:
    return (
        (s or "")
        .replace("\u201c", '"').replace("\u201d", '"')
        .replace("\u2018", "'").replace("\u2019", "'")
    )

def _try_parse_victim_json(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    victim turn의 text가 {"dialogue": "...", "thoughts":"..."} JSON 문자열로 들어오는 케이스 지원.
    반환: (dialogue, thoughts)
    """
    if not text:
        return None, None
    s = _normalize_quotes(_strip_code_fences(text)).strip()
    if not s.startswith("{"):
        return None, None
    try:
        obj = json.loads(s)
        if not isinstance(obj, dict):
            return None, None
        dlg = obj.get("dialogue")
        th  = obj.get("thoughts")
        dlg_s = dlg.strip() if isinstance(dlg, str) and dlg.strip() else None
        th_s  = th.strip()  if isinstance(th, str)  and th.strip()  else None
        return dlg_s, th_s
    except Exception:
        return None, None

def _get_text(turn: Dict[str, Any]) -> str:
    return str(turn.get("text") or turn.get("content") or "").strip()

def _get_dialogue_for_emotion(turn: Dict[str, Any]) -> str:
    """
    감정모델 입력은 '피해자 JSON이면 dialogue만', 아니면 text 전체.
    """
    raw = _get_text(turn)
    if _is_victim(turn):
        dlg, _ = _try_parse_victim_json(raw)
        return dlg or raw
    return raw

def _get_thoughts(turn: Dict[str, Any]) -> Optional[str]:
    # 프로젝트에 따라 thoughts 키 이름이 다를 수 있어 안전하게 커버
    v = turn.get("thoughts")
    if v is None:
        v = turn.get("text_pair")
    if v is None:
        v = turn.get("inner_thoughts")
    if v is None:
        # victim text가 JSON 문자열인 경우 thoughts를 거기서 추출
        raw = _get_text(turn)
        if _is_victim(turn):
            _, th = _try_parse_victim_json(raw)
            return th
        return None
    s = str(v).strip()
    return s or None


def _get_prev_offender_text(out_turns: List[Dict[str, Any]], i: int) -> Optional[str]:
    for j in range(i - 1, -1, -1):
        tj = out_turns[j]
        if _is_offender(tj):
            txt = _get_text(tj)
            return txt or None
    return None

def _get_prev_victim_text(out_turns: List[Dict[str, Any]], i: int) -> Optional[str]:
    """
    ✅ 직전 '피해자' 발화(감정모델 입력 기준)를 찾는다.
    - victim text가 JSON이면 dialogue만 추출해서 사용
    """
    for j in range(i - 1, -1, -1):
        tj = out_turns[j]
        if _is_victim(tj):
            txt = _get_dialogue_for_emotion(tj)
            return txt or None
    return None

def _try_run_hmm(emotion_seq: List[str]) -> Optional[Dict[str, Any]]:
    """
    ✅ HMM은 논문 기반으로 네가 만들 예정이니까,
    여기서는 '있으면 호출'하는 플러그인 방식으로 둔다.

    나중에 아래 모듈/함수를 만들면 자동 연결됨:
    - app.services.hmm.runner.run_hmm_on_emotions(emotion_seq: List[str]) -> Dict[str, Any]

    기대 반환 형식(권장):
    {
      "state_names": ["v1","v2","v3"],
      "gamma": [[p1,p2,p3], ...],   # T x 3 (턴별 posterior)  (선택)
      "path": ["v1","v1","v2",...], # Viterbi path           (선택)
      "final_state": "v2",          # 최종 상태              (선택)
      "final_probs": [p1,p2,p3],    # 마지막 posterior       (선택)
      ... (추가 메타 OK)
    }
    """
    try:
        from app.services.hmm.runner import run_hmm_on_emotions  # type: ignore
        return run_hmm_on_emotions(emotion_seq)
    except Exception:
        # HMM 구현 전/미존재/에러면 그냥 None으로
        return None


def label_emotions_on_turns(
    turns: List[Dict[str, Any]],
    *,
    pair_mode: PairMode = "none",
    batch_size: int = 16,
    max_length: int = 512,
    run_hmm: bool = True,
    hmm_attach: HmmAttachMode = "per_victim_turn",
) -> List[Dict[str, Any]]:
    """
    turns를 받아서:
    1) 피해자 발화에만 emotion 결과 주입
    2) (옵션) 피해자 pred4 시퀀스를 HMM에 넣고 v1/v2/v3 결과를 주입
    """
    out_turns = [dict(t) for t in turns]
    # ✅ 디버그 출력 토글: EMOTION_DEBUG_INPUT=1 일 때만 출력
    debug_input = (os.getenv("EMOTION_DEBUG_INPUT", "0") or "").strip().lower() in ("1","true","yes","y","on")
    def _dbg(msg: str) -> None:
        if debug_input:
            print(msg, flush=True)

    victim_indices: List[int] = []
    items: List[EmotionItem] = []

    # 1) 피해자 발화만 추출해서 모델 입력 구성
    for i, t in enumerate(out_turns):
        if not _is_victim(t):
            continue

        # ✅ 현재 victim의 모델 입력 text(=dialogue 우선)
        text = _get_dialogue_for_emotion(t)
        if not text:
            continue

        text_pair: Optional[str] = None
        if pair_mode == "prev_offender":
            text_pair = _get_prev_offender_text(out_turns, i)
        elif pair_mode == "prev_victim":
            text_pair = _get_prev_victim_text(out_turns, i)
        elif pair_mode == "thoughts":
            text_pair = _get_thoughts(t)
        elif pair_mode == "prev_offender+thoughts":
            a = _get_prev_offender_text(out_turns, i)
            b = _get_thoughts(t)
            if a and b:
                text_pair = f"{a}\n{b}"
            else:
                text_pair = a or b
        elif pair_mode == "prev_victim+thoughts":
            a = _get_prev_victim_text(out_turns, i)
            b = _get_thoughts(t)
            if a and b:
                text_pair = f"{a}\n{b}"
            else:
                text_pair = a or b
        # ✅ 모델 입력 확인 로그
        _dbg(
            "[EMOTION_INPUT]"
            f" pair_mode={pair_mode}"
            f" victim_turn_idx={i}"
            f" text={text!r}"
            f" text_pair={text_pair!r}"
        )
        victim_indices.append(i)
        items.append(EmotionItem(text=text, text_pair=text_pair))

    if not items:
        return out_turns

    # 2) 감정 예측
    preds = emotion_service.predict_batch(
        items,
        batch_size=batch_size,
        max_length=max_length,
        include_probs8=True,
    )

    # 3) 결과 주입 + 피해자 pred4 시퀀스 수집
    victim_emotion_seq: List[str] = []            # HMM 입력용 (피해자 pred4)
    labeled_victim_indices: List[int] = []        # _skip 제외한 실제 주입된 victim turn index
    for idx, pred in zip(victim_indices, preds):
        if pred.get("_skip"):
            continue

        emotion_obj = {
            "pred4": pred["pred4"],
            "probs4": pred["probs4"],
            "pred8": pred["pred8"],
            "probs8": pred.get("probs8"),
            "surprise_to": pred.get("surprise_to"),
            "cue_scores": pred.get("cue_scores"),
            "p_surprise": pred.get("p_surprise"),
        }
        out_turns[idx]["emotion"] = emotion_obj
        victim_emotion_seq.append(pred["pred4"])
        labeled_victim_indices.append(idx)

    # 4) (옵션) HMM 실행 후 결과 주입
    if run_hmm and victim_emotion_seq:
        hmm_result = _try_run_hmm(victim_emotion_seq)

        if hmm_result:
            gamma = hmm_result.get("gamma")  # T x 3 (optional)
            path = hmm_result.get("path")    # T (optional)

            # attach 모드에 따라
            if hmm_attach == "per_victim_turn" and isinstance(gamma, list):
                # 실제 주입된 victim_emotion_seq 길이와 gamma 길이가 같을 때만 per-turn 주입
                if len(gamma) == len(labeled_victim_indices):
                    for t_i, turn_idx in enumerate(labeled_victim_indices):
                        out_turns[turn_idx]["hmm"] = {
                            "state_names": hmm_result.get("state_names", ["v1", "v2", "v3"]),
                            "posterior": gamma[t_i],
                            "viterbi": path[t_i] if isinstance(path, list) and t_i < len(path) else None,
                        }

            # 요약 결과는 마지막 victim turn에 붙여두면 downstream에서 쓰기 쉬움
            last_victim_turn_idx = labeled_victim_indices[-1] if labeled_victim_indices else victim_indices[-1]
            out_turns[last_victim_turn_idx].setdefault("hmm_summary", {})
            out_turns[last_victim_turn_idx]["hmm_summary"] = {
                "state_names": hmm_result.get("state_names", ["v1", "v2", "v3"]),
                "final_state": hmm_result.get("final_state"),
                "final_probs": hmm_result.get("final_probs"),
                "path": hmm_result.get("path"),
                "meta": {k: v for k, v in hmm_result.items() if k not in ("gamma", "path", "final_state", "final_probs", "state_names")},
            }

    return out_turns
