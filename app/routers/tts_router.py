# # app/routers/tts_router.py
# from __future__ import annotations
# import os, io, base64, re, wave
# from typing import List, Optional, Literal, Union, Tuple, Set
# from fastapi import APIRouter, HTTPException
# from pydantic import BaseModel, Field
# from dotenv import load_dotenv

# # 환경 설정
# load_dotenv()
# cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "../service-account-key.json")
# if cred_path:
#     os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_path

# # Google TTS 클라이언트
# from google.cloud import texttospeech_v1 as texttospeech
# tts_client = texttospeech.TextToSpeechClient()

# router = APIRouter(prefix="/tts", tags=["TTS"])



# VOICE_BY_SPEAKER = {
#     "offender": {"languageCode": "ko-KR", "voiceName": "ko-KR-Standard-B"},
#     "victim":   {"languageCode": "ko-KR", "voiceName": "ko-KR-Standard-A"},
# }

# # 연령·성별 기반 음성 매핑
# VOICE_BY_AGE_GENDER = {
#     ("20s", "female"): "ko-KR-Standard-A",
#     ("30s", "female"): "ko-KR-Standard-A",
#     ("20s", "male")  : "ko-KR-Standard-C",
#     ("30s", "male")  : "ko-KR-Standard-C",
#     ("40s", "female"): "ko-KR-Standard-B",
#     ("50s", "female"): "ko-KR-Standard-B",
#     ("60s", "female"): "ko-KR-Standard-B",
#     ("40s", "male")  : "ko-KR-Standard-D",
#     ("50s", "male")  : "ko-KR-Standard-D",
#     ("60s", "male")  : "ko-KR-Standard-D",
# }

# # 가해자용 대체 음성 리스트 (충돌 방지용)
# OFFENDER_ALTERNATES = [
#     "ko-KR-Standard-A",
#     "ko-KR-Standard-B",
#     "ko-KR-Standard-C",
#     "ko-KR-Standard-D",
# ]

# # Pydantic 모델들
# class WordTiming(BaseModel):
#     token: str
#     charCount: int
#     startSec: float
#     durationSec: float

# class TtsResponse(BaseModel):
#     audioContent: str
#     contentType: str
#     totalDurationSec: float
#     charTimeSec: float
#     words: List[WordTiming]

# class DialogueTurn(BaseModel):
#     run_no: Optional[int] = None  
#     speaker: str
#     text: str
#     voiceName: Optional[str] = None
#     languageCode: Optional[str] = None
#     age_group: Optional[str] = None   # e.g. "20s","30s","40s","50s","60s"
#     gender: Optional[str] = None      # "male" or "female"

# class TtsRequest(BaseModel):
#     mode: Literal["single", "dialogue"] = "single"
#     text: Optional[str] = Field(None)
#     languageCode: str = "ko-KR"
#     voiceName: str = "ko-KR-Standard-A"
#     speakingRate: float = 1.5  # ✅ 기본 속도를 1.5배로 상향
#     pitch: float = 0.0
#     audioEncoding: str = "LINEAR16"
#     dialogue: Optional[List[DialogueTurn]] = None

# class DialogueItem(BaseModel):
#     run_no: Optional[int] = None
#     speaker: str
#     text: str
#     voiceName: str
#     languageCode: str
#     audioContent: str
#     contentType: str
#     totalDurationSec: float
#     charTimeSec: float
#     words: List[WordTiming]

# class DialogueResponse(BaseModel):
#     items: List[DialogueItem]
#     note: str = "Word timings are heuristic (char-proportional)."

# # 유틸리티 함수들
# CHAR_PATTERN = re.compile(r"[가-힣A-Za-z0-9]")

# def count_chars(token: str) -> int:
#     return len(CHAR_PATTERN.findall(token))

# def wav_duration_sec(wav_bytes: bytes) -> float:
#     with wave.open(io.BytesIO(wav_bytes), "rb") as w:
#         return w.getnframes() / float(w.getframerate())

# def wrap_pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1, sampwidth: int = 2) -> bytes:
#     """raw PCM (linear16) bytes → WAV 파일 bytes"""
#     buf = io.BytesIO()
#     with wave.open(buf, "wb") as w:
#         w.setnchannels(channels)
#         w.setsampwidth(sampwidth)
#         w.setframerate(sample_rate)
#         w.writeframes(pcm_bytes)
#     return buf.getvalue()

# def synthesize_wav_and_timings(
#     text: str,
#     languageCode: str,
#     voiceName: str,
#     speakingRate: float = 1.0,
#     pitch: float = 0.0,
#     sample_rate_hz: int = 24000,
# ) -> Tuple[bytes, float, float, List[WordTiming]]:
#     """
#     Google TTS 호출 → WAV bytes 확보(이미 WAV면 그대로, RAW면 래핑)
#     → 길이 및 글자 비례 타이밍 반환.
#     """
#     synthesis_input = texttospeech.SynthesisInput(text=text)
#     voice = texttospeech.VoiceSelectionParams(language_code=languageCode, name=voiceName)
#     audio_config = texttospeech.AudioConfig(
#         audio_encoding=texttospeech.AudioEncoding.LINEAR16,
#         speaking_rate=speakingRate,
#         pitch=pitch,
#         sample_rate_hertz=sample_rate_hz,
#     )

