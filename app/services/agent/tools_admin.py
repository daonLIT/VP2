# app/services/agent/tools_admin.py

from __future__ import annotations
from typing import Dict, Any, Optional, List, Tuple
from uuid import UUID

import os
import json
import ast
import httpx
import re

from pydantic import BaseModel, Field
from langchain_core.tools import tool
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.db import models as m
from app.core.logging import get_logger

# (중요) 요약/판정기는 "턴 리스트(JSON)"만으로 판정하도록 설계
# summarize_run_full(turns=List[Dict[str, Any]]) 시그니처를 권장
# 만약 기존 summarize_run_full이 (db, case_id, run_no)만 받는다면,
# 해당 파일도 turns 기반 시그니처로 업데이트하세요.
from app.services.admin_summary import summarize_run_full  # turns 기반 사용 권장

# ★ 추가: LLM 호출용
from app.services.llm_providers import agent_chat

# ★★ 추가: 동적 지침 생성기(2안)
from app.services.agent.guidance_generator import DynamicGuidanceGenerator

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────
# 환경변수
# ─────────────────────────────────────────────────────────
MCP_BASE_URL = os.getenv("MCP_BASE_URL", "http://127.0.0.1:5177")  # 운영 시 외부 MCP 주소로 설정

# ─────────────────────────────────────────────────────────
# 공통: {"data": {...}} 입력 통일
# ─────────────────────────────────────────────────────────
class SingleData(BaseModel):
    data: Any = Field(..., description="이 안에 실제 페이로드를 담는다")


