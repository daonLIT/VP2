# app/services/llm_providers.py
import os
from typing import Optional
from typing import Union
from app.core.config import settings
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI


def _openai_like_chat(model: str,
                      base_url: str,
                      api_key: str,
                      temperature: float = 0.7):
    return ChatOpenAI(model=model,
                      base_url=base_url,
                      api_key=api_key,
                      temperature=temperature,
                      timeout=600000)


def openai_chat(model: Optional[str] = None, temperature: float = 0.7):
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")

    mdl = model or settings.ADMIN_MODEL
    is_o_series = mdl.strip().lower().startswith(
        "o")  # "o4-mini", "o3-mini", "o1" 등

    if is_o_series:
        # ❗ 기본값(0.7)이 실수로 들어가지 않게 temperature=1을 **명시적으로** 전달
        return ChatOpenAI(
            model=mdl,
            temperature=1,  # ← 이것이 핵심
            api_key=settings.OPENAI_API_KEY,
            timeout=6000)
    else:
        return ChatOpenAI(model=mdl,
                          temperature=temperature,
                          api_key=settings.OPENAI_API_KEY,
                          timeout=600000)


# STOP_SAFE_DEFAULT = "gpt-4o-2024-08-06"  # ReAct/stop 호환 안정판


def agent_chat(model: str | None = None, temperature: float = 0.2):
    name = (model or getattr(settings, "AGENT_MODEL", None))
    if not name:
        raise RuntimeError("AGENT_MODEL not set")

    # ✅ o-series는 temperature를 잘못 넣으면 기대와 다르게 동작할 수 있으니
    # openai_chat()와 동일한 정책으로 맞춘다.
    is_o_series = name.strip().lower().startswith("o")
    if is_o_series:
        temperature = 1

    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")

    return ChatOpenAI(
        model=name,
        temperature=temperature,  # non-o 모델은 0~0.3 권장
        api_key = settings.OPENAI_API_KEY,
        timeout=600000,
    )


# def agent_chat():
#     return ChatOpenAI(
#         model=getattr(settings, "AGENT_MODEL", "o4-mini"),
#         temperature=1,
#         timeout=600000,
#     )


def gemini_chat(model: Optional[str] = None, temperature: float = 0.7):
    if not settings.GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY not set")
    return ChatGoogleGenerativeAI(
        model=model or "gemini-2.5-flash-lite",
        google_api_key=settings.GOOGLE_API_KEY,
        temperature=temperature,
        timeout=600000,
    )


def attacker_chat():
    # gpt-4.1-mini는 temperature 조절 가능
    return openai_chat(settings.ATTACKER_MODEL, temperature=0.7)


def victim_chat():
    provider = getattr(settings, "VICTIM_PROVIDER", "gemini").lower()
    model = settings.VICTIM_MODEL
    if provider == "gemini":
        return gemini_chat(model, temperature=0.7)
    if provider == "local":
        if not settings.LOCAL_BASE_URL:
            raise RuntimeError("LOCAL_BASE_URL not set for local provider")
        return _openai_like_chat(model,
                                 settings.LOCAL_BASE_URL,
                                 settings.LOCAL_API_KEY,
                                 temperature=0.7)
    elif provider == "openai":
        return openai_chat(model, temperature=0.7)
    else:
        raise ValueError(
            f"Unsupported VICTIM_PROVIDER: {provider}. Use 'openai' or 'gemini'."
        )


def admin_chat():
    # o4-mini 경로 → temperature=1이 강제되도록 openai_chat 내부 분기 사용
    return openai_chat(settings.ADMIN_MODEL)
