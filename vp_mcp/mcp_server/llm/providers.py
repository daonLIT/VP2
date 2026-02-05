# vp_mcp/mcp_server/llm/providers.py
from __future__ import annotations
from typing import Optional, Dict, Any, List

import re
import os
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.language_models.chat_models import BaseChatModel
from app.core.logging import get_logger
logger = get_logger(__name__)

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI

# ─────────────────────────────────────────────────────────
# 내부 유틸 (app/services/llm_providers.py 로직을 MCP에 맞게 이식)
# ─────────────────────────────────────────────────────────

# STOP_SAFE_DEFAULT = "gpt-4o-2024-08-06"  # 안정판 (참고용)

def _openai_like_chat(model: str, base_url: str, api_key: str, temperature: float = 0.7) -> BaseChatModel:
    """
    OpenAI 호환 엔드포인트(로컬 서버 등)에 붙을 때 사용.
    """
    if not api_key:
        raise RuntimeError("LOCAL_API_KEY not set for local provider")
    if not base_url:
        raise RuntimeError("LOCAL_BASE_URL not set for local provider")
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        timeout=600000,
    )

def _openai_chat(model: Optional[str] = None, temperature: float = 0.7) -> BaseChatModel:
    """
    OpenAI 정식 엔드포인트. o-시리즈는 temperature=1로 강제 (네 기존 코드 반영)
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    mdl = (model or os.getenv("ADMIN_MODEL")).strip()
    is_o_series = mdl.lower().startswith("o")  # "o4-mini", "o3-mini", "o1" 등

    if is_o_series:
        # 응답 API 계열은 temp=1 명시
        return ChatOpenAI(model=mdl, temperature=1, api_key=api_key, timeout=6000)
    else:
        return ChatOpenAI(model=mdl, temperature=temperature, api_key=api_key, timeout=600000)

def _gemini_chat(model: Optional[str] = None, temperature: float = 0.7) -> BaseChatModel:
    """
    Google Gemini.
    """
    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")
    return ChatGoogleGenerativeAI(
        model=model or "gemini-2.5-flash-lite",
        google_api_key=api_key,
        temperature=temperature,
        timeout=600000,
    )

def attacker_chat(model: Optional[str] = None, temperature: float = 0.7) -> BaseChatModel:
    """
    공격자 LLM. 기본은 OpenAI.
    """
    mdl = model or os.getenv("ATTACKER_MODEL") or "gpt-4o-mini-2024-07-18"
    return _openai_chat(mdl, temperature=temperature)

def victim_chat() -> BaseChatModel:
    """
    피해자 LLM. provider 선택 가능: openai | gemini | local
    - local 은 OpenAI 호환 서버 (base_url + api_key 필요)
    """
    provider = (os.getenv("VICTIM_PROVIDER", "gemini") or "gemini").lower()
    model = os.getenv("VICTIM_MODEL")

    if provider == "gemini":
        return _gemini_chat(model, temperature=0.7)
    if provider == "local":
        return _openai_like_chat(
            model,
            os.getenv("LOCAL_BASE_URL", ""),
            os.getenv("LOCAL_API_KEY", ""),
            temperature=0.7,
        )
    if provider == "openai":
        return _openai_chat(model, temperature=0.7)

    raise ValueError(f"Unsupported VICTIM_PROVIDER: {provider}. Use 'openai' | 'gemini' | 'local'.")

def agent_chat(model: Optional[str] = None, temperature: float = 0.2) -> BaseChatModel:
    """
    에이전트/플래너용 (여기서는 필요시 사용). ReAct는 저온 권장.
    alias 맵은 필요하면 추가.
    """
    name = model or os.getenv("AGENT_MODEL")
    alias_map = {
        "o4-mini": "gpt-4o-mini-2024-07-18",
        "o4": "gpt-4o-2024-08-06",
    }
    name = alias_map.get(name, name)
    return _openai_chat(name, temperature=temperature)

# ─────────────────────────────────────────────────────────
# 시뮬용 래퍼 클래스 (simulate_dialogue.py에서 사용)
# ─────────────────────────────────────────────────────────

def _compose_turn_prompt(
    role: str,
    *,
    last_peer_text: str,
    current_step: str,
    guidance: str,
    guidance_type: str,
    victim_meta: Optional[Dict[str, Any]] = None,
    victim_knowledge: Optional[Dict[str, Any]] = None,
    victim_traits: Optional[Dict[str, Any]] = None,
    # ✅ (옵션) planner/realizer 지원
    # - planner: previous_turns_block(이전 대화 블록)을 보고 proc_code를 선택하게 할 때 사용
    # - realizer: proc_code를 고정해 발화를 생성하게 할 때 사용
    previous_turns_block: str = "",
    proc_code: str = "",
) -> str:
    """
    한 턴 프롬프트 합성 (공격자/피해자 공통)
    """
    # ✅ Planner/Realizer 휴리스틱 판별
    # - simulate_dialogue.py에서 planner 호출은 guidance="" / guidance_type="" / proc_code="" 로 들어옴
    # - realizer 호출은 proc_code가 채워져 들어오는 것이 정상
    #
    # 목적:
    # - Planner는 "proc_code만 JSON으로" 내야 하므로,
    #   공통 instruction("한 턴 대사") 같은 문구가 끼면 출력이 오염될 수 있음.
    is_planner_call = (
        role == "attacker"
        and (guidance or "").strip() == ""
        and (guidance_type or "").strip() == ""
        and (proc_code or "").strip() == ""
    )
    lines = []
    # planner/realizer용 추가 슬롯 (필요할 때만 포함)
    if previous_turns_block:
        lines.append(f"[Previous Turns]\n{previous_turns_block}")
    if proc_code:
        lines.append(f"[Fixed Proc Code]\n{proc_code}")
    if current_step:
        lines.append(f"[Step]\n{current_step}")
    if guidance:
        lines.append(f"[Guidance type={guidance_type or '-'}]\n{guidance}")
    if last_peer_text:
        peer = "Victim" if role == "attacker" else "Offender"
        lines.append(f"[Last {peer}]\n{last_peer_text}")
    if role == "victim":
        if victim_meta:
            lines.append(f"[Victim Meta]\n{victim_meta}")
        if victim_knowledge:
            lines.append(f"[Victim Knowledge]\n{victim_knowledge}")
        if victim_traits:
            lines.append(f"[Victim Traits]\n{victim_traits}")

    # ✅ Planner에서는 instruction을 제거/완화
    # - Planner: system 프롬프트 규칙(=proc_code JSON only)을 방해하지 않게 함
    # - Realizer/Victim: 기존대로 "한 턴" 지시 유지
    if is_planner_call:
        lines.append(
            "[Instruction]\n"
            "Follow the SYSTEM rules exactly. Output ONLY the required JSON. No extra text."
        )
    else:
        lines.append("[Instruction]\nRespond with one concise turn, staying in character.")
    return "\n\n".join(lines)

def _infer_attacker_phase(*, guidance: str, guidance_type: str, proc_code: str) -> str:
    """
    simulate_dialogue.py의 호출 규약에 기반한 휴리스틱:
    - Planner call: guidance="", guidance_type="", proc_code=""
    - Realizer call: proc_code가 채워져 들어오는 것이 정상
    (혹시 예외가 있으면 'attacker'로 표기)
    """
    g = (guidance or "").strip()
    gt = (guidance_type or "").strip()
    pc = (proc_code or "").strip()
    if g == "" and gt == "" and pc == "":
        return "planner"
    if pc != "":
        return "realizer"
    return "attacker"

class _BaseLLM:
    def __init__(self, *, model: str, system: str, temperature: float):
        self.model_name = model
        self.temperature = temperature
        self.system = system

        # 모델명 접두로 프로바이더 선택 (app 코드 정책 반영)
        m = (model or "").lower()
        if m.startswith(("gpt", "o", "openai")):
            provider = "openai"
            self.llm: BaseChatModel = _openai_chat(model, temperature)
        elif m.startswith("gemini"):
            provider = "gemini"
            self.llm = _gemini_chat(model, temperature)
        else:
            # 기본은 OpenAI
            provider = "openai(default)"
            self.llm = _openai_chat(model or "gpt-4o-mini-2024-07-18", temperature)
        logger.info(f"[LLM:init] model={model} provider={provider} temperature={temperature}")

    def _invoke(self, messages: List, *, phase: Optional[str] = None):
        if phase:
            logger.info(f"[LLM:invoke] phase={phase} model={self.model_name} len_messages={len(messages)}")
        else:
            logger.info(f"[LLM:invoke] model={self.model_name} len_messages={len(messages)}")
        res = self.llm.invoke(messages)
        out = getattr(res, "content", str(res)).strip()
        if phase:
            logger.info(f"[LLM:done] phase={phase} model={self.model_name} out_len={len(out)}")
        else:
            logger.info(f"[LLM:done] model={self.model_name} out_len={len(out)}")
        return out

class AttackerLLM(_BaseLLM):
    def next(
        self,
        *,
        history: List,                 # [AIMessage/HumanMessage ...] (공격자 퍼스펙티브)
        last_victim: str,
        current_step: str,
        guidance: str,
        guidance_type: str,
        # ✅ (옵션) planner/realizer 지원
        previous_turns_block: str = "",
        proc_code: str = "",
    ) -> str:
        messages: List = [SystemMessage(self.system)]
        messages.extend(history or [])
        prompt = _compose_turn_prompt(
            "attacker",
            last_peer_text=last_victim,
            current_step=current_step,
            guidance=guidance,
            guidance_type=guidance_type,
            previous_turns_block=previous_turns_block,
            proc_code=proc_code,
        )
        messages.append(HumanMessage(prompt))
        phase = _infer_attacker_phase(guidance=guidance, guidance_type=guidance_type, proc_code=proc_code)
        return self._invoke(messages, phase=phase)

class VictimLLM(_BaseLLM):
    def next(
        self,
        *,
        history: List,                 # [AIMessage/HumanMessage ...] (피해자 퍼스펙티브)
        last_offender: str,
        meta: Optional[Dict[str, Any]],
        knowledge: Optional[Dict[str, Any]],
        traits: Optional[Dict[str, Any]],
        guidance: str,
        guidance_type: str,
    ) -> str:
        messages: List = [SystemMessage(self.system)]
        messages.extend(history or [])
        prompt = _compose_turn_prompt(
            "victim",
            last_peer_text=last_offender,
            current_step="",
            guidance=guidance,
            guidance_type=guidance_type,
            victim_meta=meta,
            victim_knowledge=knowledge,
            victim_traits=traits,
        )
        messages.append(HumanMessage(prompt))
        # victim도 phase 태그를 달아 planner/realizer/victim 흐름을 로그에서 확정 가능하게 함
        return self._invoke(messages, phase="victim")