def _to_dict(obj: Any) -> Dict[str, Any]:
    """
    admin.* 툴에 들어오는 data를 dict로 정규화.
    """
    # Pydantic 모델 처리
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()

    # 이미 dict면 그대로 반환
    if isinstance(obj, dict):
        return obj

    # 문자열이 아니면 에러
    if not isinstance(obj, str):
        raise HTTPException(status_code=422, detail=f"data는 JSON 객체여야 합니다. got type: {type(obj).__name__}")

    s = obj.strip()

    # 빈 문자열 체크
    if not s:
        raise HTTPException(status_code=422, detail="data가 비어있습니다.")

    logger.info("[_to_dict] 입력 길이: %d자", len(s))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ★★★ 0-1단계: 전역 Invalid escape 사전 제거
    # 문자열 밖에서도 \} 같은 패턴을 } 로 변환
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def global_fix_invalid_escapes(text: str) -> str:
        """
        전역적으로 잘못된 이스케이프 패턴 제거.
        NOTE: 기존 코드는 정규식 문자열을 str.replace로 처리해 사실상 동작이 약했음.
        최소 침습으로 re.sub 사용.
        """
        # \} \] \) \{ \[ \( 형태에서 백슬래시 제거
        return re.sub(r'\\([}\]\)\{\[\(])', r'\1', text)

    s_global_fixed = global_fix_invalid_escapes(s)
    if s_global_fixed != s:
        logger.info("[_to_dict] 0-1단계: 전역 Invalid escape 제거 (%d → %d자)", len(s), len(s_global_fixed))
        s = s_global_fixed

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ★★★ 0-2단계: 깨진 JSON 구조 복구
    # rolevictimtext{...} → {"role": "victim", "text": "..."}
    # roleoffendertext{...} → {"role": "offender", "text": "..."}
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def fix_broken_json_structure(text: str) -> str:
        """깨진 JSON 구조 패턴을 올바른 형태로 복구"""
        # rolevictimtext{content} 패턴
        result = text

        pattern1 = re.compile(r'rolevictimtext\{([^}]*)\}', re.DOTALL)
        result = pattern1.sub(r'{"role": "victim", "text": "\1"}', result)

        pattern2 = re.compile(r'roleoffendertext\{([^}]*)\}', re.DOTALL)
        result = pattern2.sub(r'{"role": "offender", "text": "\1"}', result)

        pattern3 = re.compile(r'rolevictimtext', re.DOTALL)
        result = pattern3.sub(r'"role": "victim", "text": ', result)

        pattern4 = re.compile(r'roleoffendertext', re.DOTALL)
        result = pattern4.sub(r'"role": "offender", "text": ', result)

        return result

    s_structure_fixed = fix_broken_json_structure(s)
    if s_structure_fixed != s:
        logger.info("[_to_dict] 0-2단계: 깨진 JSON 구조 복구 (%d → %d자)", len(s), len(s_structure_fixed))
        s = s_structure_fixed

    def _try_parse(candidate: str) -> Optional[Dict[str, Any]]:
        """JSON 또는 literal_eval 시도 - 개선된 버전"""
        candidate = candidate.strip()
        if not candidate:
            return None

        # ★★★ 0. Invalid escape sequence 처리
        def fix_invalid_escapes(text: str) -> str:
            """잘못된 이스케이프 시퀀스 수정"""
            result: List[str] = []
            i = 0
            in_string = False

            while i < len(text):
                if text[i] == '"' and (i == 0 or text[i - 1] != '\\'):
                    in_string = not in_string
                    result.append(text[i])
                    i += 1
                elif in_string and text[i] == '\\' and i + 1 < len(text):
                    next_char = text[i + 1]
                    if next_char in '"\\/:bfnrtu':
                        result.append(text[i])
                        result.append(next_char)
                        i += 2
                    else:
                        result.append(next_char)
                        i += 2
                else:
                    result.append(text[i])
                    i += 1

            return ''.join(result)

        # 1. JSON 파싱
        try:
            v = json.loads(candidate)
            if isinstance(v, dict):
                return v
        except json.JSONDecodeError as e:
            if "Invalid" in str(e) and "escape" in str(e):
                try:
                    fixed = fix_invalid_escapes(candidate)
                    v = json.loads(fixed)
                    if isinstance(v, dict):
                        logger.info("[_try_parse] Invalid escape 수정 후 성공")
                        return v
                except json.JSONDecodeError:
                    pass

        # ★★★ 1-1. Invalid control character 에러 처리 (강화)
        try:
            cleaned = candidate.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
            if cleaned != candidate:
                v = json.loads(cleaned)
                if isinstance(v, dict):
                    logger.info("[_try_parse] 제어문자 이스케이프 후 성공")
                    return v
        except json.JSONDecodeError:
            pass

        # 2. literal_eval
        try:
            v = ast.literal_eval(candidate)
            if isinstance(v, dict):
                return v
        except (ValueError, SyntaxError):
            pass

        # 3. 코드펜스 제거 후 재파싱
        fence_pattern = re.compile(r'^```(?:json)?\s*(.*?)\s*```$', re.DOTALL)
        fence_match = fence_pattern.match(candidate)
        if fence_match:
            clean = fence_match.group(1).strip()
            try:
                v = json.loads(clean)
                if isinstance(v, dict):
                    return v
            except json.JSONDecodeError:
                pass

        # 4. 첫 { 부터 마지막 } 까지만 추출
        first_brace = candidate.find('{')
        last_brace = candidate.rfind('}')
        if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
            extracted = candidate[first_brace:last_brace + 1]
            if extracted != candidate:
                try:
                    v = json.loads(extracted)
                    if isinstance(v, dict):
                        return v
                except json.JSONDecodeError:
                    pass

        return None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ★★★ 0단계: 작은따옴표로 감싸진 JSON 값 정리
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def normalize_quoted_json(text: str) -> str:
        """
        "text": '{"key": "value"}' 같은 패턴을
        "text": "{\"key\": \"value\"}" 형태로 변환
        """
        pattern = r'("[^"]+"\s*:\s*)\'([^\']*(?:\'\'[^\']*)*)\'(?=\s*[,}\]])'

        def replace_func(match):
            prefix = match.group(1)
            content = match.group(2)
            content_escaped = content.replace('"', '\\"')
            return f'{prefix}"{content_escaped}"'

        return re.sub(pattern, replace_func, text)

    s_normalized = normalize_quoted_json(s)
    if s_normalized != s:
        logger.info("[_to_dict] 0단계: 작은따옴표 JSON 값 정규화")
        val = _try_parse(s_normalized)
        if val is not None:
            logger.info("[_to_dict] 0단계 성공")
            return val
        s = s_normalized

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 1단계: 전체 문자열 직접 파싱
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    val = _try_parse(s)
    if val is not None:
        logger.info("[_to_dict] 1단계 성공 (전체 문자열)")
        return val

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ★★★ 1-2단계: 제어문자 사전 정리 후 재시도
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    s_cleaned_ctrl = s.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
    if s_cleaned_ctrl != s:
        logger.info("[_to_dict] 1-2단계: 전체 문자열 제어문자 정리")
        val = _try_parse(s_cleaned_ctrl)
        if val is not None:
            logger.info("[_to_dict] 1-2단계 성공 (제어문자 정리)")
            return val
        s = s_cleaned_ctrl

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 2단계: "data" 키 뒤의 {...} 블록만 추출
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    data_keyword_pos = s.find('"data"')
    if data_keyword_pos == -1:
        data_keyword_pos = s.find("'data'")

    if data_keyword_pos != -1:
        colon_pos = s.find(":", data_keyword_pos)
        if colon_pos != -1:
            search_start = colon_pos + 1
            while search_start < len(s) and s[search_start] in ' \t\n\r':
                search_start += 1

            if search_start < len(s) and s[search_start] == '{':
                depth = 0
                end_pos = None
                in_string = False
                escape_next = False

                for i in range(search_start, len(s)):
                    ch = s[i]

                    if escape_next:
                        escape_next = False
                        continue

                    if ch == '\\':
                        escape_next = True
                        continue

                    if ch == '"':
                        in_string = not in_string
                        continue

                    if not in_string:
                        if ch == '{':
                            depth += 1
                        elif ch == '}':
                            depth -= 1
                            if depth == 0:
                                end_pos = i
                                break

                if end_pos is not None:
                    inner_block = s[search_start:end_pos + 1]
                    logger.info("[_to_dict] 2단계: data 블록 추출 (%d자)", len(inner_block))

                    val = _try_parse(inner_block)
                    if val is not None:
                        logger.info("[_to_dict] 2단계 성공 (data 블록 파싱)")
                        return {"data": val}

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 3단계: 전체에서 가장 큰 {...} 블록 추출
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    m_big = re.search(r"\{.*\}", s, re.DOTALL)
    if m_big:
        sub = m_big.group(0)
        logger.info("[_to_dict] 3단계: 정규식 추출 (%d자)", len(sub))
        val = _try_parse(sub)
        if val is not None:
            logger.info("[_to_dict] 3단계 성공")
            return val

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 4단계: 끝의 불필요한 문자(], }) 제거 시도
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    tmp = s
    for attempt in range(10):
        tmp = tmp.rstrip()
        if not tmp:
            break
        if tmp[-1] in ']}':
            tmp = tmp[:-1]
            val = _try_parse(tmp)
            if val is not None:
                logger.warning("[_to_dict] 4단계 성공 (끝 문자 %d개 제거)", attempt + 1)
                return val
        else:
            break

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 5단계: 중괄호 부족/과다 감지 및 수정 시도 (강화 버전)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    s_cleaned = s.rstrip()

    removed_total = 0
    for _ in range(5):
        if not s_cleaned:
            break
        s_cleaned = s_cleaned.rstrip()
        if s_cleaned and s_cleaned[-1] in ']}':
            s_cleaned = s_cleaned[:-1]
            removed_total += 1
        else:
            break

    if removed_total > 0 or s_cleaned != s:
        logger.info("[_to_dict] 5단계 전: 공백+괄호 %d개 제거", removed_total)
        val = _try_parse(s_cleaned)
        if val is not None:
            logger.warning("[_to_dict] 5단계 성공 (공백+괄호 제거)")
            return val
        s = s_cleaned

    # 문자열 내부 제외하고 카운트
    def count_brackets(text: str) -> tuple:
        open_b = 0
        close_b = 0
        open_sq = 0
        close_sq = 0
        in_string = False
        escape = False

        for ch in text:
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if not in_string:
                if ch == '{':
                    open_b += 1
                elif ch == '}':
                    close_b += 1
                elif ch == '[':
                    open_sq += 1
                elif ch == ']':
                    close_sq += 1

        return open_b, close_b, open_sq, close_sq

    open_braces, close_braces, open_brackets, close_brackets = count_brackets(s)

    logger.info(
        "[_to_dict] 5단계: 괄호 카운트 - 중괄호 열림:{%d} 닫힘:{%d}, 대괄호 열림:[%d] 닫힘:]%d]",
        open_braces, close_braces, open_brackets, close_brackets
    )

    s_fixed = s
    modifications = []

    if open_braces > close_braces:
        missing = open_braces - close_braces
        s_fixed = s_fixed + ('}' * missing)
        modifications.append(f"}} {missing}개")
        logger.info("[_to_dict] 5단계: 닫는 } %d개 추가 시도", missing)

    if open_brackets > close_brackets:
        missing = open_brackets - close_brackets
        s_fixed = s_fixed + (']' * missing)
        modifications.append(f"] {missing}개")
        logger.info("[_to_dict] 5단계: 닫는 ] %d개 추가 시도", missing)

    if modifications:
        logger.info("[_to_dict] 5단계: 괄호 보정 완료 (%s)", ", ".join(modifications))
        val = _try_parse(s_fixed)
        if val is not None:
            logger.warning("[_to_dict] 5단계 성공 (괄호 보정: %s)", ", ".join(modifications))
            return val
        s = s_fixed

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 6단계: 이스케이프 변환 시도
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if '\\n' in s or "\\'" in s or '\\t' in s:
        logger.info("[_to_dict] 6단계: 이스케이프 변환 시도")
        cleaned = s.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r').replace("\\'", "'")
        val = _try_parse(cleaned)
        if val is not None:
            logger.warning("[_to_dict] 6단계 성공 (이스케이프 변환)")
            return val

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ★★★ 7단계: 중첩된 JSON 문자열 처리 (신규)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def fix_nested_json_strings(text: str) -> str:
        """작은따옴표로 감싸진 JSON 값을 큰따옴표로 변환하고 내부 따옴표를 이스케이프"""
        result: List[str] = []
        i = 0

        while i < len(text):
            if i < len(text) - 3 and text[i] == '"':
                key_start = i
                i += 1
                while i < len(text) and text[i] != '"':
                    if text[i] == '\\':
                        i += 2
                    else:
                        i += 1

                if i < len(text):
                    i += 1  # 닫는 "
                    while i < len(text) and text[i] in ' \t\n\r':
                        i += 1

                    if i < len(text) and text[i] == ':':
                        result.append(text[key_start:i + 1])
                        i += 1
                        while i < len(text) and text[i] in ' \t\n\r':
                            result.append(text[i])
                            i += 1

                        if i < len(text) and text[i] == "'":
                            i += 1
                            escaped_value: List[str] = []

                            while i < len(text) and text[i] != "'":
                                if text[i] == '"':
                                    escaped_value.append('\\"')
                                elif text[i] == '\\' and i + 1 < len(text) and text[i + 1] == "'":
                                    escaped_value.append("'")
                                    i += 1
                                else:
                                    escaped_value.append(text[i])
                                i += 1

                            result.append('"')
                            result.extend(escaped_value)
                            result.append('"')

                            if i < len(text) and text[i] == "'":
                                i += 1
                            continue

            # ✅ 방어 가드: i가 len(text)로 점프한 뒤 text[i] 접근 방지
            if i >= len(text):
                break
            result.append(text[i])
            i += 1

        return ''.join(result)

    s_nested_fixed = fix_nested_json_strings(s)
    if s_nested_fixed != s:
        logger.info("[_to_dict] 7단계: 중첩 JSON 문자열 처리")
        val = _try_parse(s_nested_fixed)
        if val is not None:
            logger.warning("[_to_dict] 7단계 성공 (중첩 JSON 처리)")
            return val
        s = s_nested_fixed

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ★★★ 8단계: 모든 처리를 조합하여 최종 시도
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    logger.info("[_to_dict] 8단계: 종합 처리 시도")
    s_final = s

    s_final = global_fix_invalid_escapes(s_final)
    s_final = fix_broken_json_structure(s_final)
    s_final = normalize_quoted_json(s_final)
    s_final = fix_nested_json_strings(s_final)
    s_final = s_final.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')

    open_b, close_b, open_sq, close_sq = count_brackets(s_final)
    if open_b > close_b:
        s_final += '}' * (open_b - close_b)
    if open_sq > close_sq:
        s_final += ']' * (open_sq - close_sq)

    if s_final != s:
        val = _try_parse(s_final)
        if val is not None:
            logger.warning("[_to_dict] 8단계 성공 (종합 처리)")
            return val

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 모든 시도 실패
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    logger.error("[_to_dict] 모든 파싱 시도 실패")
    logger.error("[_to_dict] 입력 앞 500자: %s", s[:500])
    logger.error("[_to_dict] 입력 뒤 500자: %s", s[-500:])

    # JSON 구조 진단
    try:
        json.loads(s)
    except json.JSONDecodeError as e:
        logger.error("[_to_dict] JSON 파싱 에러: %s (위치: %d / 전체길이: %d)", e.msg, e.pos, len(s))

        context_start = max(0, e.pos - 100)
        context_end = min(len(s), e.pos + 100)
        logger.error("[_to_dict] 에러 위치 주변 (±100자):")
        logger.error("    %s", s[context_start:context_end])

        if "Unterminated string" in e.msg:
            logger.error("[_to_dict] ⚠️  문자열이 중간에 잘렸습니다")
            logger.error("[_to_dict] ⚠️  원인: 에이전트 출력 길이 제한 또는 LLM 응답 불완전")
            logger.error("[_to_dict] ⚠️  해결: max_iterations 증가 또는 데이터 분할 전송")

        if "Expecting" in e.msg:
            logger.error("[_to_dict] ⚠️  JSON 구조 오류: %s", e.msg)
            logger.error("[_to_dict] ⚠️  에이전트가 잘못된 JSON을 생성했을 가능성")

        if "Invalid control character" in e.msg:
            logger.error("[_to_dict] ⚠️  제어 문자 오류: JSON 문자열 내부에 이스케이프되지 않은 개행문자 존재")
            logger.error("[_to_dict] ⚠️  원인: 에이전트가 \\n 대신 실제 개행문자를 사용")
            logger.error("[_to_dict] ⚠️  해결: 0단계, 1-2단계, 7-8단계에서 자동 처리 시도했으나 실패")

        if "Invalid" in e.msg and "escape" in e.msg:
            logger.error("[_to_dict] ⚠️  잘못된 이스케이프 시퀀스: \\} 같은 잘못된 이스케이프 존재")
            logger.error("[_to_dict] ⚠️  원인: 에이전트가 유효하지 않은 이스케이프 시퀀스 생성")
            logger.error("[_to_dict] ⚠️  해결: _try_parse의 fix_invalid_escapes로 자동 처리 시도했으나 실패")

    raise HTTPException(
        status_code=422,
        detail="data는 JSON 객체여야 합니다. 파싱 실패."
    )