#     resp = tts_client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
#     audio_bytes = resp.audio_content or b""

#     # 감지: 이미 WAV 컨테이너인지 확인 (RIFF header)
#     if len(audio_bytes) >= 12 and audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
#         wav_bytes = audio_bytes
#     else:
#         # raw PCM → WAV로 래핑
#         wav_bytes = wrap_pcm_to_wav(audio_bytes, sample_rate=sample_rate_hz, channels=1, sampwidth=2)

#     total_sec = wav_duration_sec(wav_bytes)

#     # 토큰화: 공백으로 분리하고 빈 토큰 제거
#     tokens = [tk for tk in re.split(r"\s+", text.strip()) if tk != ""]
#     counts = [count_chars(tk) for tk in tokens]
#     total_chars = sum(counts)
#     words: List[WordTiming] = []

#     # 글자 수가 0인 경우(숫자/기호만 있는 경우 등) — 균등 분배
#     if total_chars <= 0 or not tokens:
#         per = total_sec / max(len(tokens), 1) if tokens else 0.0
#         acc = 0.0
#         for tk in tokens:
#             words.append(WordTiming(token=tk, charCount=0, startSec=round(acc, 6), durationSec=round(per, 6)))
#             acc += per
#         return wav_bytes, total_sec, 0.0, words

#     # 글자 비례 계산
#     char_time = total_sec / total_chars
#     acc = 0.0
#     for tk, cc in zip(tokens, counts):
#         dur = cc * char_time
#         words.append(WordTiming(token=tk, charCount=cc, startSec=acc, durationSec=dur))
#         acc += dur

#     # 보정: 누적 오차로 인한 잔여 시간을 마지막 단어에 더함
#     if len(words) > 0:
#         last = words[-1]
#         last_end = last.startSec + last.durationSec
#         epsilon = 1e-6
#         if total_sec - last_end > epsilon:
#             extra = total_sec - last_end
#             last.durationSec += extra

#     # 응답용 반올림
#     for w in words:
#         w.startSec = round(w.startSec, 6)
#         w.durationSec = round(w.durationSec, 6)

#     char_time = round(char_time, 9)
#     return wav_bytes, total_sec, char_time, words

# def choose_voice_name(turn: DialogueTurn, default: str = "ko-KR-Standard-A", taken_voices: Optional[Set[str]] = None, role: Optional[str] = None) -> str:
#     """
#     우선순위: explicit turn.voiceName -> age+gender 매핑 -> VOICE_BY_SPEAKER(role) -> default
#     taken_voices가 주어지고 role == 'offender'인 경우 충돌 시 OFFENDER_ALTERNATES에서 대체 선택.
#     """
#     # 1) explicit override
#     if turn.voiceName:
#         candidate = turn.voiceName
#     else:
#         # 2) age+gender 매핑 우선
#         age = (turn.age_group or "").strip()
#         gender = (turn.gender or "").strip().lower()
#         if age and gender:
#             candidate = VOICE_BY_AGE_GENDER.get((age, gender), None)
#         else:
#             candidate = None

#         # 3) VOICE_BY_SPEAKER 기반 (role 매핑)
#         if not candidate and role:
#             base = VOICE_BY_SPEAKER.get(role)
#             if base:
#                 candidate = base.get("voiceName")
#         # 4) 기본값
#         if not candidate:
#             candidate = default

#     # 가해자 충돌 방지: taken_voices와 동일하면 대체 선택
#     if role == "offender" and taken_voices:
#         if candidate in taken_voices:
#             for alt in OFFENDER_ALTERNATES:
#                 if alt not in taken_voices and alt != candidate:
#                     return alt
#             return candidate

#     return candidate

# # API 엔드포인트
# @router.post("/synthesize", response_model=Union[TtsResponse, DialogueResponse])
# def synthesize(req: TtsRequest):
#     """TTS 합성 엔드포인트 - 단일 텍스트 또는 대화 모드 지원"""
    
#     if req.mode == "dialogue":
#         turns = req.dialogue if (req.dialogue and len(req.dialogue) > 0) else [DialogueTurn(**t) for t in DUMMY_DIALOGUE]
#         items: List[DialogueItem] = []

#         # 피해자(들)의 음성 집합을 미리 계산해 가해자 충돌을 예방
#         victim_voices: Set[str] = set()
#         for t in turns:
#             try:
#                 if t.speaker == "victim":
#                     v = choose_voice_name(t, default="ko-KR-Standard-A", role="victim")
#                     if v:
#                         victim_voices.add(v)
#             except Exception:
#                 pass

