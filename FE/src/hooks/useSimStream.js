//스트림을 consume하는 React 훅
import { useEffect, useRef, useState, useCallback } from "react";
import { streamReactSimulation } from "../lib/streamReactSimulation";

/**
 * useSimStream 훅
 * - 백엔드 SSE(streamReactSimulation)를 구독하여 이벤트를 실시간으로 상태에 반영
 * - SimulatorPage의 setMessages를 받아 대화 로그를 업데이트함
 */
const RAW_API_BASE = import.meta.env?.VITE_API_URL || window.location.origin;
const API_BASE = RAW_API_BASE.replace(/\/$/, "");
const API_PREFIX = "/api";
export const API_ROOT = `${API_BASE}${API_PREFIX}`;

export function useSimStream(setMessages) {
  const [logs, setLogs] = useState([]);
  const [messages, setLocalMessages] = useState([]);
  const [judgement, setJudgement] = useState(null);
  const [guidance, setGuidance] = useState(null);
  const [prevention, setPrevention] = useState(null);
  const [running, setRunning] = useState(false);

  const start = useCallback(async (payload) => {
    if (running) return;
    setRunning(true);
    setLogs([]);
    setJudgement(null);
    setGuidance(null);
    setPrevention(null);

    for await (const ev of streamReactSimulation(payload)) {
      console.log("[SSE Event]", ev); // ✅ 콘솔 확인용

      // 1️⃣ 터미널/로그 이벤트
      if (["log", "terminal", "agent_action"].includes(ev.type)) {
        setLogs((prev) => [...prev, ev.content || JSON.stringify(ev)]);
      }

      // 2️⃣ 채팅 메시지 이벤트
      else if (ev.type === "new_message") {
        const content = ev.content || ev.message || "";
        if (!content.trim()) continue;
        const role = (ev.role || "offender").toLowerCase();
        const newMsg = {
          type: "chat",
          sender: role,
          role,
          side: role === "offender" ? "left" : "right",
          content,
          timestamp: new Date().toLocaleTimeString(),
        };
        setLocalMessages((prev) => [...prev, newMsg]);
        if (setMessages) setMessages((prev) => [...prev, newMsg]);
      }

      // 3️⃣ 분석/판단 이벤트
      else if (ev.type === "judgement") setJudgement(ev);
      else if (ev.type === "guidance_generated") setGuidance(ev);
      else if (ev.type === "prevention_tip") setPrevention(ev);

      // 4️⃣ 종료 이벤트
      else if (["run_end", "run_end_local", "error"].includes(ev.type)) {
        setRunning(false);
        break;
      }
    }
    setRunning(false);
  }, [running, setMessages]);

  const stop = useCallback(() => {
    setRunning(false);
  }, []);

  useEffect(() => {
    const es = new EventSource(`${API_ROOT}/simulator/stream`);
    es.onmessage = (e) => {
      const data = JSON.parse(e.data);

      if (data.type === "log") {
        // ✅ 이 부분 반드시 있어야 함
        setLogs((prev) => [...prev, data]);
      }

      if (data.type === "message") {
        setMessages((prev) => [...prev, data]);
      }
    };

    return () => es.close();
  }, []);


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