def _unwrap_data(obj: Any) -> Dict[str, Any]:
    """
    SingleData(data=...) 구조를 풀어서 실제 payload(dict)를 반환.
    - {"case_id": "...", "run_no": 1, ...}
    - {"data": {...}}
    - 'Action Input: {"data":{...}}'
    전부 허용.
    """
    d = _to_dict(obj)

    inner = d.get("data")
    if isinstance(inner, dict):
        return inner

    return d


def _normalize_kind(val: Any) -> str:
    if isinstance(val, str):
        s = val.strip()
        if s.startswith("{"):
            try:
                parsed = json.loads(s)
            except Exception:
                try:
                    parsed = ast.literal_eval(s)
                except Exception:
                    raise HTTPException(status_code=422, detail="kind 형식 오류")
            k = parsed.get("kind") or parsed.get("type")
            if isinstance(k, str):
                return k
        return s
    raise HTTPException(status_code=422, detail="kind는 문자열이어야 합니다.")


# ─────────────────────────────────────────────────────────
# 입력 스키마
# ─────────────────────────────────────────────────────────
class _JudgeReadInput(BaseModel):
    case_id: UUID
    run_no: int = Field(1, ge=1)


class _JudgeMakeInput(BaseModel):
    case_id: UUID
    run_no: int = Field(1, ge=1)
    # 오케스트레이터가 바로 턴을 넘겨줄 수 있게 허용
    turns: Optional[List[Dict[str, Any]]] = None
    log: Optional[Dict[str, Any]] = None


