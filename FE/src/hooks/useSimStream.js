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

      // 이전 스트림 정리
      hardClose();

      // 준비 상태
      setSimulationState?.("PREPARE");
      setProgress?.(0);

      // 시작 안내(선택)
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

          // 🔚 종료 조건: run_end / run_end_local / error / terminal(Finished chain)
          if (type === "run_end" || type === "run_end_local" || type === "error") {
            setSimulationState?.("FINISH");
            break;
          }
          if (type === "terminal" && containsFinishedChain(contentStr || "")) {
            setLogs((p) => [...p, contentStr]);
            setSimulationState?.("FINISH");
            break;
          }

          // 1) 로그/터미널/액션
          if (type === "log" || type === "terminal" || type === "agent_action") {
            setLogs((p) => [...p, event.content ?? JSON.stringify(event)]);

            // [conversation_log] 묶음 로그 파싱 → 발화 분해
            if (
              type === "log" &&
              typeof event.content === "string" &&
              event.content.startsWith("[conversation_log]")
            ) {
              const parsed = parseConversationLogContent(event.content);
              if (parsed && parsed.turns?.length) {
                const roundNo = parsed.roundNo || 1;
                setProgress?.((pr) => Math.min(100, (typeof pr === "number" ? pr : 0) + 1));
                setSimulationState?.("RUNNING");
                parsed.turns.forEach((t, idx) => {
                  const role = (t.role || "offender").toLowerCase();
                  const raw = t.text || t.content || "";
                  const text = extractDialogueOrPlainText(raw);
                  const key = `${roundNo}:${idx}:${role}`;
                  if (seenTurnsRef.current.has(key)) return;
                  seenTurnsRef.current.add(key);

                  const label =
                    role === "offender"
                      ? (selectedScenario?.name || "피싱범")
                      : (selectedCharacter?.name || "피해자");
                  const side = role === "offender" ? "left" : "right";

                  addChat?.(role, text, new Date().toLocaleTimeString(), label, side, {
                    run: roundNo,
                    turn: idx,
                  });

                  const newMsg = {
                    type: "chat",
                    sender: role,
                    role,
                    side,
                    content: text,
                    timestamp: new Date().toLocaleTimeString(),
                    run: roundNo,
                    turn: idx,
                  };
                  setLocalMessages((prev) => [...prev, newMsg]);
                  setMessages?.((prev) => [...prev, newMsg]);
                });
              }
            }
            continue;
          }

          // 2) 케이스 생성
          if (type === "case_created") {
            caseIdRef.current = evt.case_id;
            addSystem?.(`케이스 생성: ${evt.case_id}`);
            continue;
          }

          // 3) 라운드 시작/진행
          if (type === "round_start") {
            addSystem?.(evt.message);
            continue;
          }
          if (type === "simulation_progress") {
            setSimulationState?.("RUNNING");
            addSystem?.(evt.message || `라운드 ${evt.round} 진행 중...`);
            continue;
          }

          // 4) 라운드 대화 로그 일괄(conversation_logs)
          if (type === "conversation_logs") {
            const round = evt.round ?? 1;
            setProgress?.((round / (totalRoundsRef.current || 1)) * 100);

            const logs = Array.isArray(evt.logs) ? evt.logs : [];
            const missing = logs
              .sort((a, b) => (a.turn_index ?? 0) - (b.turn_index ?? 0))
              .filter((log) => {
                const role = (log.role || "offender").toLowerCase();
                const key = `${round}:${log.turn_index}:${role}`;
                return !seenTurnsRef.current.has(key);
              });

            for (const log of missing) {
              const role = (log.role || "offender").toLowerCase();
              const raw = log.content || log.text || log.message || "";
              const text = extractDialogueOrPlainText(raw);
              const label =
                role === "offender"
                  ? (selectedScenario?.name || "피싱범")
                  : (selectedCharacter?.name || "피해자");
              const side = role === "offender" ? "left" : "right";
              const ts = log.created_kst
                ? new Date(log.created_kst).toLocaleTimeString()
                : new Date().toLocaleTimeString();

              addChat?.(role, text, ts, label, side, {
                run: log.run,
                turn: log.turn_index ?? log.turn,
              });

              const newMsg = {
                type: "chat",
                sender: role,
                role,
                side,
                content: text,
                timestamp: ts,
                run: log.run,
                turn: log.turn_index ?? log.turn,
              };
              setLocalMessages((prev) => [...prev, newMsg]);
              setMessages?.((prev) => [...prev, newMsg]);

              const key = `${round}:${log.turn_index}:${role}`;
              seenTurnsRef.current.add(key);
            }

            if (evt.status === "no_logs") addSystem?.(`⚠️ 라운드 ${round} 로그를 가져오지 못했습니다.`);
            setSimulationState?.("RUNNING");
            continue;
          }

          // 5) 라운드 완료
          if (type === "round_complete") {
            addSystem?.(`라운드 ${evt.round} 완료 (${evt.total_turns}턴)`);
            continue;
          }

          // 6) 단건 메시지
          if (type === "new_message") {
            const role = (evt.role || "offender").toLowerCase();
            const key = `${evt.round}:${evt.turn_index}:${role}`;
            if (seenTurnsRef.current.has(key)) continue;
            seenTurnsRef.current.add(key);

            const raw = evt.content || "";
            const text = extractDialogueOrPlainText(raw);
            const label =
              role === "offender"
                ? (selectedScenario?.name || "피싱범")
                : (selectedCharacter?.name || "피해자");
            const side = role === "offender" ? "left" : "right";
            const ts = evt.created_kst
              ? new Date(evt.created_kst).toLocaleTimeString()
              : new Date().toLocaleTimeString();

            addChat?.(role, text, ts, label, side, { run: evt.round, turn: evt.turn_index });
            setSimulationState?.("RUNNING");
            setProgress?.((p) => Math.min(100, (typeof p === "number" ? p : 0) + 1));

            const newMsg = {
              type: "chat",
              sender: role,
              role,
              side,
              content: text,
              timestamp: ts,
              run: evt.round,
              turn: evt.turn_index,
            };
            setLocalMessages((prev) => [...prev, newMsg]);
            setMessages?.((prev) => [...prev, newMsg]);
            continue;
          }

          // 7) 판정/가이드/예방팁
          if (type === "judgement") {
            setJudgement(event);
            addSystem?.(
              `라운드 ${evt.round} 판정: ${evt.phishing ? "피싱 성공" : "피싱 실패"} - ${evt.reason}`
            );
            continue;
          }
          if (type === "guidance_generated") {
            setGuidance(event);
            addSystem?.(
              `라운드 ${evt.round} 지침 생성: ${
                evt.guidance?.categories?.join(", ") || "N/A"
              }`
            );
            continue;
          }
          if (type === "prevention_tip") {
            setPrevention(event);
            continue;
          }

          // 8) 전체 완료
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

          // 9) 오류
          if (type === "error") {
            if ((event.message || "").includes("duplicated simulation run detected")) {
              addSystem?.("이미 실행 중인 시뮬레이션이 있습니다. 잠시 후 다시 시도해주세요.");
            }
            throw new Error(event.message || "시뮬레이션 오류");
          }
        }

        // 루프가 종료됐는데도 caseId가 없고 FINISH가 아니면 에러 처리(선택)
        // (백엔드가 run_end를 보냈다면 FINISH로 끝났을 것)
        // 필요 시 활성화:
        // if (!caseIdRef.current) { throw new Error("case_id를 받지 못했습니다."); }

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
      addChat,
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


