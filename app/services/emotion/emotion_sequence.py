# app/services/emotion/emotion_sequence.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple, Set


def _extract_emotion_label(emo_val: Any) -> Optional[str]:
    """
    오케스트레이터 현재 출력 형태에 맞춘 감정 라벨 추출기.
    - emo_val이 str이면 그대로
    - emo_val이 dict이면:
        1) pred4 우선 (예: 'F', 'A', 'N', 'E')
        2) pred8 == '놀라움'이면 surprise_to 우선 (예: 'F'/'A'/'N'/'E')
        3) 그 외 pred8 반환
    """
    if emo_val is None:
        return None

    if isinstance(emo_val, str):
        s = emo_val.strip()
        return s if s else None

    if isinstance(emo_val, dict):
        pred4 = emo_val.get("pred4")
        if pred4 is not None:
            s = str(pred4).strip()
            return s if s else None

        pred8 = emo_val.get("pred8")
        if isinstance(pred8, str) and pred8.strip() == "놀라움":
            st = emo_val.get("surprise_to")
            if st is not None:
                s = str(st).strip()
                return s if s else None
            return "놀라움"

        if pred8 is not None:
            s = str(pred8).strip()
            return s if s else None

        return None

    # fallback: 기타 타입은 문자열화
    s = str(emo_val).strip()
    return s if s else None


def extract_victim_emotion_sequence(
    turns: Sequence[Dict[str, Any]],
    *,
    # 스키마가 프로젝트마다 다를 수 있어서 key 후보를 여러 개 지원
    role_keys: Sequence[str] = ("speaker", "role", "who", "agent"),
    victim_roles: Sequence[str] = ("victim", "user", "customer"),
    emotion_keys: Sequence[str] = ("emotion", "emotion_label", "emotionTag", "emotion_tag"),
    # 감정이 None/빈값이면 스킵
    drop_empty: bool = True,
) -> Tuple[List[str], List[int]]:
    """
    전체 턴 목록에서 '피해자 턴'만 골라 감정 시퀀스를 반환.
    return:
      - emotions: 피해자 감정 라벨 리스트 (원문 라벨; HMM에서 normalize 가능)
      - indices: emotions[i]가 turns[indices[i]]에 해당하는 원본 인덱스

    주의:
      - 논문 HMM 관측치는 4감정으로 고정이므로
        여기서는 "추출"만 하고, 4감정 정규화는 HMM 쪽에서 처리하는 구조가 안전함.
    """
    victim_set: Set[str] = {str(v).strip().lower() for v in victim_roles}

    def _get_first(d: Dict[str, Any], keys: Sequence[str]) -> Optional[Any]:
        for k in keys:
            if k in d:
                return d.get(k)
        return None

    emotions: List[str] = []
    indices: List[int] = []

    for i, t in enumerate(turns):
        role_val = _get_first(t, role_keys)
        role = str(role_val).strip().lower() if role_val is not None else ""

        if role not in victim_set:
            continue

        emo_val = _get_first(t, emotion_keys)
        emo_str = _extract_emotion_label(emo_val)
        if emo_str is None:
            continue
        if drop_empty and not str(emo_str).strip():
            continue

        emotions.append(emo_str)
        indices.append(i)

    return emotions, indices


def extract_victim_text_sequence(
    turns: Sequence[Dict[str, Any]],
    *,
    role_keys: Sequence[str] = ("speaker", "role", "who", "agent"),
    victim_roles: Sequence[str] = ("victim", "user", "customer"),
    text_keys: Sequence[str] = ("text", "utterance", "content", "message"),
    drop_empty: bool = True,
) -> Tuple[List[str], List[int]]:
    """
    (선택) 디버깅/리포트용: 피해자 발화(text) 시퀀스도 함께 뽑고 싶을 때 사용.
    """
    victim_set: Set[str] = {str(v).strip().lower() for v in victim_roles}

    def _get_first(d: Dict[str, Any], keys: Sequence[str]) -> Optional[Any]:
        for k in keys:
            if k in d:
                return d.get(k)
        return None

    texts: List[str] = []
    indices: List[int] = []

    for i, t in enumerate(turns):
        role_val = _get_first(t, role_keys)
        role = str(role_val).strip().lower() if role_val is not None else ""

        if role not in victim_set:
            continue

        txt_val = _get_first(t, text_keys)
        if txt_val is None:
            continue

        txt_str = str(txt_val).strip()
        if drop_empty and not txt_str:
            continue

        texts.append(txt_str)
        indices.append(i)

    return texts, indices