class _GuidanceInput(BaseModel):
    kind: str = Field(..., pattern="^(P|A)$", description="지침 종류: 'P'(피해자) | 'A'(공격자)")


class _SavePreventionInput(BaseModel):
    case_id: UUID
    offender_id: int
    victim_id: int
    run_no: int = Field(1, ge=1)
    summary: str
    steps: List[str] = Field(default_factory=list)


# ★ 추가: 최종예방책 생성 입력
class _MakePreventionInput(BaseModel):
    case_id: UUID
    rounds: int = Field(..., ge=1)
    turns: List[Dict[str, Any]] = Field(default_factory=list)
    judgements: List[Dict[str, Any]] = Field(default_factory=list)
    guidances: List[Dict[str, Any]] = Field(default_factory=list)
    # 포맷은 고정적으로 personalized_prevention을 기대
    format: str = Field("personalized_prevention")


# ─────────────────────────────────────────────────────────
# 터미널 조건(라운드5 또는 critical) 판단 헬퍼
# ─────────────────────────────────────────────────────────
def _is_terminal_case(rounds: int, judgements: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """
    rounds 가 3 이상이거나, judgements 중 risk.level == 'critical' 이 하나라도 있으면 터미널로 간주.
    return: (is_terminal, reason)  # reason in {"round3", "critical", "not_terminal"}
    """
    logger.info(f"[_is_terminal_case] rounds={rounds}, judgements count={len(judgements or [])}")

    try:
        if rounds >= 3:
            return True, "round3"

        for idx, j in enumerate(judgements or []):
            logger.info(f"[_is_terminal_case] judgement[{idx}]: {j}")

            risk = j.get("risk")
            logger.info(f"[_is_terminal_case] risk={risk}")

            if risk:
                lvl = str(risk.get("level", "")).lower()
                logger.info(f"[_is_terminal_case] level={lvl}")

                if lvl == "critical":
                    logger.info(f"[_is_terminal_case] ✓ CRITICAL 발견!")
                    return True, "critical"
    except Exception as e:
        logger.error(f"[_is_terminal_case] Exception: {e}")

    return False, "not_terminal"


# ─────────────────────────────────────────────────────────
# MCP에서 대화 턴(JSON) 가져오기
# ─────────────────────────────────────────────────────────
def _fetch_turns_from_mcp(case_id: UUID, run_no: int) -> List[Dict[str, Any]]:
    """
    MCP가 제공하는 대화로그(JSON) 엔드포인트에서 특정 라운드의 전체 턴을 받아온다.
    기대 형식: [{"role": "attacker"|"victim"|"system", "text": "...", "meta": {...}}, ...]
    기본 엔드포인트 가정: GET {MCP_BASE_URL}/api/cases/{case_id}/turns?run={run_no}
    """
    url = f"{MCP_BASE_URL}/api/cases/{case_id}/turns"
    params = {"run": run_no}
    try:
        with httpx.Client(timeout=30) as client:
            r = client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.error(f"[MCP] 대화 로그 조회 실패: {e}")
        raise HTTPException(status_code=502, detail=f"MCP 대화로그 조회 실패: {e}")

    turns: Any = None
    if isinstance(data, dict):
        if "turns" in data:
            turns = data["turns"]
        elif "result" in data and isinstance(data["result"], dict) and "turns" in data["result"]:
            turns = data["result"]["turns"]
        else:
            if all(isinstance(v, list) for v in data.values()):
                turns = next(iter(data.values()))
    elif isinstance(data, list):
        turns = data

    if not isinstance(turns, list):
        raise HTTPException(status_code=502, detail="MCP 응답에서 turns 배열을 찾을 수 없습니다.")
    return turns  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────
# 판정 결과 저장 / 조회 (DB는 결과 저장·조회에만 사용)
# ─────────────────────────────────────────────────────────
def _persist_verdict(
    db: Session,
    *,
    case_id: UUID,
    run_no: int,
    verdict: Dict[str, Any],
) -> bool:
    """
    verdict 예:
      {
        "phishing": False,
        "evidence": "...",
        "risk": {"score": 10, "level": "low", "rationale": "..."},
        "victim_vulnerabilities": [...],
        "continue": {"recommendation": "continue", "reason": "..."}
      }
    """
    success = False

    # 1) AdminCaseSummary가 있으면 라운드별로 저장/업서트
    try:
        if hasattr(m, "AdminCaseSummary"):
            Model = m.AdminCaseSummary
            row = (
                db.query(Model)
                .filter(Model.case_id == case_id, Model.run == run_no)
                .first()
            )
            if not row:
                row = Model(case_id=case_id, run=run_no)
                db.add(row)

            row.phishing = bool(verdict.get("phishing", False))

            if hasattr(Model, "evidence"):
                setattr(row, "evidence", str(verdict.get("evidence", ""))[:4000])

            risk = verdict.get("risk") or {}
            if hasattr(Model, "risk_score"):
                setattr(row, "risk_score", int(risk.get("score", 0) or 0))
            if hasattr(Model, "risk_level"):
                setattr(row, "risk_level", str(risk.get("level", "") or ""))
            if hasattr(Model, "risk_rationale"):
                setattr(row, "risk_rationale", str(risk.get("rationale", "") or "")[:2000])

            if hasattr(Model, "vulnerabilities"):
                setattr(row, "vulnerabilities", verdict.get("victim_vulnerabilities", []))
            if hasattr(Model, "verdict_json"):
                setattr(row, "verdict_json", verdict)

            success = True
    except Exception as e:
        logger.warning(f"[admin.make_judgement] AdminCaseSummary 저장/업데이트 실패: {e}")

    # 2) 항상 AdminCase에 최신 요약 + 히스토리 라인 누적
    try:
        case = db.get(m.AdminCase, case_id)
        if not case:
            try:
                case = m.AdminCase(
                    id=case_id,
                    scenario={},
                    phishing=False,
                    status="running",
                    defense_count=0,
                )
                db.add(case)
                db.flush()
            except Exception as e:
                logger.warning(f"[admin.make_judgement] AdminCase 생성 실패: {e}")
                if success:
                    try:
                        db.commit()
                    except Exception:
                        pass
                return success

        case.phishing = bool(getattr(case, "phishing", False) or verdict.get("phishing", False))

        risk = verdict.get("risk") or {}
        cont = verdict.get("continue") or {}

        if hasattr(case, "last_run_no"):
            case.last_run_no = run_no
        if hasattr(case, "last_risk_score"):
            case.last_risk_score = int(risk.get("score", 0) or 0)
        if hasattr(case, "last_risk_level"):
            case.last_risk_level = str(risk.get("level", "") or "")
        if hasattr(case, "last_risk_rationale"):
            case.last_risk_rationale = str(risk.get("rationale", "") or "")
        if hasattr(case, "last_vulnerabilities"):
            case.last_vulnerabilities = verdict.get("victim_vulnerabilities", [])
        if hasattr(case, "last_recommendation"):
            case.last_recommendation = str(cont.get("recommendation", "") or "")
        if hasattr(case, "last_recommendation_reason"):
            case.last_recommendation_reason = str(cont.get("reason", "") or "")

        prev = (case.evidence or "").strip()
        piece = json.dumps({"run": run_no, "verdict": verdict}, ensure_ascii=False)
        case.evidence = (prev + ("\n" if prev else "") + piece)[:8000]

        success = True
        db.commit()
        return success

    except Exception as e:
        logger.warning(f"[admin.make_judgement] AdminCase 저장 실패: {e}")
        try:
            db.commit()
        except Exception:
            pass
        return bool(success)


def _read_persisted_verdict(db: Session, *, case_id: UUID, run_no: int) -> Optional[Dict[str, Any]]:
    # 1) AdminCaseSummary 우선
    try:
        if hasattr(m, "AdminCaseSummary"):
            Model = m.AdminCaseSummary
            row = (
                db.query(Model)
                .filter(Model.case_id == case_id, Model.run == run_no)
                .first()
            )
            if row:
                ev = ""
                if hasattr(row, "evidence") and getattr(row, "evidence", None):
                    ev = row.evidence
                elif hasattr(row, "reason") and getattr(row, "reason", None):
                    ev = row.reason

                risk: Dict[str, Any] = {}
                if hasattr(row, "risk_score"):
                    risk["score"] = int(getattr(row, "risk_score", 0) or 0)
                if hasattr(row, "risk_level"):
                    risk["level"] = getattr(row, "risk_level", None) or ""
                if hasattr(row, "risk_rationale"):
                    risk["rationale"] = getattr(row, "risk_rationale", None) or ""

                vul: List[Any] = []
                if hasattr(row, "vulnerabilities") and getattr(row, "vulnerabilities", None):
                    vul = list(row.vulnerabilities or [])

                if hasattr(row, "verdict_json") and getattr(row, "verdict_json", None):
                    vj = dict(row.verdict_json or {})
                    vj.setdefault("evidence", ev)
                    vj.setdefault("risk", risk or {"score": 0, "level": "", "rationale": ""})
                    vj.setdefault("victim_vulnerabilities", vul)
                    vj.setdefault("phishing", bool(getattr(row, "phishing", False)))
                    vj.setdefault("continue", {"recommendation": "continue", "reason": ""})
                    return vj

                return {
                    "phishing": bool(getattr(row, "phishing", False)),
                    "evidence": ev,
                    "risk": risk or {"score": 0, "level": "", "rationale": ""},
                    "victim_vulnerabilities": vul,
                    "continue": {"recommendation": "continue", "reason": ""},
                }
    except Exception:
        pass

    # 2) Fallback: AdminCase.evidence에서 run별 JSON 찾기
    try:
        case = db.get(m.AdminCase, case_id)
        raw = (getattr(case, "evidence", "") or "")
        for line in raw.splitlines():
            try:
                obj = json.loads(line)
                if int(obj.get("run", -1)) == run_no and isinstance(obj.get("verdict"), dict):
                    return obj["verdict"]
            except Exception:
                continue
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────
# LLM 결과 파싱 보조
# ─────────────────────────────────────────────────────────
def _safe_json_parse(text: str) -> Optional[Dict[str, Any]]:
    """
    코드펜스/설명 섞여도 '첫 번째로 완결되는 JSON(객체/배열)'만 추출해 파싱.
    Extra data(뒤에 설명 문장 붙음) 오류를 크게 줄인다.
    """
    text = (text or "").strip()

    def _strip_code_fence(s: str) -> str:
        s = s.strip()
        if s.startswith("```"):
            s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
            s = re.sub(r"\s*```$", "", s)
        return s.strip()

    def _extract_first_json_fragment(s: str) -> Optional[str]:
        s = _strip_code_fence(s)
        if not s:
            return None

        start = None
        start_ch = None
        for idx, ch in enumerate(s):
            if ch in "{[":
                start = idx
                start_ch = ch
                break
        if start is None or start_ch is None:
            return None

        end_ch = "}" if start_ch == "{" else "]"
        depth = 0
        in_str = False
        esc = False

        for j in range(start, len(s)):
            ch = s[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            else:
                if ch == '"':
                    in_str = True
                    continue
                if ch == start_ch:
                    depth += 1
                elif ch == end_ch:
                    depth -= 1
                    if depth == 0:
                        return s[start:j + 1]
        return None

    frag = _extract_first_json_fragment(text)
    if not frag:
        return None

    try:
        obj = json.loads(frag)
        if isinstance(obj, dict):
            return obj
        # 배열이면 dict로 감싸서 기존 호출부 안전
        return {"data": obj}
    except Exception:
        try:
            obj = ast.literal_eval(frag)
            if isinstance(obj, dict):
                return obj
            return {"data": obj}
        except Exception:
            return None


# ─────────────────────────────────────────────────────────
# 툴 팩토리
# ─────────────────────────────────────────────────────────
def make_admin_tools(db: Session, guideline_repo):
    dynamic_generator = DynamicGuidanceGenerator()

    @tool(
        "admin.make_judgement",
        args_schema=SingleData,
        description="(case_id, run_no)의 전체 대화를 MCP JSON 또는 전달받은 turns로 판정한다. DB는 결과 저장에만 사용한다."
    )
    def make_judgement(data: Any) -> Dict[str, Any]:
        logger.info("[admin.make_judgement] raw data type=%s repr=%r", type(data), data)
        payload = _unwrap_data(data)
        try:
            ji = _JudgeMakeInput(**payload)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"JudgeMakeInput 검증 실패: {e}")

        turns: Optional[List[Dict[str, Any]]] = ji.turns

        if turns is None and ji.log and isinstance(ji.log, dict):
            maybe = ji.log.get("turns")
            if isinstance(maybe, list):
                turns = maybe
        if turns is None:
            turns = _fetch_turns_from_mcp(ji.case_id, ji.run_no)

        try:
            verdict = summarize_run_full(turns=turns)
        except TypeError as te:
            logger.error("[admin.make_judgement] summarize_run_full가 turns 기반 시그니처를 지원해야 합니다.")
            raise HTTPException(
                status_code=500,
                detail="summarize_run_full이 'turns' 인자를 지원하도록 업데이트해 주세요."
            ) from te

        risk = verdict.get("risk") or {}
        score = int(risk.get("score", 0) or 0)
        score = 0 if score < 0 else (100 if score > 100 else score)
        risk["score"] = score

        level = str((risk.get("level") or "").lower())
        if level not in {"low", "medium", "high", "critical"}:
            level = (
                "critical" if score >= 75 else
                "high" if score >= 50 else
                "medium" if score >= 25 else
                "low"
            )
        risk["level"] = level
        verdict["risk"] = risk

        if level == "critical":
            verdict["continue"] = {
                "recommendation": "stop",
                "reason": "위험도가 critical로 판정되어 시뮬레이션을 종료합니다."
            }
        else:
            verdict["continue"] = {
                "recommendation": "continue",
                "reason": "위험도가 critical이 아니므로 다음 라운드를 진행합니다."
            }

        persisted = _persist_verdict(db, case_id=ji.case_id, run_no=ji.run_no, verdict=verdict)
        if not persisted:
            try:
                logger.warning("[admin.make_judgement] persisted=False → 1회 재시도")
                persisted = _persist_verdict(db, case_id=ji.case_id, run_no=ji.run_no, verdict=verdict)
            except Exception:
                pass

        return {
            "ok": True,
            "persisted": persisted,
            "case_id": str(ji.case_id),
            "run_no": ji.run_no,
            **verdict,
        }

    @tool(
        "admin.judge",
        args_schema=SingleData,
        description="(case_id, run_no)의 **저장된 판정**을 조회한다. 저장된 결과가 없으면 '없음'을 알려준다."
    )
    def judge(data: Any) -> Dict[str, Any]:
        payload = _unwrap_data(data)
        try:
            ji = _JudgeReadInput(**payload)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"JudgeInput 검증 실패: {e}")

        saved = _read_persisted_verdict(db, case_id=ji.case_id, run_no=ji.run_no)
        if saved is not None:
            return {
                "ok": True,
                "phishing": bool(saved.get("phishing", False)),
                "reason": str(saved.get("evidence", "")),  # 기존 호환
                "run_no": ji.run_no,
                "evidence": saved.get("evidence", ""),
                "risk": saved.get("risk", {"score": 0, "level": "", "rationale": ""}),
                "victim_vulnerabilities": saved.get("victim_vulnerabilities", []),
                "continue": saved.get("continue", {"recommendation": "continue", "reason": ""}),
            }

        return {
            "ok": False,
            "case_id": str(ji.case_id),
            "run_no": ji.run_no,
            "message": "저장된 라운드 판정이 없습니다. admin.make_judgement를 먼저 호출하세요."
        }

    @tool(
        "admin.generate_guidance",
        args_schema=SingleData,
        description=(
            "판정결과(위험도/취약점/피싱여부/근거) + (선택) 시나리오/피해자/이전판정을 바탕으로 "
            "공격자용 맞춤 지침을 생성한다. 예: {'data': {'case_id':UUID,'run_no':int,"
            "'scenario':{...},'victim_profile':{...},'previous_judgements':[...]} }"
        )
    )
    def generate_guidance(data: Any) -> Dict[str, Any]:
        payload = _unwrap_data(data)
        case_id = payload.get("case_id")
        run_no = int(payload.get("run_no") or payload.get("round_no") or 1)

        try:
            case_uuid = UUID(str(case_id))
        except Exception:
            return {"ok": False, "error": "invalid_case_id", "message": "case_id must be UUID"}

        verdict = _read_persisted_verdict(db, case_id=case_uuid, run_no=run_no)
        if not verdict:
            return {"ok": False, "error": "no_saved_verdict", "message": "admin.make_judgement 이후 호출하세요."}

        scenario = payload.get("scenario") or {}
        victim_profile = payload.get("victim_profile") or {}
        previous_judgements = payload.get("previous_judgements") or []

        try:
            result = dynamic_generator.generate_guidance(
                db=db,
                case_id=str(case_uuid),
                round_no=run_no,
                scenario=scenario,
                victim_profile=victim_profile,
                previous_judgments=previous_judgements,
                verdict=verdict,
            )
        except Exception as e:
            logger.exception("[admin.generate_guidance] 실패")
            return {"ok": False, "error": f"generator_failed: {e!s}"}

        return {
            "ok": True,
            "type": "A",
            "text": result.get("guidance_text", ""),
            "categories": result.get("selected_categories", []),
            "reasoning": result.get("reasoning", ""),
            "expected_effect": result.get("expected_effect", ""),
            "risk_level": (verdict.get("risk") or {}).get("level", ""),
            "targets": verdict.get("victim_vulnerabilities", []),
            "source": "dynamic_generator+verdict"
        }

    @tool(
        "admin.make_prevention",
        args_schema=SingleData,
        description=(
            "대화(turns)+판단(judgements)+지침(guidances)로 최종 예방책(personalized_prevention) JSON을 생성한다. "
            "Action Input 예: {'data': {'case_id':UUID,'rounds':int,'turns':[...],'judgements':[...],'guidances':[...],'format':'personalized_prevention'}}"
        )
    )
    def make_prevention(data: Any) -> Dict[str, Any]:
        payload = _unwrap_data(data)
        try:
            pi = _MakePreventionInput(**payload)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"MakePreventionInput 검증 실패: {e}")

        is_term, _reason = _is_terminal_case(pi.rounds, pi.judgements)
        if not is_term:
            return {
                "ok": False,
                "error": "not_terminal",
                "message": "prevention can be generated only at round 3 or when risk is critical",
                "rounds": pi.rounds,
            }

        llm = agent_chat(temperature=0.2)

        schema_hint = {
            "personalized_prevention": {
                "summary": "string (2~3문장)",
                "analysis": {
                    "outcome": "success|fail",
                    "reasons": ["string", "string", "string"],
                    "risk_level": "low|medium|high"
                },
                "steps": ["명령형 한국어 단계 5~9개"],
                "tips": ["체크리스트형 팁 3~6개"]
            }
        }

        system = (
            "너는 보이스피싱 예방 전문가다. 입력된 대화/판단/지침을 바탕으로, "
            "아래 스키마에 맞춘 JSON만 출력하라. 한국어로 간결하고 실용적으로 작성하라. "
            "코드블럭/주석/설명 금지. 오직 JSON 한 개만 반환."
        )
        user = {
            "case_id": str(pi.case_id),
            "rounds": pi.rounds,
            "guidances": pi.guidances,
            "judgements": pi.judgements,
            "turns": pi.turns,
            "format": pi.format,
            "schema": schema_hint
        }

        messages = [
            ("system", system),
            ("human",
             "다음 입력을 바탕으로 'personalized_prevention' 키 하나만 있는 JSON을 출력하라.\n"
             + json.dumps(user, ensure_ascii=False))
        ]

        try:
            res = llm.invoke(messages)
            text = getattr(res, "content", str(res))
            parsed = _safe_json_parse(text) or {}
            if "personalized_prevention" not in parsed:
                return {
                    "ok": False,
                    "error": "missing_key_personalized_prevention",
                    "raw": text[:1200]
                }
            return {
                "ok": True,
                "case_id": str(pi.case_id),
                "personalized_prevention": parsed["personalized_prevention"]
            }
        except Exception as e:
            return {"ok": False, "error": f"llm_error: {e!s}"}

    @tool(
        "admin.save_prevention",
        args_schema=SingleData,
        description="개인화된 예방책을 DB에 저장한다. {'data': {'case_id':UUID,'offender_id':int,'victim_id':int,'run_no':int,'summary':str,'steps':[str,...]}}"
    )
    def save_prevention(data: Any) -> str:
        payload = _unwrap_data(data)
        try:
            spi = _SavePreventionInput(**payload)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"SavePreventionInput 검증 실패: {e}")

        try:
            q = (
                db.query(m.PersonalizedPrevention)
                .filter(
                    m.PersonalizedPrevention.case_id == spi.case_id,
                    m.PersonalizedPrevention.is_active == True  # noqa: E712
                )
            )
            if hasattr(m.PersonalizedPrevention, "created_at"):
                q = q.order_by(m.PersonalizedPrevention.created_at.desc())
            else:
                q = q.order_by(m.PersonalizedPrevention.id.desc())
            existing = q.first()
            if existing:
                return str(existing.id)
        except Exception:
            pass

        obj = m.PersonalizedPrevention(
            case_id=spi.case_id,
            offender_id=spi.offender_id,
            victim_id=spi.victim_id,
            run=spi.run_no,
            content={"summary": spi.summary, "steps": spi.steps},
            note="agent-generated",
            is_active=True,
        )
        db.add(obj)
        db.commit()
        return str(obj.id)

    return [make_judgement, judge, generate_guidance, make_prevention, save_prevention]