// src/hooks/useSimStream.js
// import { useState, useCallback } from "react";
// import { streamReactSimulation } from "../lib/streamReactSimulation";

// export function useSimStream(setMessages) {
//   const [logs, setLogs] = useState([]);
//   const [messages, setLocalMessages] = useState([]);
//   const [judgement, setJudgement] = useState(null);
//   const [guidance, setGuidance] = useState(null);
//   const [prevention, setPrevention] = useState(null);
//   const [running, setRunning] = useState(false);

//   const start = useCallback(async (payload) => {
//     if (running) return;
//     setRunning(true);
//     setLogs([]);
//     setJudgement(null);
//     setGuidance(null);
//     setPrevention(null);

//     for await (const ev of streamReactSimulation(payload)) {
//       console.log("[SSE Event]", ev);

//       // ✅ 1. 터미널 로그 이벤트 (기존 유지)
//       if (["log", "terminal", "agent_action"].includes(ev.type)) {
//         setLogs((prev) => [...prev, ev.content || JSON.stringify(ev)]);
//       }

//       // ✅ 2. 단일 메시지 이벤트 (기존 유지)
//       else if (ev.type === "new_message") {
//         const content = ev.content || ev.message || "";
//         if (!content.trim()) continue;
//         const role = (ev.role || "offender").toLowerCase();

