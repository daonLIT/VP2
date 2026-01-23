# app/services/emotion/emoti_shing_hmm.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple, Optional
import math


# =========================================================
# Emoti-Shing (Nyassi et al., 2024) HMM (Fixed parameters)
# - Hidden states: V1, V2, V3
# - Observations: neutral, anger, fear, excitement
# - Transition matrix A: Table 4
# - Emission matrix B: Table 5
# - Initial distribution pi: (1.0, 0.0, 0.0)
# =========================================================

STATES: Tuple[str, str, str] = ("V1", "V2", "V3")
EMOTIONS: Tuple[str, str, str, str] = ("neutral", "anger", "fear", "excitement")

STATE_TO_IDX: Dict[str, int] = {s: i for i, s in enumerate(STATES)}
EMO_TO_IDX: Dict[str, int] = {e: i for i, e in enumerate(EMOTIONS)}


# ---- Transition probability matrix A (Table 4) ----
# Rows: from V1,V2,V3 ; Cols: to V1,V2,V3
_A_RAW: List[List[float]] = [
    [0.22, 0.44, 0.33],  # V1 -> V1,V2,V3
    [0.22, 0.33, 0.44],  # V2 -> V1,V2,V3
    [0.22, 0.33, 0.44],  # V3 -> V1,V2,V3
]

# ---- Emission probability matrix B (Table 5) ----
# Paper table columns order: a f n e  (anger, fear, neutral, excitement)
# We internally store in EMOTIONS order: neutral, anger, fear, excitement
# V1: a 0.28, f 0.16, n 0.24, e 0.32
# V2: a 0.28, f 0.16, n 0.24, e 0.32
# V3: a 0.23, f 0.15, n 0.31, e 0.31
_B: List[List[float]] = [
    [0.24, 0.28, 0.16, 0.32],  # V1: neutral, anger, fear, excitement
    [0.24, 0.28, 0.16, 0.32],  # V2
    [0.31, 0.23, 0.15, 0.31],  # V3
]

# ---- Initial distribution pi (Eq. 33) ----
_PI: List[float] = [1.0, 0.0, 0.0]


def _normalize_rows(mat: Sequence[Sequence[float]]) -> List[List[float]]:
    """
    논문 표는 반올림 값이라 행 합이 0.99처럼 살짝 어긋날 수 있음.
    구현 안정성을 위해 행 단위 정규화만 1회 적용(비율은 유지).
    """
    out: List[List[float]] = []
    for row in mat:
        s = float(sum(row))
        if s <= 0:
            raise ValueError("Matrix row sum must be > 0.")
        out.append([float(v) / s for v in row])
    return out


# 정규화된 A (비율 유지)
A: List[List[float]] = _normalize_rows(_A_RAW)


def _safe_log(x: float) -> float:
    """
    0 확률은 log에서 -inf 처리.
    파라미터상 0이 실제로 존재(π의 0)하므로 안전 처리 필요.
    """
    if x <= 0.0:
        return float("-inf")
    return math.log(x)


# (선택) 감정 라벨 정규화 매핑: 프로젝트 라벨이 한국어/변형일 가능성 대비
DEFAULT_EMOTION_ALIASES: Dict[str, str] = {
    # ---- 4-class code aliases (현재 오케스트레이터 pred4 출력 대응) ----
    # pred4: 'N'/'F'/'A'/'E' 같은 코드가 들어오므로 lower() 이후도 포함
    "n": "neutral",
    "f": "fear",
    "a": "anger",
    "e": "excitement",

    # neutral
    "neutral": "neutral",
    "중립": "neutral",
    "중립감": "neutral",
    "무감정": "neutral",
    "평온": "neutral",
    # anger
    "anger": "anger",
    "angry": "anger",
    "분노": "anger",
    "화남": "anger",
    "짜증": "anger",
    "혐오": "anger",

    # fear
    "fear": "fear",
    "afraid": "fear",
    "공포": "fear",
    "두려움": "fear",
    "불안": "fear",  # 논문 감정은 fear로만 받으니 불안은 여기서 fear로 합치는 게 현실적
    # excitement
    "excitement": "excitement",
    "excited": "excitement",
    "흥분": "excitement",
    "기대": "excitement",
    "설렘": "excitement",
    "기쁨": "excitement",
    "행복": "excitement",
    # 8-class에 있을 법한 라벨(보수적 fallback)
    "슬픔": "neutral",
    "놀라움": "excitement",
}


def _map_probs4_to_code(probs4: Any) -> Optional[str]:
    """
    pred4가 없고 probs4만 있을 때 fallback.
    로그 관찰상 probs4 최대값 인덱스가 pred4와 일치:
      index 1 -> F, index 2 -> A 로 보임.
    따라서 order를 [N, F, A, E] 로 가정.
    """
    try:
        if not isinstance(probs4, (list, tuple)) or len(probs4) != 4:
            return None
        order = ["N", "F", "A", "E"]
        mx_i = max(range(4), key=lambda i: float(probs4[i]))
        return order[mx_i]
    except Exception:
        return None


