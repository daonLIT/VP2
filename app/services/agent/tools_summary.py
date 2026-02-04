# VP/app/services/agent/tools_summary.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
from langchain_core.tools import tool

import ast
import hashlib
import inspect
import json
import asyncio # Add asyncio import

from app.core.logging import get_logger
from app.services.llm_providers import agent_chat

logger = get_logger(__name__)

# (1) 결과 캐시: 같은 입력이면 재계산 방지
SUMMARY_CACHE: Dict[str, Dict[str, Any]] = {}
# (2) 입력 캐시: "긴 payload"를 저장해두고 cache_key만 tool에 넘기기
SUMMARY_INPUT_CACHE: Dict[str, Dict[str, Any]] = {}

MAX_INPUT_CACHE_ITEMS = 2000


SUMMARY_SYSTEM_PROMPT = """
너는 보이스피싱 시뮬레이션의 "라운드별 요약 생성기"다.

규칙:
- 절대 사실을 지어내지 말고, 입력에 있는 정보만 사용해라.
- 각 라운드 요약에는: 대화(공격자/피해자), 감정(emotion), 판단(judgement)을 반영해라.
- guidance(지침)는 "현재 라운드"의 것은 아직 생성 전이므로 절대 추측하거나 작성하지 마라.
- 출력은 반드시 JSON만 반환해라. (설명 문장 금지)

반환 JSON 스키마:
{
  "victim_overview": "피해자 정보를 줄글로 요약",
  "round_summaries": [
    {
      "round": 1,
      "text": "라운드 1 요약(줄글 2~4문장)",
      "has_guidance": true
    }
  ],
  "summary_text": "victim_overview + 라운드별 요약을 합친 프롬프트용 줄글(너무 길지 않게)"
}
""".strip()


def _call_with_supported_kwargs(fn, **kwargs):
    sig = inspect.signature(fn)
    supported = {}
    for k, v in kwargs.items():
        if k in sig.parameters:
            supported[k] = v
    return fn(**supported)