#         for turn in turns:
#             spk = turn.speaker
#             txt = turn.text
#             base = VOICE_BY_SPEAKER.get(spk, {"languageCode":"ko-KR","voiceName":"ko-KR-Standard-A"})
#             lang = turn.languageCode or base["languageCode"]

#             # 가해자(offender)는 피해자와 음성이 겹치지 않도록 taken_voices로 피해자 음성 전달
#             if spk == "offender":
#                 vname = choose_voice_name(turn, default=base["voiceName"], taken_voices=victim_voices, role="offender")
#             else:
#                 vname = choose_voice_name(turn, default=base["voiceName"], role=spk)

#             try:
#                 wav_bytes, total_sec, char_time, words = synthesize_wav_and_timings(
#                     text=txt, languageCode=lang, voiceName=vname,
#                     speakingRate=req.speakingRate, pitch=req.pitch, sample_rate_hz=24000
#                 )
#             except Exception as e:
#                 raise HTTPException(status_code=500, detail=f"대화 TTS 실패: {e}")

#             items.append(DialogueItem(
#                 run_no=turn.run_no,                 # ✅ 위치는 상관없지만 콤마 주의
#                 speaker=spk,
#                 text=txt,
#                 voiceName=vname,
#                 languageCode=lang,
#                 audioContent=base64.b64encode(wav_bytes).decode("utf-8"),
#                 contentType="audio/wav",
#                 totalDurationSec=round(total_sec, 3),
#                 charTimeSec=round(char_time, 6),
#                 words=words,                        # ✅ 콤마 추가
#             ))
#         return DialogueResponse(items=items)

#     # 단건 모드
#     text = (req.text or "안녕하세요. 제 이름은 구글입니다. 오늘은 텍스트 음성 변환을 테스트합니다.").strip()
#     try:
#         wav_bytes, total_sec, char_time, words = synthesize_wav_and_timings(
#             text=text, languageCode=req.languageCode, voiceName=req.voiceName,
#             speakingRate=req.speakingRate, pitch=req.pitch, sample_rate_hz=24000
#         )
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"TTS 요청 실패: {e}")

#     return TtsResponse(
#         audioContent=base64.b64encode(wav_bytes).decode("utf-8"),
#         contentType="audio/wav",
#         totalDurationSec=round(total_sec, 3),
#         charTimeSec=round(char_time, 6),
#         words=words
#     )

# @router.get("/voices")
# def list_voices():
#     """사용 가능한 TTS 음성 목록 반환"""
#     try:
#         voices = tts_client.list_voices()
#         korean_voices = [
#             {
#                 "name": voice.name,
#                 "language_code": voice.language_codes[0],
#                 "ssml_gender": voice.ssml_gender.name,
#                 "natural_sample_rate": voice.natural_sample_rate_hertz
#             }
#             for voice in voices.voices
#             if "ko-KR" in voice.language_codes[0]
#         ]
#         return {"voices": korean_voices}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"음성 목록 조회 실패: {e}")

# @router.get("/health")
# def tts_health():
#     """TTS 서비스 헬스체크"""
#     try:
#         # 간단한 테스트 합성
#         synthesis_input = texttospeech.SynthesisInput(text="테스트")
#         voice = texttospeech.VoiceSelectionParams(
#             language_code="ko-KR",
#             name="ko-KR-Standard-A"
#         )
#         audio_config = texttospeech.AudioConfig(
#             audio_encoding=texttospeech.AudioEncoding.LINEAR16
#         )
        
#         tts_client.synthesize_speech(
#             input=synthesis_input,
#             voice=voice,
#             audio_config=audio_config
#         )
        
#         return {"status": "healthy", "service": "google-tts"}
#     except Exception as e:
#         return {"status": "unhealthy", "error": str(e)}


# # # app/routers/tts_router.py
# # from __future__ import annotations
# # import os, io, base64, re, wave
# # from typing import List, Optional, Literal, Union, Tuple, Set
# # from fastapi import APIRouter, HTTPException
# # from pydantic import BaseModel, Field
# # from dotenv import load_dotenv

# # # 환경 설정
# # load_dotenv()
# # cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "../service-account-key.json")
# # if cred_path:
# #     os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_path

# # # Google TTS 클라이언트
# # from google.cloud import texttospeech
# # tts_client = texttospeech.TextToSpeechClient()

# # router = APIRouter(prefix="/tts", tags=["TTS"])