//         const newMsg = {
//           sender: role,
//           role,
//           type: "chat",
//           side: role === "offender" ? "left" : "right",
//           content,
//           timestamp: new Date().toLocaleTimeString(),
//         };

//         setLocalMessages((prev) => [...prev, newMsg]);
//         if (setMessages) setMessages((prev) => [...prev, newMsg]);
//       }

//       // ✅ 3. conversation_log (대화 turn 전체)
//       else if ((ev.type || ev.event) === "conversation_log") {
//         try {
//           let data = ev.data || ev.content || ev.message;
//           if (typeof data === "string") {
//             try { data = JSON.parse(data); } catch {
//               //주석
//             }
//           }
//           const turns = data.turns || data?.data?.turns || [];
//           if (!Array.isArray(turns) || turns.length === 0) continue;

//           // 🔍 전체 구조 출력
//           console.log("🎯 [DEBUG] 대화 턴 전체 구조:", turns);

//           // 🔍 각 턴별 대화 요약 출력
//           turns.forEach((t, i) => {
//             try {
//               if (t.role === "offender") {
//                 console.log(`🔴 [피싱범 #${i + 1}]`, t.text);
//               } else if (t.role === "victim") {
//                 let parsed = {};
//                 try {
//                   parsed = JSON.parse(t.text);
//                 } catch {
//                   parsed = { dialogue: t.text };
//                 }
//                 console.log(
//                   `🟢 [피해자 #${i + 1}]`,
//                   "\n대화:", parsed.dialogue,
//                   "\n속마음:", parsed.thoughts,
//                   "\n설득도:", parsed.is_convinced
//                 );
//               }
//             } catch (innerErr) {
//               console.error("⚠️ 개별 턴 파싱 오류:", innerErr, t);
//             }
//           });

//           // ✅ MessageBubble용 객체 생성
//           const newMsgs = turns.map((t) => {
//             const isVictim = t.role === "victim";
//             let dialogueText = t.text;
//             let thoughts = null;
//             let convinced = null;

//             if (isVictim) {
//               try {
//                 const parsed = JSON.parse(t.text);
//                 dialogueText = parsed.dialogue || "";
//                 thoughts = parsed.thoughts || null;
//                 convinced = parsed.is_convinced || null;
//               } catch {
//                 // JSON 파싱 실패 시 원문 그대로 사용
//               }
//             }

//             return {
//               sender: t.role,
//               role: t.role,
//               type: "chat",
//               side: isVictim ? "right" : "left",
//               content: dialogueText,
//               thoughts,
//               convinced,
//               timestamp: new Date().toLocaleTimeString(),
//             };
//           });

//           // ✅ 상태 업데이트
//           setLocalMessages((prev) => [...prev, ...newMsgs]);
//           if (setMessages) setMessages((prev) => [...prev, ...newMsgs]);
//         } catch (err) {
//           console.error("❌ conversation_log 파싱 실패:", err, ev);
//         }
//       }

//       // ✅ 4. 분석 결과 이벤트 (기존 유지)
//       else if (ev.type === "judgement") setJudgement(ev);
//       else if (ev.type === "guidance_generated") setGuidance(ev);
//       else if (ev.type === "prevention_tip") setPrevention(ev);

//       // ✅ 5. 종료 이벤트
//       else if (["run_end", "error"].includes(ev.type)) {
//         setRunning(false);
//         break;
//       }
//     }

//     setRunning(false);
//   }, [running, setMessages]);

//   return { logs, messages, start, running, judgement, guidance, prevention };
// }



// src/hooks/useSimStream.js ===> 터미널 로그는 작동되는 코드임!!!!
// import { useState, useCallback } from "react";
// import { streamReactSimulation } from "../lib/streamReactSimulation";

// export function useSimStream(setMessages) {
//   const [logs, setLogs] = useState([]);
//   const [messages, setLocalMessages] = useState([]);
//   const [judgement, setJudgement] = useState(null);
//   const [guidance, setGuidance] = useState(null);
//   const [prevention, setPrevention] = useState(null);
//   const [running, setRunning] = useState(false);

//   const start = useCallback(async (payload) => {
//     if (running) return;
//     setRunning(true);
//     setLogs([]);
//     setJudgement(null);
//     setGuidance(null);
//     setPrevention(null);

