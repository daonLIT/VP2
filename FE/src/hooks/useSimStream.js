// useSimStream.js
// 스트림을 consume하는 React 훅 (완성형 오케스트레이터 버전)
import { useRef, useState, useCallback } from "react";
import { streamReactSimulation } from "../lib/streamReactSimulation";

/**
 * useSimStream 훅
 * - 백엔드 SSE(streamReactSimulation)를 구독하여 이벤트를 실시간으로 상태/메시지에 반영
 * - App.jsx에서 하던 세밀한 분기(케이스 생성, 진행률, 묶음 로그 파싱, 중복방지, 완료 후 번들 조회)까지 포함
 *
 * @param {Function} setMessages  외부 messages state setter
 * @param {Object}   options
 *   - addSystem: (msg) => void
 *   - addChat: (role, content, ts, label, side, meta) => void
 *   - setProgress: (n|fn) => void
 *   - setSimulationState: (state) => void   // "IDLE" | "PREPARE" | "RUNNING" | "FINISH"
 *   - getConversationBundle: async (caseId) => bundle
 *   - onSessionResult: (payload) => void    // setSessionResult 대체 콜백
 *   - selectedScenario: object              // 라벨 표기용
 *   - selectedCharacter: object             // 라벨 표기용
 */
export function useSimStream(
  setMessages,
  {
    addSystem,
    addChat,
    setProgress,
    setSimulationState,
    getConversationBundle,
    onSessionResult,
    selectedScenario,
    selectedCharacter,
  } = {}
) {
  const [logs, setLogs] = useState([]);
  const [messages, setLocalMessages] = useState([]);
  const [judgement, setJudgement] = useState(null);
  const [guidance, setGuidance] = useState(null);
  const [prevention, setPrevention] = useState(null);
  const [running, setRunning] = useState(false);

  // 현재 실행 중인 async iterator 핸들
  const iterRef = useRef(null);
  const stoppedRef = useRef(false);

  // App.jsx 동등 기능용 refs
  const caseIdRef = useRef(null);
  const totalRoundsRef = useRef(5);
  const seenTurnsRef = useRef(new Set());

  // ────────────────────────────── 유틸 ──────────────────────────────
  const stripAnsi = (s = "") => String(s).replace(/\x1B\[[0-9;]*m/g, "");
  const containsFinishedChain = (text = "") => /\bFinished chain\b/i.test(stripAnsi(text));

  function extractDialogueOrPlainText(s) {
    if (!s) return s;
    const cleaned = s.replace(/```(?:json)?/gi, "").trim();
    try {
      const m = cleaned.match(/\{[\s\S]*\}/);
      if (m) {
        const obj = JSON.parse(m[0]);
        if (obj && typeof obj === "object") {
          if (typeof obj.dialogue === "string" && obj.dialogue.trim()) return obj.dialogue.trim();
          if (typeof obj.thoughts === "string" && obj.thoughts.trim()) return obj.thoughts.trim();
        }
      }
    } catch (_) {}
    return cleaned.replace(/[ \t]+/g, " ").replace(/\s*\n\s*/g, "\n").trim();
  }

  function parseConversationLogContent(content) {
    if (!content || typeof content !== "string") return null;
    const idx = content.indexOf("{");
    if (idx < 0) return null;
    try {
      const obj = JSON.parse(content.slice(idx));
      const caseId = obj.case_id || obj.meta?.case_id || obj.log?.case_id || null;
      const roundNo =
        obj.meta?.round_no || obj.meta?.run_no || obj.stats?.round || obj.stats?.run || 1;
      const turns = Array.isArray(obj.turns) ? obj.turns : [];
      return { caseId, roundNo: Number(roundNo) || 1, turns };
    } catch (_) {
      return null;
    }
  }

  // 현재 스트림 강제 종료(반드시 iterator.return() 호출)
  const hardClose = useCallback(() => {
    try {
      const it = iterRef.current;
      if (it && typeof it.return === "function") it.return();
    } catch {}
    finally {
      iterRef.current = null;
    }
  }, []);

  // ────────────────────────────── start/stop ──────────────────────────────
  const start = useCallback(
    async (payload) => {
      if (running) return;
      setRunning(true);
      stoppedRef.current = false;

      // 초기화
      setLogs([]);
      setLocalMessages([]);
      setJudgement(null);
      setGuidance(null);
      setPrevention(null);
      caseIdRef.current = null;
      seenTurnsRef.current = new Set();
      totalRoundsRef.current = payload?.round_limit ?? 5;

      hardClose();
      setSimulationState?.("PREPARE");
      setProgress?.(0);

      if (selectedScenario && selectedCharacter) {
        addSystem?.(`시뮬레이션 시작: ${selectedScenario.name} / ${selectedCharacter.name}`);
      }

      const it = streamReactSimulation(payload);
      iterRef.current = it;

      try {
        for await (const event of it) {
          if (stoppedRef.current) break;

          const evt = event?.content ?? event;
          const type = event?.type;
          const contentStr =
            typeof event?.content === "string"
              ? event.content
              : (event?.content?.message ?? "");

          // 🔍 디버깅 로그 (개발 중에만 사용)
          console.log('📨 [SSE Event]', { type, event });

          // 🔚 종료 조건
          if (type === "run_end" || type === "run_end_local" || type === "error") {
            setSimulationState?.("FINISH");
            break;
          }
          if (type === "terminal" && containsFinishedChain(contentStr || "")) {
            setLogs((p) => [...p, contentStr]);
            setSimulationState?.("FINISH");
            break;
          }

          // ✅ 1) conversation_log 이벤트 처리 (최우선)
          if (type === "conversation_log") {
            console.log('🎯 conversation_log 감지!', evt);
            
            // content가 객체인지 확인
            const logData = typeof evt === "object" ? evt : event?.content;
            const turns = logData?.turns || logData?.log?.turns || [];
            
            if (Array.isArray(turns) && turns.length > 0) {
              setSimulationState?.("RUNNING");
              
              // 각 턴을 메시지로 변환
              turns.forEach((turn, idx) => {
                const role = (turn.role || "offender").toLowerCase();
                const key = `conv:${Date.now()}:${idx}:${role}`;
                
                // 중복 방지
                if (seenTurnsRef.current.has(key)) return;
                seenTurnsRef.current.add(key);

                const raw = turn.text || "";
                let text = "";
                let thoughts = null;
                let convinced = null;

                // 피해자 메시지 JSON 파싱
                if (role === "victim") {
                  try {
                    const cleaned = raw.replace(/```(?:json)?/gi, "").trim();
                    const match = cleaned.match(/\{[\s\S]*\}/);
                    if (match) {
                      const parsed = JSON.parse(match[0]);
                      text = parsed.dialogue || parsed.text || "";
                      thoughts = parsed.thoughts || null;
                      convinced = parsed.is_convinced ?? null;
                    } else {
                      text = raw;
                    }
                  } catch {
                    text = raw;
                  }
                } else {
                  text = raw;
                }

                const label = role === "offender"
                  ? (selectedScenario?.name || "피싱범")
                  : (selectedCharacter?.name || "피해자");
                const side = role === "offender" ? "left" : "right";

                const newMsg = {
                  type: "chat",
                  sender: role,
                  role,
                  side,
                  content: text,
                  thoughts,
                  convinced,
                  timestamp: new Date().toLocaleTimeString(),
                  turn: idx,
                };

                console.log('💬 대화 추가:', newMsg);
                
                setLocalMessages((prev) => [...prev, newMsg]);
                setMessages?.((prev) => [...prev, newMsg]);
              });
              
              setProgress?.((p) => Math.min(100, (typeof p === "number" ? p : 0) + 10));
            }
            continue;
          }

          // 2) 로그/터미널 (수정 버전)
          if (type === "log" || type === "terminal" || type === "agent_action") {
            const content = event.content ?? "";
            setLogs((p) => [...p, content]);

            // ✅ [GuidanceGeneration] 로그 감지 (기존 guidance 대체)
            if (typeof content === "string" && content.startsWith("[GuidanceGeneration]")) {
              try {
                const jsonStr = content.replace("[GuidanceGeneration]", "").trim();
                const parsed = JSON.parse(jsonStr);
                const g = parsed?.generated_guidance;

                if (g) {
                  setGuidance({
                    type: "GuidanceGeneration",
                    content: g.text,
                    categories: g.categories,
                    reasoning: g.reasoning,
                    expected_effect: g.expected_effect,
                    meta: {
                      case_id: parsed.case_id,
                      round_no: parsed.round_no,
                      timestamp: parsed.timestamp,
                      analysis_context: parsed.analysis_context,
                    },
                    raw: parsed,
                  });

                  console.log("✅ GuidanceGeneration에서 guidance 추출 성공:", g.text);
                }
              } catch (e) {
                console.warn("⚠️ GuidanceGeneration 파싱 실패:", e, content);
              }
            }

            // [conversation_log] 문자열 형태 처리 (폴백)
            if (
              type === "log" &&
              typeof event.content === "string" &&
              event.content.startsWith("[conversation_log]")
            ) {
              const parsed = parseConversationLogContent(event.content);
              if (parsed && parsed.turns?.length) {
                // 위의 conversation_log 처리 로직과 동일
                // (생략 가능)
              }
            }

            continue;
          }

          // 3) 케이스 생성
          if (type === "case_created") {
            caseIdRef.current = evt.case_id;
            addSystem?.(`케이스 생성: ${evt.case_id}`);
            continue;
          }

          // 4) 라운드 시작/진행
          if (type === "round_start") {
            addSystem?.(evt.message);
            continue;
          }
          if (type === "simulation_progress") {
            setSimulationState?.("RUNNING");
            addSystem?.(evt.message || `라운드 ${evt.round} 진행 중...`);
            continue;
          }

          // 5) 판정/가이드
          // if (type === "judgement") {
          //   setJudgement(event);
          //   addSystem?.(
          //     `라운드 ${evt.round} 판정: ${evt.phishing ? "피싱 성공" : "피싱 실패"} - ${evt.reason}`
          //   );
          //   continue;
          // }
          // 5) 판정/가이드
          if (type === "judgement") {
            setJudgement(event);

            // ✅ applied_guidance 자동 추출
            const appliedGuidance =
              evt?.meta?.scenario?.enhancement_info?.applied_guidance ??
              evt?.enhancement_info?.applied_guidance ??
              null;

            if (appliedGuidance) {
              setGuidance({
                type: "guidance_extracted",
                content: appliedGuidance,
                source: "meta.scenario.enhancement_info.applied_guidance",
              });
              console.log("✅ applied_guidance 추출됨:", appliedGuidance);
            }

            addSystem?.(
              `라운드 ${evt.round ?? "?"} 판정: ${
                evt.phishing ? "피싱 성공" : "피싱 실패"
              } - ${evt.reason ?? "N/A"}`
            );
            continue;
          }

          if (type === "guidance_generated") {
            setGuidance(event);
            addSystem?.(
              `라운드 ${evt.round} 지침 생성: ${evt.guidance?.categories?.join(", ") || "N/A"}`
            );
            continue;
          }

          if (type === "prevention_tip") {
            setPrevention(event);
            continue;
          }

          // 6) 전체 완료
          if (type === "complete") {
            setProgress?.(100);
            setSimulationState?.("IDLE");
            addSystem?.("시뮬레이션 완료!");
            
            if (caseIdRef.current && getConversationBundle && onSessionResult) {
              try {
                const bundle = await getConversationBundle(caseIdRef.current);
                onSessionResult({
                  phishing: bundle.phishing,
                  evidence: bundle.evidence,
                  totalTurns: bundle.total_turns,
                  preview: bundle.preview,
                });
              } catch {}
            }
            continue;
          }

          // 7) 오류
          if (type === "error") {
            if ((event.message || "").includes("duplicated simulation run detected")) {
              addSystem?.("이미 실행 중인 시뮬레이션이 있습니다. 잠시 후 다시 시도해주세요.");
            }
            throw new Error(event.message || "시뮬레이션 오류");
          }
        }
      } catch (e) {
        if (!stoppedRef.current) {
          console.error("SSE 스트리밍 실패:", e);
          addSystem?.(`시뮬레이션 실패: ${e.message}`);
          setSimulationState?.("IDLE");
        }
      } finally {
        setRunning(false);
        hardClose();
      }
    },
    [
      running,
      setMessages,
      hardClose,
      addSystem,
      setProgress,
      setSimulationState,
      getConversationBundle,
      onSessionResult,
      selectedScenario,
      selectedCharacter,
    ]
  );

  const stop = useCallback(() => {
    stoppedRef.current = true;
    setRunning(false);
    hardClose();
  }, [hardClose]);

  return {
    logs,
    messages,
    start,
    stop,
    running,
    judgement,
    guidance,
    prevention,
  };
}