# # # 더미 대화 데이터
# # DUMMY_DIALOGUE = [
# #     {"speaker":"offender","text":"안녕하세요, 고객님. 저는 OO은행 대출팀 직원입니다. 최근 저희 은행에서 고객님께 저금리 대출 상품을 우선적으로 안내해드리고 있는데요, 간단한 심사만 거치면 최대 1억 원까지 대출이 가능합니다. 지금 바로 상담 도와드려도 될까요?"},
# #     {"speaker":"victim","text":"네, OO은행이요? 제가 대출이 필요한 건 아닌데, 혹시 어떤 상품인지 좀 더 자세히 설명해주실 수 있나요?"},
# #     {"speaker":"offender","text":"네, 고객님. 이번에 저희가 특별히 준비한 ‘스마트 저금리 대출’ 상품은 기존 대출보다 최대 2% 낮은 금리로 이용 가능하시고, 서류 간소화로 빠르게 승인받으실 수 있습니다. 신용등급에 따라 한도는 달라지지만, 상담 후 바로 대출 가능 여부도 알려드릴 수 있으니 부담 없이 진행해보시죠."},
# #     {"speaker":"victim","text":"금리가 2%나 낮다니 솔깃하긴 하네요. 그런데 제가 지금 당장 대출이 필요한 상황은 아니라서요. 혹시 대출 가능 여부 확인하려면 어떤 정보가 필요한가요?"},
# #     {"speaker":"offender","text":"네, 고객님. 대출 가능 여부 확인을 위해서는 본인 명의의 주민등록번호와 은행 거래용 휴대폰 번호만 알려주시면 간단히 조회 가능합니다. 직접 방문 없이도 빠른 심사가 가능하니 편하실 겁니다. 지금 바로 진행해드려도 될까요?"},
# #     {"speaker":"victim","text":"주민등록번호랑 휴대폰 번호만으로요? 좀 불안한데요. 혹시 다른 필요한 서류는 없나요? 그리고 OO은행 지점 전화번호를 알 수 있을까요? 제가 직접 전화해서 확인해보고 싶어서요."},
# #     {"speaker":"offender","text":"고객님 불안하신 점 충분히 이해합니다. 하지만 저희는 고객님의 편의를 위해 최소한의 정보만으로 신속하게 확인하는 시스템을 운영 중입니다. 지점 전화번호는 보안상 바로 안내해드리기 어렵지만, 원하시면 공식 홈페이지에서 확인하실 수 있습니다. 그래도 지금 바로 간단히 대출 가능 여부만 조회해드려도 괜찮으실까요?"},
# #     {"speaker":"victim","text":"아, 그렇군요. 홈페이지에서 확인해보겠습니다. 지금 바로 조회는 괜찮지만, 혹시 조회 후에 제가 꼭 대출을 받아야 하는 건 아니죠?"},
# #     {"speaker":"offender","text":"물론입니다, 고객님. 조회는 대출 진행 의사와 무관하게 단순한 가능 여부 확인 단계이니 부담 가지실 필요 없습니다. 지금 바로 조회해 드리면 정확한 결과 알려드리겠습니다. 시작해도 될까요?"},
# #     {"speaker":"victim","text":"네, 알겠습니다. 그럼 제 주민등록번호 앞자리 8자리와 생년월일을 말씀드리겠습니다. 뒷자리는 조회 후에 필요하면 말씀드리겠습니다."},
# #     {"speaker":"offender","text":"좋습니다, 고객님. 주민등록번호 앞자리와 생년월일만으로도 조회가 가능하니 우선 진행해보겠습니다. 이후 추가 정보가 필요하면 바로 말씀드릴 테니 걱정하지 않으셔도 됩니다. 지금 바로 시작하겠습니다."},
# #     {"speaker":"victim","text":"네, 기다리겠습니다. 혹시 조회 결과가 나오면 바로 알려주시겠어요?"},
# #     {"speaker":"offender","text":"네, 고객님. 조회 결과는 바로 확인되는 대로 신속하게 안내해드리겠습니다. 조금만 기다려주시고 편안하게 계시면 됩니다. 혹시 궁금한 점 있으시면 언제든 말씀해 주세요."},
# #     {"speaker":"victim","text":"네, 알겠습니다. 혹시 조회 결과가 얼마나 걸릴지 대략적으로라도 알 수 있을까요?"},
# #     {"speaker":"offender","text":"고객님, 조회 결과는 보통 5분 이내로 나오지만 간혹 시스템 상황에 따라 조금 더 걸릴 수도 있습니다. 최대한 빠르게 결과 알려드리니 조금만 더 기다려 주시겠어요? 곧 좋은 소식 전해드리겠습니다."},
# #     {"speaker":"victim","text":"네, 알겠습니다. 5분 정도면 기다릴 수 있죠. 결과 나오면 꼭 바로 알려주세요."},
# #     {"speaker":"offender","text":"네, 고객님. 조회 결과가 나오는 대로 바로 연락드려 안내해드리겠습니다. 조금만 더 기다려주시고, 혹시 그동안 궁금하신 점 있으면 언제든 말씀해 주세요."},
# #     {"speaker":"victim","text":"아직 결과가 안 나왔나요? 5분 정도 기다렸는데... 혹시 문제가 생긴 건 아닌가요?"},
# #     {"speaker":"offender","text":"죄송합니다, 고객님. 시스템 점검 중이라 잠시 지연되고 있는 상황입니다. 조금만 더 기다려 주시면 바로 조회 결과를 안내해 드리겠습니다. 불편을 드려 정말 죄송합니다."},
# #     {"speaker":"victim","text":"아니요, 괜찮습니다. 더 이상 기다리기 어려울 것 같네요. 나중에 다시 연락드리겠습니다."},