def normalize_emotion(
    label: Any,
    aliases: Optional[Dict[str, str]] = None,
) -> str:
    """
    입력 라벨을 논문 관측치(4감정)로 정규화.
    - 이미 neutral/anger/fear/excitement면 그대로
    - 한국어/변형 라벨이면 aliases로 매핑
    - dict 구조(현재 orchestrator emotion 출력)도 지원:
        1) pred4 우선
        2) pred8이 '놀라움'이면 surprise_to 우선
        3) probs4만 있으면 argmax 기반 fallback
    """
    if label is None:
        raise ValueError("Emotion label is None")

    # ✅ 현재 로그처럼 emotion이 dict인 경우 처리
    if isinstance(label, dict):
        # 1) pred4 최우선
        if "pred4" in label and label["pred4"] is not None:
            return normalize_emotion(label["pred4"], aliases)

        # 2) pred8이 놀라움이면 surprise_to(=N/F/A/E) 우선
        pred8 = label.get("pred8")
        if isinstance(pred8, str) and pred8.strip() == "놀라움":
            st = label.get("surprise_to")
            if st is not None:
                return normalize_emotion(st, aliases)
            return normalize_emotion(pred8, aliases)

        # 3) probs4만 있는 경우 fallback (관찰 기반)
        code = _map_probs4_to_code(label.get("probs4"))
        if code is not None:
            return normalize_emotion(code, aliases)

        # 4) 그 외에는 pred8(있으면) 또는 str(dict)
        if pred8 is not None:
            return normalize_emotion(pred8, aliases)

        raise ValueError(f"Unsupported emotion dict (no usable keys): {label}")

    s = str(label).strip().lower()
    # 원래 키는 소문자/그대로, aliases는 다양한 케이스가 있으니 한 번 더 시도
    aliases = aliases or DEFAULT_EMOTION_ALIASES
    if s in EMO_TO_IDX:
        return s

    # aliases 키도 lower로 맞춰 탐색
    if s in aliases:
        mapped = aliases[s]
        mapped = str(mapped).strip().lower()
        if mapped not in EMO_TO_IDX:
            raise ValueError(f"Alias mapped to invalid emotion: {mapped}")
        return mapped

    # 혹시 aliases가 원본 케이스로 들어있을 수 있어 원문도 시도
    raw = str(label).strip()
    if raw in aliases:
        mapped = aliases[raw]
        mapped = str(mapped).strip().lower()
        if mapped not in EMO_TO_IDX:
            raise ValueError(f"Alias mapped to invalid emotion: {mapped}")
        return mapped

    raise ValueError(f"Unsupported emotion label: {label}. Must be one of {EMOTIONS} (or alias).")


@dataclass(frozen=True)
class ViterbiResult:
    obs: List[str]                    # 정규화된 감정 시퀀스 (len=T)
    states: List[str]                 # Viterbi 상태 시퀀스 (len=T)
    final_state: str                  # 마지막 상태
    state_counts: Dict[str, int]      # V1/V2/V3 개수
    v3_ratio: float                   # V3 비율
    logp: float                       # best path log-prob (log space)


def viterbi_decode(
    obs_seq: Sequence[str],
    *,
    emotion_aliases: Optional[Dict[str, str]] = None,
) -> ViterbiResult:
    """
    논문 고정 파라미터(A,B,π) 기반 Viterbi 디코딩.
    obs_seq: 감정 라벨 시퀀스(피해자 턴만).
    """
    if not obs_seq:
        raise ValueError("obs_seq is empty (no emotions to decode).")

    obs_norm: List[str] = [normalize_emotion(o, emotion_aliases) for o in obs_seq]

    N = len(STATES)
    T = len(obs_norm)

    # delta[t][j] = best log prob up to time t ending in state j
    # psi[t][j]   = argmax prev state index
    delta: List[List[float]] = [[float("-inf")] * N for _ in range(T)]
    psi: List[List[int]] = [[0] * N for _ in range(T)]

    o0 = EMO_TO_IDX[obs_norm[0]]
    for j in range(N):
        delta[0][j] = _safe_log(_PI[j]) + _safe_log(_B[j][o0])
        psi[0][j] = 0

    for t in range(1, T):
        ot = EMO_TO_IDX[obs_norm[t]]
        for j in range(N):
            best_i = 0
            best_val = float("-inf")
            for i in range(N):
                val = delta[t - 1][i] + _safe_log(A[i][j])
                if val > best_val:
                    best_val = val
                    best_i = i
            delta[t][j] = best_val + _safe_log(_B[j][ot])
            psi[t][j] = best_i

    last = max(range(N), key=lambda j: delta[T - 1][j])
    best_logp = delta[T - 1][last]

    # backtrack
    path_idx: List[int] = [last]
    for t in range(T - 1, 0, -1):
        path_idx.append(psi[t][path_idx[-1]])
    path_idx.reverse()

    state_seq: List[str] = [STATES[i] for i in path_idx]
    counts: Dict[str, int] = {s: 0 for s in STATES}
    for s in state_seq:
        counts[s] += 1

    v3_ratio = counts["V3"] / float(T)
    final_state = state_seq[-1]

    return ViterbiResult(
        obs=obs_norm,
        states=state_seq,
        final_state=final_state,
        state_counts=counts,
        v3_ratio=v3_ratio,
        logp=best_logp,
    )


def attach_vulnerability_states_to_turns(
    turns: List[Dict],
    victim_turn_indices: Sequence[int],
    viterbi_states: Sequence[str],
    *,
    out_key: str = "vulnerability_state",
) -> None:
    """
    피해자 턴에만 Viterbi 상태(V1/V2/V3)를 턴 dict에 부착(인플레이스).
    - turns: 전체 턴 리스트
    - victim_turn_indices: 피해자 턴의 원래 인덱스들
    - viterbi_states: 해당 피해자 턴들과 동일 길이의 V1/V2/V3 시퀀스
    """
    if len(victim_turn_indices) != len(viterbi_states):
        raise ValueError("victim_turn_indices and viterbi_states length mismatch.")

    for idx, st in zip(victim_turn_indices, viterbi_states):
        if st not in STATE_TO_IDX:
            raise ValueError(f"Invalid HMM state: {st}")
        if 0 <= idx < len(turns):
            turns[idx][out_key] = st