//     for await (const ev of streamReactSimulation(payload)) {
//       console.log("[SSE Event]", ev);

//       if (["log", "terminal", "agent_action"].includes(ev.type)) {
//         setLogs((prev) => [...prev, ev.content || JSON.stringify(ev)]);
//       }
//       else if (ev.type === "new_message") {
//         const content = ev.content || ev.message || "";
//         if (!content.trim()) continue;
//         const role = (ev.role || "offender").toLowerCase();

//         const newMsg = {
//           sender: role,
//           role,
//           type: "chat",
//           side: role === "offender" ? "left" : "right",
//           content,
//           timestamp: new Date().toLocaleTimeString(),
//         };

//         setLocalMessages((prev) => [...prev, newMsg]);
//         if (setMessages) setMessages((prev) => [...prev, newMsg]);
//       }
//       else if (ev.type === "judgement") setJudgement(ev);
//       else if (ev.type === "guidance_generated") setGuidance(ev);
//       else if (ev.type === "prevention_tip") setPrevention(ev);
//       else if (["run_end", "error"].includes(ev.type)) {
//         setRunning(false);
//         break;
//       }
//     }
//     setRunning(false);
//   }, [running, setMessages]);

//   return { logs, messages, start, running, judgement, guidance, prevention };
// }


// // src/hooks/useSimStream.js
// import { useEffect, useState, useCallback } from "react";
// import { streamReactSimulation } from "../lib/streamReactSimulation";

// const RAW_API_BASE = import.meta.env?.VITE_API_URL || window.location.origin;
// const API_BASE = RAW_API_BASE.replace(/\/$/, "");
// const API_PREFIX = "/api";
// export const API_ROOT = `${API_BASE}${API_PREFIX}`;

// export function useSimStream(setMessages) {
//   const [logs, setLogs] = useState([]);
//   const [judgement, setJudgement] = useState(null);
//   const [guidance, setGuidance] = useState(null);
//   const [prevention, setPrevention] = useState(null);
//   const [running, setRunning] = useState(false);

//   const start = useCallback(
//     async (payload) => {
//       if (running) return;
//       setRunning(true);
//       setLogs([]);
//       setJudgement(null);
//       setGuidance(null);
//       setPrevention(null);
//       if (setMessages) setMessages([]); // 🔹 초기화

//       for await (const ev of streamReactSimulation(payload)) {
//         console.log("[SSE Event]", ev);

//         if (["log", "terminal", "agent_action"].includes(ev.type)) {
//           setLogs((prev) => [...prev, ev.content || JSON.stringify(ev)]);
//         }

//         else if (["new_message", "chat", "message"].includes(ev.type)) {
//           const content = ev.content || ev.message || "";
//           if (!content.trim()) continue;
//           const role = (ev.role || "offender").toLowerCase();

//           const newMsg = {
//             type: "chat",
//             sender: role,
//             role,
//             side: role === "offender" ? "left" : "right",
//             content,
//             timestamp: new Date().toLocaleTimeString(),
//           };

//           // ✅ 상위 messages 상태만 업데이트
//           if (setMessages) setMessages((prev) => [...prev, newMsg]);
//         }

//         else if (ev.type === "judgement") setJudgement(ev);
//         else if (ev.type === "guidance_generated") setGuidance(ev);
//         else if (ev.type === "prevention_tip") setPrevention(ev);

//         else if (["run_end", "run_end_local", "error"].includes(ev.type)) {
//           setRunning(false);
//           break;
//         }
//       }
//       setRunning(false);
//     },
//     [running, setMessages]
//   );

//   const stop = useCallback(() => {
//     setRunning(false);
//   }, []);

//   // ⚡ 백엔드 SSE 직접 구독 (optional)
//   useEffect(() => {
//     const es = new EventSource(`${API_ROOT}/simulator/stream`);
//     es.onmessage = (e) => {
//       const data = JSON.parse(e.data);

//       if (data.type === "log") setLogs((prev) => [...prev, data]);
//       if (["chat", "message"].includes(data.type)) {
//         if (setMessages)
//           setMessages((prev) => [...prev, data]);
//       }
//     };

//     return () => es.close();
//   }, [setMessages]);

//   return {
//     logs,
//     start,
//     stop,
//     running,
//     judgement,
//     guidance,
//     prevention,
//   };
// }