# #     {"speaker":"offender","text":"고객님, 현재 금융감독원에서 출금 내역 전수조사를 진행 중이라 고객님 계좌가 임시 보호관찰 상태로 전환됐습니다. 안전한 확인을 위해 새 휴대폰 개통과 전용 원격제어 앱 설치가 필수입니다. 이 절차가 완료되기 전까지는 기존 연락처로 연락이 어려우니 빠른 협조 부탁드립니다"},
# #     {"speaker":"victim","text":"아니, 갑자기요? 새 휴대폰 개통이랑 앱 설치라뇨? 그게 왜 필요한 거죠?"},
# #     {"speaker":"offender","text":"네, 고객님의 자금 보호와 범죄 연루 방지를 위해 기존 휴대폰은 보안상 위험하다고 판단되어 새로 개통한 휴대폰에서만 안전한 원격 관리를 하도록 조치한 겁니다. 이 절차가 완료되어야만 금융 거래와 상담이 가능하니, 빠르게 진행해 주셔야 불이익을 막을 수 있습니다."},
# #     {"speaker":"victim","text":"기존 휴대폰이 위험하다는 건 무슨 뜻이죠? 그리고 그걸 꼭 새 휴대폰으로만 해야 하나요?"},
# #     {"speaker":"offender","text":"기존 휴대폰은 해킹이나 불법 접근 가능성이 확인되어 현재 모든 금융 업무에서 차단된 상태입니다. 새 휴대폰에서만 고객님의 계좌 보호를 위한 보안 앱을 설치해 안전하게 관리할 수 있으니, 이 절차를 미루시면 자금 이동 제한 등 불이익이 발생할 수 있습니다. 지금 바로 진행해 드리겠습니다."},
# #     {"speaker":"victim","text":"아, 그렇군요. 그럼 제가 직접 새 휴대폰을 개통하러 가야 하나요?"},
# #     {"speaker":"offender","text":"네, 고객님. 새 휴대폰 개통은 고객님이 직접 가까운 대리점 방문 없이도 온라인으로 간편하게 할 수 있도록 안내해 드리겠습니다. 이후 전용 보안 앱 설치까지 저희가 단계별로 자세히 도와드릴 테니 걱정하지 마시고 따라만 와 주세요."},
# #     {"speaker":"victim","text":"온라인으로 간편하게 할 수 있다니 다행이네요. 그럼 어떻게 하면 되죠?"},
# #     {"speaker":"offender","text":"먼저 새 휴대폰 개통 절차를 위해 가상 통신사 웹사이트에 접속해 고객님의 기본 정보만 입력해 주세요. 완료되면 저에게 알려 주시면, 바로 보안 앱 설치와 원격 제어 연결을 안내해 드리겠습니다. 지금 바로 시작해 주시겠어요?"},
# #     {"speaker":"victim","text":"가상 통신사 웹사이트요? 혹시 웹사이트 주소를 알 수 있을까요?"},
# #     {"speaker":"offender","text":"고객님, 보안을 위해 웹사이트 주소는 문자로 별도로 안전하게 보내드릴 예정이며, 직접 입력하지 마시고 제가 안내해 드리는 링크를 통해 접속하시면 됩니다. 링크 접속 후 절차에 따라 안내해 드릴 테니 걱정하지 마시고 따라와 주세요."},
# #     {"speaker":"victim","text":"네, 알겠습니다. 문자로 링크 보내주시면 확인하고 진행하겠습니다."},
# #     {"speaker":"offender","text":"네, 바로 문자로 안전한 접속 링크를 보내드리겠습니다. 링크를 받으시면 바로 접속해 기본 정보 입력부터 시작해 주시고, 진행 중 궁금한 점이나 어려운 부분은 즉시 말씀해 주세요. 안전한 절차로 신속히 마무리하겠습니다."},
# #     {"speaker":"victim","text":"문자 확인했습니다. 다음에 무엇을 하면 되나요?"},
# #     {"speaker":"offender","text":"안전 확인을 위해 가상 검증 포털(가명)에 접속하시면 ‘임시 보관계정’ 안내가 뜹니다. 해당 계정으로 일시 보관 이체를 진행하시면 불법 접근 위험을 차단할 수 있습니다."},
# #     {"speaker":"victim","text":"얼마를, 어디로 이체해야 하나요?"},
# #     {"speaker":"offender","text":"포털 화면에 표시된 ‘OOO’으로 500만원만 이체하시면 됩니다. 계좌는 123-4567-890이며, 수취인은 ‘OO안전보호센터(가명)’로 표시됩니다."},
# #     {"speaker":"victim","text":"모바일 뱅킹 접속했습니다. 수취인명이 ‘OO안전보호센터(가명)’ 맞나요?"},
# #     {"speaker":"offender","text":"네, 맞습니다. 표시가 다르면 중단하시고 저에게 바로 알려주세요."},
# #     {"speaker":"victim","text":"이체 과정에서 ‘고위험 거래’ 경고가 뜹니다. 계속 진행해야 하나요?"},
# #     {"speaker":"offender","text":"현재 임시 보호관찰 상태라 자동 경고가 나올 수 있습니다. 안내대로 일시 보관만 진행하시면 바로 해제됩니다. 계속 진행해 주세요."},
# #     {"speaker":"victim","text":"네… 이체 진행하겠습니다."},
# #     {"speaker":"offender","text":"확인 중입니다… 네, 임시 보관계정으로 입금 접수 완료되었습니다. 곧 안전 확인 절차가 마무리되면 해제 안내드리겠습니다."},
# #     {"speaker":"victim","text":"해제까지 얼마나 걸리나요? 자금은 언제 돌려받을 수 있죠?"},
# #     {"speaker":"offender","text":"보통 1~2시간 내로 완료됩니다. 완료 즉시 동일 계좌로 자동 복원됩니다. 기다리시는 동안 기존 기기는 사용을 자제해 주세요."}
# # ]