async def _agent_chat(messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
    """
    agent_chat이 sync/async 어느 쪽이든 안전하게 호출.
    """
    res = _call_with_supported_kwargs(agent_chat, messages=messages, temperature=temperature)
    if inspect.isawaitable(res):
        res = await res
    if not isinstance(res, str):
        res = str(res)
    return res


def _jsonish_load(x: Any) -> Dict[str, Any]:
    if x is None:
        return {}
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except Exception:
            pass
        try:
            v = ast.literal_eval(s)
            if isinstance(v, dict):
                return v
            return {"raw": x}
        except Exception:
            return {"raw": x}
    return {"raw": x}


def _clip(s: Any, n: int = 260) -> str:
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = s.strip()
    return s if len(s) <= n else (s[: n - 3] + "...")


def _extract_utterance(text_obj: Any) -> str:
    d = _jsonish_load(text_obj)
    for k in ("utterance", "content", "text", "message", "dialogue"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    if "raw" in d and isinstance(d["raw"], str):
        return d["raw"].strip()
    try:
        return json.dumps(d, ensure_ascii=False)
    except Exception:
        return str(d)


def _get_role(turn: Dict[str, Any]) -> str:
    return (turn.get("role") or turn.get("speaker") or turn.get("type") or "").strip()


def _is_offender(role: str) -> bool:
    r = role.lower()
    return r in ("offender", "attacker", "assistant_offender", "system_offender")


def _is_victim(role: str) -> bool:
    r = role.lower()
    return r in ("victim", "user", "human", "assistant_victim")


def _split_round_pairs(turns: List[Dict[str, Any]]) -> List[Tuple[Optional[int], int]]:
    pairs: List[Tuple[Optional[int], int]] = []
    last_off: Optional[int] = None

    for idx, t in enumerate(turns):
        role = _get_role(t)
        if _is_offender(role):
            last_off = idx
            continue
        if _is_victim(role):
            pairs.append((last_off, idx))
            last_off = None

    return pairs


def _normalize_by_round(items: Any) -> Dict[int, Any]:
    out: Dict[int, Any] = {}
    if not items:
        return out

    if isinstance(items, dict):
        for k, v in items.items():
            try:
                out[int(k)] = v
            except Exception:
                continue
        return out

    if isinstance(items, list):
        for i, it in enumerate(items, start=1):
            if isinstance(it, dict):
                r = it.get("round_no") or it.get("round") or it.get("round_id")
                if isinstance(r, int):
                    out[r] = it
                elif isinstance(r, str) and r.isdigit():
                    out[int(r)] = it
                else:
                    out[i] = it
            else:
                out[i] = it
        return out

    return out


def _extract_emotion_from_victim_text(victim_text: Dict[str, Any]) -> Any:
    # victim_text는 "victim_turn['text'] 문자열"을 파싱한 dict
    for k in ("emotion", "emotion_label", "emo", "label", "emotion_main", "emotion_top"):
        if k in victim_text:
            return victim_text.get(k)
    for k in ("hmm", "hmm_state", "v_state", "posterior", "state_probs"):
        if k in victim_text:
            return {"emotion": None, k: victim_text.get(k)}
    return None


def _extract_emotion_from_turn(vic_turn: Dict[str, Any], vic_text: Dict[str, Any]) -> Any:
    """
    ✅ 핵심 수정:
    - emotion/hmm이 victim_turn 최상위에 붙는 경우를 우선 지원
    - 없으면 기존처럼 victim_text 내부에서 찾는다
    """
    if isinstance(vic_turn.get("emotion"), dict) or isinstance(vic_turn.get("emotion"), str):
        return vic_turn.get("emotion")
    if isinstance(vic_turn.get("hmm"), dict):
        return {"emotion": None, "hmm": vic_turn.get("hmm")}
    return _extract_emotion_from_victim_text(vic_text)


def _extract_judgement_from_turn(vic_turn: Dict[str, Any], vic_text: Dict[str, Any]) -> Any:
    """
    judgement가 turn 최상위로 붙는 경우도 지원 (현재는 없지만 확장)
    """
    for k in ("judgement", "judge", "decision", "risk", "admin_judgement"):
        if k in vic_turn:
            return vic_turn.get(k)
        if k in vic_text:
            return vic_text.get(k)
    return None


class MakeSummaryInput(BaseModel):
    data: Dict[str, Any] = Field(..., description="summary 생성에 필요한 전체 입력")


class PutSummaryPayloadInput(BaseModel):
    payload_json_path: str = Field(..., description="대용량 JSON 페이로드가 저장된 임시 파일의 경로")
    cache_key: Optional[str] = Field(None, description="선택적 캐시 키")


def _make_input_cache_key(payload: Dict[str, Any]) -> str:
    case_id = payload.get("case_id")
    run_no = payload.get("run_no")
    round_no = payload.get("round_no") or payload.get("current_round")
    base = {"case_id": str(case_id), "run_no": run_no, "round_no": round_no}
    raw = json.dumps(base, ensure_ascii=False, sort_keys=True)
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{case_id}:{run_no}:{round_no}:{h}"


def _evict_if_needed() -> None:
    if len(SUMMARY_INPUT_CACHE) <= MAX_INPUT_CACHE_ITEMS:
        return
    over = len(SUMMARY_INPUT_CACHE) - MAX_INPUT_CACHE_ITEMS
    for k in list(SUMMARY_INPUT_CACHE.keys())[: max(over, 50)]:
        SUMMARY_INPUT_CACHE.pop(k, None)


def _build_round_packets(data: Dict[str, Any]) -> Tuple[int, List[Dict[str, Any]]]:
    turns: List[Dict[str, Any]] = data.get("turns") or []
    pairs = _split_round_pairs(turns)

    round_no = data.get("round_no") or data.get("current_round")
    if round_no is None:
        round_no = len(pairs)

    try:
        round_no = int(round_no)
    except Exception:
        round_no = len(pairs)

    max_round = min(round_no, len(pairs))

    emo_map = _normalize_by_round(data.get("emotions") or data.get("emotion_by_round"))
    jud_map = _normalize_by_round(data.get("judgements") or data.get("judgement_by_round"))
    gui_map = _normalize_by_round(data.get("guidances") or data.get("guidance_by_round"))

    packets: List[Dict[str, Any]] = []

    for r in range(1, max_round + 1):
        off_idx, vic_idx = pairs[r - 1]

        off_turn = turns[off_idx] if (off_idx is not None and off_idx < len(turns)) else {}
        vic_turn = turns[vic_idx] if (vic_idx is not None and vic_idx < len(turns)) else {}

        off_text = _jsonish_load(off_turn.get("text") or off_turn.get("content") or off_turn.get("utterance"))
        vic_text = _jsonish_load(vic_turn.get("text") or vic_turn.get("content") or vic_turn.get("utterance"))

        offender_utt = _clip(_extract_utterance(off_turn.get("text") or off_text))
        victim_utt = _clip(_extract_utterance(vic_turn.get("text") or vic_text))

        # ✅ emotion: round-map > victim_turn(top-level) > victim_text(in-text)
        emotion = emo_map.get(r)
        if emotion is None:
            emotion = _extract_emotion_from_turn(vic_turn, vic_text)

        # ✅ judgement: round-map > victim_turn/top-level > victim_text(in-text)
        judgement = jud_map.get(r)
        if judgement is None:
            judgement = _extract_judgement_from_turn(vic_turn, vic_text)

        # 현재 라운드 guidance는 절대 포함 금지
        guidance = None
        if r < max_round:
            guidance = gui_map.get(r)

        packets.append(
            {
                "round": r,
                "dialogue": {
                    "offender": offender_utt,
                    "victim": victim_utt,
                    "offender_meta": {
                        "proc_code": off_text.get("proc_code"),
                        "ppse_labels": off_text.get("ppse_labels"),
                    },
                },
                "emotion": emotion,
                "judgement": judgement,
                "guidance": guidance,
                "has_guidance": guidance is not None,
            }
        )

    return max_round, packets


def _make_cache_key(case_id: Any, run_no: Any, round_no: int, packets: List[Dict[str, Any]]) -> str:
    base = {"case_id": str(case_id), "run_no": run_no, "round_no": round_no, "packets": packets}
    raw = json.dumps(base, ensure_ascii=False, sort_keys=True)
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{case_id}:{run_no}:{round_no}:{h}"


@tool("summary.put_payload", args_schema=PutSummaryPayloadInput)
def put_payload(payload_json_path: str, cache_key: Optional[str] = None) -> Dict[str, Any]:
    """
    "무거운" payload를 캐시하고 cache_key를 반환합니다.

    대용량 JSON 페이로드를 임시 파일에 저장한 후, 해당 파일의 경로를 이 툴에 전달하여
    페이로드를 인메모리 캐시에 저장하고 cache_key를 반환합니다.

    Args:
        payload_json_path: 대용량 JSON 페이로드가 저장된 임시 파일의 경로.
        cache_key: 선택적 캐시 키. 제공되지 않으면 자동으로 생성됩니다.
    """
    try:
        # 파일에서 JSON 페이로드 읽기
        with open(payload_json_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)

        if not isinstance(payload, dict):
            return {"ok": False, "error": "invalid_payload_in_file", "hint": "파일 내용이 유효한 JSON 딕셔너리가 아닙니다."}

        if not cache_key:
            cache_key = _make_input_cache_key(payload)

        SUMMARY_INPUT_CACHE[cache_key] = payload
        _evict_if_needed()
        return {"ok": True, "cache_key": cache_key}
    except Exception as e:
        logger.exception("summary.put_payload failed")
        return {"ok": False, "error": str(e), "message": f"캐시 저장 중 오류 발생: {e}"}


def _template_make_output(victim_profile: Dict[str, Any], packets: List[Dict[str, Any]], round_no: int) -> Dict[str, Any]:
    meta = victim_profile.get("meta") or {}
    knowledge = victim_profile.get("knowledge") or {}

    if not meta and not knowledge:
        victim_overview = "피해자에 대한 정보는 제공되지 않았다."
    else:
        victim_overview = (f"피해자 정보: {meta}. 피해자 배경/지식: {knowledge}.").strip()

    round_summaries: List[Dict[str, Any]] = []
    lines: List[str] = [victim_overview, "", "라운드별 요약:"]

    for p in packets:
        r = p["round"]
        d = p["dialogue"]
        emo = p.get("emotion")
        jud = p.get("judgement")
        gui = p.get("guidance")

        text_parts = [
            f"공격자는 '{d['offender']}'라고 말했고, 피해자는 '{d['victim']}'라고 반응함.",
            f"감정: {emo}. 판단: {jud}.",
        ]
        if gui is not None:
            text_parts.append(f"이 라운드까지의 지침: {gui}.")

        text = " ".join(text_parts)
        round_summaries.append({"round": r, "text": text, "has_guidance": gui is not None})
        lines.append(f"라운드 {r} 요약: {text}")

    summary_text = "\n".join(lines).strip()

    return {
        "victim_overview": victim_overview,
        "round_summaries": round_summaries,
        "summary_text": summary_text,
        "round_no": round_no,
        "mode": "template",
    }


@tool("summary.make_round_summaries", args_schema=MakeSummaryInput)
async def make_round_summaries(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    매 라운드 호출되는 요약 툴.

    요구:
    - round_no=r일 때, 출력은 반드시 [1..r] 라운드 요약을 모두 포함
    - guidance는 1..(r-1)까지만 포함 (r의 guidance는 생성 전)

    입력(data) 권장 키:
    - case_id, run_no, round_no
    - victim_profile
    - turns: 누적 대화로그
    - emotions / judgements / guidances: 라운드별 정보(있으면)
    - mode: "llm" | "template" (기본: llm)
    """
    try:
        # cache_key 모드
        cache_key = data.get("cache_key")
        override_mode = data.get("mode")
        override_temp = data.get("temperature")
        if cache_key:
            cached = SUMMARY_INPUT_CACHE.get(cache_key)
            if not isinstance(cached, dict):
                return {"ok": False, "error": "cache_miss", "cache_key": cache_key}
            # ✅ 캐시 payload를 기본으로 쓰되, 호출자가 준 최소 옵션만 덮어쓰기 허용
            data = dict(cached)
            if override_mode is not None:
                data["mode"] = override_mode
            if override_temp is not None:
                data["temperature"] = override_temp

        case_id = data.get("case_id")
        run_no = data.get("run_no")
        victim_profile = data.get("victim_profile") or {}

        round_no, packets = _build_round_packets(data)

        key = _make_cache_key(case_id, run_no, round_no, packets)
        if key in SUMMARY_CACHE and not data.get("no_cache"):
            return SUMMARY_CACHE[key]

        mode = (data.get("mode") or "llm").lower()

        if mode in ("template", "rule", "rules", "no_llm"):
            out = _template_make_output(victim_profile, packets, round_no)
            out["ok"] = True
            SUMMARY_CACHE[key] = out
            return out

        user_payload = {
            "round_no": round_no,
            "victim_profile": victim_profile,
            "round_packets": packets,
            "rules": {
                "no_current_round_guidance": True,
                "current_round": round_no,
                "guidance_included_rounds": f"1..{max(round_no - 1, 0)}",
            },
        }

        user_prompt = (
            "다음 입력(JSON)을 기반으로 라운드별 요약을 생성해라.\n"
            "주의: 현재 라운드의 guidance는 존재하지 않는 것으로 간주하고 절대 쓰지 마라.\n"
            "입력(JSON):\n"
            + json.dumps(user_payload, ensure_ascii=False)
        )

        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        raw = await _agent_chat(messages=messages, temperature=float(data.get("temperature", 0.2)))
        parsed = _jsonish_load(raw)

        if not isinstance(parsed, dict) or "round_summaries" not in parsed or "summary_text" not in parsed:
            logger.warning("summary LLM output invalid -> fallback template. raw=%s", str(raw)[:500])
            fb = _template_make_output(victim_profile, packets, round_no)
            fb["ok"] = True
            fb["mode"] = "fallback_template"
            SUMMARY_CACHE[key] = fb
            return fb

        out = {
            "ok": True,
            "mode": "llm",
            "round_no": round_no,
            "victim_overview": parsed.get("victim_overview", ""),
            "round_summaries": parsed.get("round_summaries", []),
            "summary_text": parsed.get("summary_text", ""),
        }

        # 안전: 현재 라운드 has_guidance 강제 False
        cleaned: List[Dict[str, Any]] = []
        for item in out["round_summaries"]:
            if not isinstance(item, dict):
                continue
            r = item.get("round")
            try:
                r = int(r)
            except Exception:
                continue
            has_g = bool(item.get("has_guidance"))
            if r == round_no:
                has_g = False
            cleaned.append({"round": r, "text": item.get("text", ""), "has_guidance": has_g})

        cleaned.sort(key=lambda x: x["round"])
        out["round_summaries"] = cleaned

        SUMMARY_CACHE[key] = out
        return out

    except Exception as e:
        logger.exception("summary.make_round_summaries failed")
        return {"ok": False, "error": str(e)}


def make_summary_tools() -> list:
    # ✅ 이름 통일: summary.make_round_summaries만 제공
    return [put_payload, make_round_summaries]