# # VOICE_BY_SPEAKER = {
# #     "offender": {"languageCode": "ko-KR", "voiceName": "ko-KR-Standard-B"},
# #     "victim":   {"languageCode": "ko-KR", "voiceName": "ko-KR-Standard-A"},
# # }

# # # 연령·성별 기반 음성 매핑
# # VOICE_BY_AGE_GENDER = {
# #     ("20s", "female"): "ko-KR-Standard-A",
# #     ("30s", "female"): "ko-KR-Standard-A",
# #     ("20s", "male")  : "ko-KR-Standard-C",
# #     ("30s", "male")  : "ko-KR-Standard-C",
# #     ("40s", "female"): "ko-KR-Standard-B",
# #     ("50s", "female"): "ko-KR-Standard-B",
# #     ("60s", "female"): "ko-KR-Standard-B",
# #     ("40s", "male")  : "ko-KR-Standard-D",
# #     ("50s", "male")  : "ko-KR-Standard-D",
# #     ("60s", "male")  : "ko-KR-Standard-D",
# # }

# # # 가해자용 대체 음성 리스트 (충돌 방지용)
# # OFFENDER_ALTERNATES = [
# #     "ko-KR-Standard-A",
# #     "ko-KR-Standard-B",
# #     "ko-KR-Standard-C",
# #     "ko-KR-Standard-D",
# # ]

# # # Pydantic 모델들
# # class WordTiming(BaseModel):
# #     token: str
# #     charCount: int
# #     startSec: float
# #     durationSec: float

# # class TtsResponse(BaseModel):
# #     audioContent: str
# #     contentType: str
# #     totalDurationSec: float
# #     charTimeSec: float
# #     words: List[WordTiming]

# # class DialogueTurn(BaseModel):
# #     speaker: str
# #     text: str
# #     voiceName: Optional[str] = None
# #     languageCode: Optional[str] = None
# #     age_group: Optional[str] = None   # e.g. "20s","30s","40s","50s","60s"
# #     gender: Optional[str] = None      # "male" or "female"

# # class TtsRequest(BaseModel):
# #     mode: Literal["single", "dialogue"] = "single"
# #     text: Optional[str] = Field(None)
# #     languageCode: str = "ko-KR"
# #     voiceName: str = "ko-KR-Standard-A"
# #     speakingRate: float = 1.0
# #     pitch: float = 0.0
# #     audioEncoding: str = "LINEAR16"
# #     dialogue: Optional[List[DialogueTurn]] = None

# # class DialogueItem(BaseModel):
# #     speaker: str
# #     text: str
# #     voiceName: str
# #     languageCode: str
# #     audioContent: str
# #     contentType: str
# #     totalDurationSec: float
# #     charTimeSec: float
# #     words: List[WordTiming]

# # class DialogueResponse(BaseModel):
# #     items: List[DialogueItem]
# #     note: str = "Word timings are heuristic (char-proportional)."

# # # 유틸리티 함수들
# # CHAR_PATTERN = re.compile(r"[가-힣A-Za-z0-9]")

# # def count_chars(token: str) -> int:
# #     return len(CHAR_PATTERN.findall(token))

# # def wav_duration_sec(wav_bytes: bytes) -> float:
# #     with wave.open(io.BytesIO(wav_bytes), "rb") as w:
# #         return w.getnframes() / float(w.getframerate())

# # def wrap_pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1, sampwidth: int = 2) -> bytes:
# #     """raw PCM (linear16) bytes → WAV 파일 bytes"""
# #     buf = io.BytesIO()
# #     with wave.open(buf, "wb") as w:
# #         w.setnchannels(channels)
# #         w.setsampwidth(sampwidth)
# #         w.setframerate(sample_rate)
# #         w.writeframes(pcm_bytes)
# #     return buf.getvalue()

# # def synthesize_wav_and_timings(
# #     text: str,
# #     languageCode: str,
# #     voiceName: str,
# #     speakingRate: float = 1.0,
# #     pitch: float = 0.0,
# #     sample_rate_hz: int = 24000,
# # ) -> Tuple[bytes, float, float, List[WordTiming]]:
# #     """
# #     Google TTS 호출 → WAV bytes 확보(이미 WAV면 그대로, RAW면 래핑)
# #     → 길이 및 글자 비례 타이밍 반환.
# #     """
# #     synthesis_input = texttospeech.SynthesisInput(text=text)
# #     voice = texttospeech.VoiceSelectionParams(language_code=languageCode, name=voiceName)
# #     audio_config = texttospeech.AudioConfig(
# #         audio_encoding=texttospeech.AudioEncoding.LINEAR16,
# #         speaking_rate=speakingRate,
# #         pitch=pitch,
# #         sample_rate_hertz=sample_rate_hz,
# #     )

# #     resp = tts_client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
# #     audio_bytes = resp.audio_content or b""

# #     # 감지: 이미 WAV 컨테이너인지 확인 (RIFF header)
# #     if len(audio_bytes) >= 12 and audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
# #         wav_bytes = audio_bytes
# #     else:
# #         # raw PCM → WAV로 래핑
# #         wav_bytes = wrap_pcm_to_wav(audio_bytes, sample_rate=sample_rate_hz, channels=1, sampwidth=2)

# #     total_sec = wav_duration_sec(wav_bytes)

# #     # 토큰화: 공백으로 분리하고 빈 토큰 제거
# #     tokens = [tk for tk in re.split(r"\s+", text.strip()) if tk != ""]
# #     counts = [count_chars(tk) for tk in tokens]
# #     total_chars = sum(counts)
# #     words: List[WordTiming] = []

# #     # 글자 수가 0인 경우(숫자/기호만 있는 경우 등) — 균등 분배
# #     if total_chars <= 0 or not tokens:
# #         per = total_sec / max(len(tokens), 1) if tokens else 0.0
# #         acc = 0.0
# #         for tk in tokens:
# #             words.append(WordTiming(token=tk, charCount=0, startSec=round(acc, 6), durationSec=round(per, 6)))
# #             acc += per
# #         return wav_bytes, total_sec, 0.0, words

# #     # 글자 비례 계산
# #     char_time = total_sec / total_chars
# #     acc = 0.0
# #     for tk, cc in zip(tokens, counts):
# #         dur = cc * char_time
# #         words.append(WordTiming(token=tk, charCount=cc, startSec=acc, durationSec=dur))
# #         acc += dur

# #     # 보정: 누적 오차로 인한 잔여 시간을 마지막 단어에 더함
# #     if len(words) > 0:
# #         last = words[-1]
# #         last_end = last.startSec + last.durationSec
# #         epsilon = 1e-6
# #         if total_sec - last_end > epsilon:
# #             extra = total_sec - last_end
# #             last.durationSec += extra

# #     # 응답용 반올림
# #     for w in words:
# #         w.startSec = round(w.startSec, 6)
# #         w.durationSec = round(w.durationSec, 6)

# #     char_time = round(char_time, 9)
# #     return wav_bytes, total_sec, char_time, words

# # def choose_voice_name(turn: DialogueTurn, default: str = "ko-KR-Standard-A", taken_voices: Optional[Set[str]] = None, role: Optional[str] = None) -> str:
# #     """
# #     우선순위: explicit turn.voiceName -> age+gender 매핑 -> VOICE_BY_SPEAKER(role) -> default
# #     taken_voices가 주어지고 role == 'offender'인 경우 충돌 시 OFFENDER_ALTERNATES에서 대체 선택.
# #     """
# #     # 1) explicit override
# #     if turn.voiceName:
# #         candidate = turn.voiceName
# #     else:
# #         # 2) age+gender 매핑 우선
# #         age = (turn.age_group or "").strip()
# #         gender = (turn.gender or "").strip().lower()
# #         if age and gender:
# #             candidate = VOICE_BY_AGE_GENDER.get((age, gender), None)
# #         else:
# #             candidate = None

# #         # 3) VOICE_BY_SPEAKER 기반 (role 매핑)
# #         if not candidate and role:
# #             base = VOICE_BY_SPEAKER.get(role)
# #             if base:
# #                 candidate = base.get("voiceName")
# #         # 4) 기본값
# #         if not candidate:
# #             candidate = default

# #     # 가해자 충돌 방지: taken_voices와 동일하면 대체 선택
# #     if role == "offender" and taken_voices:
# #         if candidate in taken_voices:
# #             # 후보에서 첫 사용 가능한 음성을 선택
# #             for alt in OFFENDER_ALTERNATES:
# #                 if alt not in taken_voices and alt != candidate:
# #                     return alt
# #             # 모든 후보가 충돌하면 그냥 candidate 반환
# #             return candidate

# #     return candidate

# # # API 엔드포인트
# # @router.post("/synthesize", response_model=Union[TtsResponse, DialogueResponse])
# # def synthesize(req: TtsRequest):
# #     """TTS 합성 엔드포인트 - 단일 텍스트 또는 대화 모드 지원"""
    
# #     if req.mode == "dialogue":
# #         turns = req.dialogue if (req.dialogue and len(req.dialogue) > 0) else [DialogueTurn(**t) for t in DUMMY_DIALOGUE]
# #         items: List[DialogueItem] = []

# #         # 피해자(들)의 음성 집합을 미리 계산해 가해자 충돌을 예방
# #         victim_voices: Set[str] = set()
# #         for t in turns:
# #             try:
# #                 if t.speaker == "victim":
# #                     v = choose_voice_name(t, default="ko-KR-Standard-A", role="victim")
# #                     if v:
# #                         victim_voices.add(v)
# #             except Exception:
# #                 pass

# #         for turn in turns:
# #             spk = turn.speaker
# #             txt = turn.text
# #             base = VOICE_BY_SPEAKER.get(spk, {"languageCode":"ko-KR","voiceName":"ko-KR-Standard-A"})
# #             lang = turn.languageCode or base["languageCode"]

# #             # 가해자(offender)는 피해자와 음성이 겹치지 않도록 taken_voices로 피해자 음성 전달
# #             if spk == "offender":
# #                 vname = choose_voice_name(turn, default=base["voiceName"], taken_voices=victim_voices, role="offender")
# #             else:
# #                 # 피해자 또는 일반 화자
# #                 vname = choose_voice_name(turn, default=base["voiceName"], role=spk)

# #             try:
# #                 wav_bytes, total_sec, char_time, words = synthesize_wav_and_timings(
# #                     text=txt, languageCode=lang, voiceName=vname,
# #                     speakingRate=req.speakingRate, pitch=req.pitch, sample_rate_hz=24000
# #                 )
# #             except Exception as e:
# #                 raise HTTPException(status_code=500, detail=f"대화 TTS 실패: {e}")

# #             items.append(DialogueItem(
# #                 speaker=spk,
# #                 text=txt,
# #                 voiceName=vname,
# #                 languageCode=lang,
# #                 audioContent=base64.b64encode(wav_bytes).decode("utf-8"),
# #                 contentType="audio/wav",
# #                 totalDurationSec=round(total_sec, 3),
# #                 charTimeSec=round(char_time, 6),
# #                 words=words
# #             ))
# #         return DialogueResponse(items=items)

# #     # 단건 모드
# #     text = (req.text or "안녕하세요. 제 이름은 구글입니다. 오늘은 텍스트 음성 변환을 테스트합니다.").strip()
# #     try:
# #         wav_bytes, total_sec, char_time, words = synthesize_wav_and_timings(
# #             text=text, languageCode=req.languageCode, voiceName=req.voiceName,
# #             speakingRate=req.speakingRate, pitch=req.pitch, sample_rate_hz=24000
# #         )
# #     except Exception as e:
# #         raise HTTPException(status_code=500, detail=f"TTS 요청 실패: {e}")

# #     return TtsResponse(
# #         audioContent=base64.b64encode(wav_bytes).decode("utf-8"),
# #         contentType="audio/wav",
# #         totalDurationSec=round(total_sec, 3),
# #         charTimeSec=round(char_time, 6),
# #         words=words
# #     )

# # @router.get("/voices")
# # def list_voices():
# #     """사용 가능한 TTS 음성 목록 반환"""
# #     try:
# #         voices = tts_client.list_voices()
# #         korean_voices = [
# #             {
# #                 "name": voice.name,
# #                 "language_code": voice.language_codes[0],
# #                 "ssml_gender": voice.ssml_gender.name,
# #                 "natural_sample_rate": voice.natural_sample_rate_hertz
# #             }
# #             for voice in voices.voices
# #             if "ko-KR" in voice.language_codes[0]
# #         ]
# #         return {"voices": korean_voices}
# #     except Exception as e:
# #         raise HTTPException(status_code=500, detail=f"음성 목록 조회 실패: {e}")

# # @router.get("/health")
# # def tts_health():
# #     """TTS 서비스 헬스체크"""
# #     try:
# #         # 간단한 테스트 합성
# #         synthesis_input = texttospeech.SynthesisInput(text="테스트")
# #         voice = texttospeech.VoiceSelectionParams(
# #             language_code="ko-KR",
# #             name="ko-KR-Standard-A"
# #         )
# #         audio_config = texttospeech.AudioConfig(
# #             audio_encoding=texttospeech.AudioEncoding.LINEAR16
# #         )
        
# #         tts_client.synthesize_speech(
# #             input=synthesis_input,
# #             voice=voice,
# #             audio_config=audio_config
# #         )
        
# #         return {"status": "healthy", "service": "google-tts"}
# #     except Exception as e:
# #         return {"status": "unhealthy", "error": str(e)}