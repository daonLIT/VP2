// src/ReportPage.jsx
import {
  User,
  Bot,
  Terminal,
  ExternalLink,
  Shield,
  AlertTriangle,
} from "lucide-react";
import { useEffect, useState, useMemo } from "react";
import Badge from "./Badge";

async function fetchWithTimeout(url, { timeout = 15000, ...opts } = {}) {
  const ctrl = new AbortController();
  const id = setTimeout(() => ctrl.abort(), timeout);
  try {
    const res = await fetch(url, { ...opts, signal: ctrl.signal });
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      throw new Error(`HTTP ${res.status} ${res.statusText} ${txt}`);
    }
    const txt = await res.text();
    return txt ? JSON.parse(txt) : null;
  } finally {
    clearTimeout(id);
  }
}

const ReportPage = ({
  COLORS,
  setCurrentPage,
  sessionResult,
  scenarios,
  defaultCaseData,
  selectedScenario,
  selectedCharacter,
  currentCaseId,
  victimImageUrl,
  preventions = [],
}) => {
  const THEME = {
    ...COLORS,
    bg: "#030617",
    panel: "#061329",
    panelDark: "#04101f",
    panelDarker: "#020812",
    border: "#A8862A",
    text: "#FFFFFF",
    sub: "#BFB38A",
    blurple: "#A8862A",
    success: COLORS?.success ?? "#57F287",
    warn: COLORS?.warn ?? "#FF4757",
    white: "#FFFFFF",
    black: "#000000",
    danger: COLORS?.danger ?? "#ED4245",
  };

  const [adminCase, setAdminCase] = useState(null);
  const [adminCaseLoading, setAdminCaseLoading] = useState(false);
  const [adminCaseError, setAdminCaseError] = useState(null);

  useEffect(() => {
    let mounted = true;
    (async () => {
      if (!currentCaseId) return;
      setAdminCaseLoading(true);
      setAdminCaseError(null);
      try {
        const data = await fetchWithTimeout(
          `/api/admin-cases/${encodeURIComponent(currentCaseId)}`,
          { timeout: 15000 }
        );
        if (!mounted) return;
        setAdminCase(data || null);
      } catch (e) {
        if (!mounted) return;
        setAdminCaseError(e.message || String(e));
      } finally {
        if (mounted) setAdminCaseLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [currentCaseId]);

  const casePhishing = useMemo(() => {
    const fromAdmin =
      typeof adminCase?.phishing === "boolean" ? adminCase.phishing : undefined;
    const fromDefault =
      typeof defaultCaseData?.case?.phishing === "boolean"
        ? defaultCaseData.case.phishing
        : undefined;
    const fromSessionPhishing =
      typeof sessionResult?.phishing === "boolean"
        ? sessionResult.phishing
        : undefined;
    const fromSessionIs =
      typeof sessionResult?.isPhishing === "boolean"
        ? sessionResult.isPhishing
        : undefined;

    return (
      fromAdmin ?? fromDefault ?? fromSessionPhishing ?? fromSessionIs ?? false
    );
  }, [adminCase, defaultCaseData, sessionResult]);

  const caseEvidence = useMemo(() => {
    return (
      adminCase?.evidence ??
      defaultCaseData?.case?.evidence ??
      sessionResult?.case?.evidence ??
      sessionResult?.evidence ??
      ""
    );
  }, [adminCase, defaultCaseData, sessionResult]);

  const latestPrevention = useMemo(() => {
    if (!Array.isArray(preventions) || preventions.length === 0) {
      return null;
    }
    return preventions[preventions.length - 1];
  }, [preventions]);

  const victimFromSession = sessionResult
    ? {
        name: sessionResult.victimName ?? "알 수 없음",
        meta: {
          age: sessionResult.victimAge ?? "-",
          gender: sessionResult.victimGender ?? "-",
          address: sessionResult.victimAddress ?? "-",
          education: sessionResult.victimEducation ?? "-",
          job: sessionResult.victimJob ?? "-",
        },
        traits: { ocean: undefined, list: sessionResult.victimTraits ?? [] },
        knowledge: {
          comparative_notes: Array.isArray(sessionResult?.victimKnowledge)
            ? sessionResult.victimKnowledge
            : Array.isArray(sessionResult?.victimComparativeNotes)
              ? sessionResult.victimComparativeNotes
              : Array.isArray(sessionResult?.knowledge?.comparative_notes)
                ? sessionResult.knowledge.comparative_notes
                : [],
        },
      }
    : null;

  const victim = selectedCharacter ??
    victimFromSession ?? {
      name: "알 수 없음",
      meta: { age: "-", gender: "-", address: "-", education: "-", job: "-" },
      traits: { ocean: undefined, list: [] },
      knowledge: { comparative_notes: [] },
    };

  const oceanLabelMap = {
    openness: "개방성",
    neuroticism: "신경성",
    extraversion: "외향성",
    agreeableness: "친화성",
    conscientiousness: "성실성",
  };

  const oceanEntries =
    victim?.traits?.ocean && typeof victim.traits.ocean === "object"
      ? Object.entries(victim.traits.ocean).map(([k, v]) => ({
          label: oceanLabelMap[k] ?? k,
          value: v,
        }))
      : [];

  const traitList = Array.isArray(victim?.traits?.list)
    ? victim.traits.list
    : [];

  const phishingTypeText =
    selectedScenario?.type ??
    (Array.isArray(scenarios)
      ? (scenarios[0]?.type ?? "피싱 유형")
      : "피싱 유형");

  const rawAgentLogs = useMemo(() => {
    return (
      sessionResult?.agentLogs ??
      defaultCaseData?.agent_logs ??
      defaultCaseData?.agentLogs ??
      defaultCaseData?.agent?.logs ??
      adminCase?.agent_logs ??
      adminCase?.agentLogs ??
      []
    );
  }, [sessionResult, defaultCaseData, adminCase]);

  const filteredAgentLogs = useMemo(() => {
    if (!Array.isArray(rawAgentLogs)) return [];
    return rawAgentLogs.filter((l) => {
      if (typeof l === "string") return true;
      const v =
        l?.use_agent ??
        l?.useAgent ??
        l?.use_agent_flag ??
        l?.use_agent_value ??
        undefined;
      if (v === true || v === "true" || v === 1 || v === "1") return true;
      return false;
    });
  }, [rawAgentLogs]);

  function RiskBadge({ level }) {
    const lv = String(level || "").toLowerCase();
    let toneBg = THEME.border;
    if (lv.includes("high")) toneBg = THEME.danger;
    else if (lv.includes("medium")) toneBg = THEME.warn;
    else if (lv.includes("low")) toneBg = THEME.success;

    return (
      <span
        className="text-xs px-3 py-1 rounded font-semibold"
        style={{ backgroundColor: toneBg, color: THEME.black }}
      >
        위험도: {level ?? "-"}
      </span>
    );
  }

  return (
    <div
      style={{ backgroundColor: THEME.bg, color: THEME.text }}
      className="min-h-screen"
    >
      <div className="mx-auto min-h-screen p-6 md:p-10 xl:p-12 flex flex-col">
        <div className="flex items-center justify-between mb-10">
          <h1 className="text-4xl font-bold">시뮬레이션 리포트</h1>
          <div className="flex gap-3">
            <button
              onClick={() => setCurrentPage("simulator")}
              className="px-6 py-3 rounded-lg text-lg font-medium"
              style={{ 
                backgroundColor: THEME.panelDark, 
                color: THEME.text,
                border: `1px solid ${THEME.border}`
              }}
            >
              대화 보기
            </button>
            <button
              onClick={() => {
                setSelectedScenario(null);
                setSelectedCharacter(null);
                setMessages([]);
                setProgress(0);
                setCurrentPage("simulator");
              }}
              className="px-6 py-3 rounded-lg text-lg font-medium"
              style={{ backgroundColor: THEME.blurple, color: THEME.white }}
            >
              새 시뮬레이션
            </button>
          </div>
        </div>

        {sessionResult || (preventions && preventions.length > 0) ? (
          <div className="flex gap-10 flex-1 overflow-hidden">
            <div
              className="w-full lg:w-1/3 flex-shrink-0 space-y-8 pr-6"
              style={{ borderRight: `1px solid ${THEME.border}` }}
            >
              <div
                className="rounded-2xl p-8"
                style={{
                  backgroundColor: THEME.panel,
                  border: `1px solid ${THEME.border}`,
                }}
              >
                <h2
                  className="text-2xl font-semibold mb-5 flex items-center"
                  style={{ color: THEME.text }}
                >
                  <Shield className="mr-3" size={26} />
                  피싱 유형
                </h2>
                <div
                  className="text-xl font-medium"
                  style={{ color: THEME.blurple }}
                >
                  {phishingTypeText}
                </div>
              </div>

              <div
                className="rounded-2xl p-8"
                style={{
                  backgroundColor: THEME.panel,
                  border: `1px solid ${THEME.border}`,
                }}
              >
                <h2
                  className="text-2xl font-semibold mb-5 flex items-center"
                  style={{ color: THEME.text }}
                >
                  <User className="mr-3" size={26} />
                  피해자 정보
                </h2>

                <div className="space-y-5">
                  <div className="flex justify-center">
                    {victimImageUrl ? (
                      <img
                        src={victimImageUrl}
                        alt={victim.name}
                        className="w-24 h-24 rounded-full object-cover"
                      />
                    ) : (
                      <div
                        className="w-24 h-24 rounded-full flex items-center justify-center"
                        style={{ backgroundColor: THEME.border }}
                      >
                        <User size={48} color={THEME.text} />
                      </div>
                    )}
                  </div>

                  <div className="text-center">
                    <div
                      className="font-semibold text-xl mb-3"
                      style={{ color: THEME.text }}
                    >
                      {victim?.name}
                    </div>
                    <div
                      className="text-base space-y-2"
                      style={{ color: THEME.sub }}
                    >
                      <div>나이: {victim?.meta?.age}</div>
                      <div>성별: {victim?.meta?.gender}</div>
                      <div>거주지: {victim?.meta?.address}</div>
                      <div>학력: {victim?.meta?.education}</div>
                      {victim?.meta?.job && <div>직업: {victim.meta.job}</div>}
                    </div>
                  </div>

                  <div>
                    <h3
                      className="font-semibold text-lg mb-3"
                      style={{ color: THEME.text }}
                    >
                      성격 특성 (OCEAN)
                    </h3>

                    <div className="flex flex-wrap gap-3 mb-3">
                      {oceanEntries.length > 0 ? (
                        oceanEntries.map((e, idx) => (
                          <span
                            key={idx}
                            className="px-3 py-2 rounded-full text-sm font-medium"
                            style={{
                              backgroundColor: THEME.border,
                              color: THEME.black,
                            }}
                          >
                            {e.label}: {e.value}
                          </span>
                        ))
                      ) : (
                        <span className="text-sm" style={{ color: THEME.sub }}>
                          OCEAN 정보 없음
                        </span>
                      )}
                    </div>

                    {traitList?.length > 0 && (
                      <div className="flex flex-wrap gap-3">
                        {traitList.map((t, i) => (
                          <span
                            key={i}
                            className="px-4 py-2 rounded-full text-sm font-medium"
                            style={{
                              backgroundColor: THEME.border,
                              color: THEME.black,
                            }}
                          >
                            {t}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="mt-6">
                    <h3
                      className="font-semibold text-lg mb-3"
                      style={{ color: THEME.text }}
                    >
                      지식
                    </h3>
                    <div className="space-y-1">
                      {Array.isArray(victim?.knowledge?.comparative_notes) &&
                      victim.knowledge.comparative_notes.length > 0 ? (
                        victim.knowledge.comparative_notes.map((note, idx) => (
                          <div
                            key={idx}
                            className="text-sm font-medium leading-relaxed"
                            style={{ color: THEME.text }}
                          >
                            • {note}
                          </div>
                        ))
                      ) : (
                        <div className="text-sm" style={{ color: THEME.sub }}>
                          비고 없음
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              <div
                className="rounded-2xl p-8"
                style={{
                  backgroundColor: THEME.panel,
                  border: `1px solid ${THEME.border}`,
                }}
              >
                <h2
                  className="text-2xl font-semibold mb-5 flex items-center"
                  style={{ color: THEME.text }}
                >
                  <Bot className="mr-3" size={26} />
                  AI 에이전트
                </h2>
                <div className="flex items-center gap-4">
                  <Badge
                    tone={sessionResult?.agentUsed ? "success" : "neutral"}
                    COLORS={THEME}
                  >
                    {sessionResult?.agentUsed ? "사용" : "미사용"}
                  </Badge>
                </div>
              </div>
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto space-y-8 pr-2">
              <div
                className="rounded-2xl p-8"
                style={{
                  backgroundColor: THEME.panel,
                  border: `1px solid ${THEME.border}`,
                }}
              >
                <div className="flex items-center justify-between mb-5">
                  <h2
                    className="text-2xl font-semibold flex items-center"
                    style={{ color: THEME.text }}
                  >
                    <AlertTriangle className="mr-3" size={26} />
                    피싱 판정 결과
                  </h2>

                  <div className="ml-4">
                    <Badge
                      tone={casePhishing ? "primary" : "danger"}
                      COLORS={THEME}
                    >
                      {casePhishing ? "피싱 성공" : "피싱 실패"}
                    </Badge>
                  </div>
                </div>

                {adminCaseLoading && (
                  <div className="mb-3 text-sm" style={{ color: THEME.sub }}>
                    근거 불러오는 중…
                  </div>
                )}
                {adminCaseError && (
                  <div className="mb-3 text-sm" style={{ color: THEME.warn }}>
                    근거 조회 실패: {adminCaseError}
                  </div>
                )}

                <div
                  className="mt-2 p-4 rounded"
                  style={{
                    backgroundColor: THEME.bg,
                    border: `1px solid ${THEME.border}`,
                    color: THEME.text,
                  }}
                >
                  <h4 className="font-semibold mb-2">
                    {casePhishing ? "피싱 성공 근거" : "피싱 실패 근거"}
                  </h4>
                  <p
                    className="text-sm leading-relaxed whitespace-pre-wrap"
                    style={{ color: THEME.sub }}
                  >
                    {caseEvidence || "근거 정보가 없습니다."}
                  </p>
                </div>
              </div>

              <div
                className="rounded-2xl p-8"
                style={{
                  backgroundColor: THEME.panel,
                  border: `1px solid ${THEME.border}`,
                }}
              >
                <div className="flex items-center justify-between mb-5">
                  <h2
                    className="text-2xl font-semibold flex items-center"
                    style={{ color: THEME.text }}
                  >
                    <Shield className="mr-3" size={26} />
                    개인화 예방법
                  </h2>
                  {latestPrevention?.content?.analysis?.risk_level && (
                    <RiskBadge
                      level={latestPrevention.content.analysis.risk_level}
                    />
                  )}
                </div>

                {!latestPrevention ? (
                  <div className="text-sm" style={{ color: THEME.sub }}>
                    예방법 정보가 없습니다.
                  </div>
                ) : (
                  <>
                    <div
                      className="p-4 rounded mb-6"
                      style={{
                        backgroundColor: THEME.bg,
                        border: `1px solid ${THEME.border}`,
                        color: THEME.text,
                      }}
                    >
                      <h3 className="font-semibold mb-2">요약</h3>
                      <p
                        className="text-sm leading-relaxed whitespace-pre-wrap"
                        style={{ color: THEME.sub }}
                      >
                        {latestPrevention.content?.summary ??
                          "요약 정보가 없습니다."}
                      </p>
                    </div>

                    <div className="mb-6">
                      <h3
                        className="font-semibold mb-3"
                        style={{ color: THEME.text }}
                      >
                        실천 단계
                      </h3>
                      {Array.isArray(latestPrevention.content?.steps) &&
                      latestPrevention.content.steps.length > 0 ? (
                        <ul
                          className="list-disc pl-6 space-y-1 text-sm"
                          style={{ color: THEME.sub }}
                        >
                          {latestPrevention.content.steps.map((s, i) => (
                            <li key={i}>{s}</li>
                          ))}
                        </ul>
                      ) : (
                        <div className="text-sm" style={{ color: THEME.sub }}>
                          단계 정보 없음
                        </div>
                      )}
                    </div>

                    <div className="mb-6">
                      <h3
                        className="font-semibold mb-3"
                        style={{ color: THEME.text }}
                      >
                        핵심 팁
                      </h3>
                      {Array.isArray(latestPrevention.content?.tips) &&
                      latestPrevention.content.tips.length > 0 ? (
                        <ul
                          className="list-disc pl-6 space-y-1 text-sm"
                          style={{ color: THEME.sub }}
                        >
                          {latestPrevention.content.tips.map((t, i) => (
                            <li key={i}>{t}</li>
                          ))}
                        </ul>
                      ) : (
                        <div className="text-sm" style={{ color: THEME.sub }}>
                          팁 정보 없음
                        </div>
                      )}
                    </div>

                    <div>
                      <h3
                        className="font-semibold mb-3"
                        style={{ color: THEME.text }}
                      >
                        판단 근거
                      </h3>
                      {Array.isArray(
                        latestPrevention.content?.analysis?.reasons
                      ) &&
                      latestPrevention.content.analysis.reasons.length > 0 ? (
                        <ul
                          className="list-disc pl-6 space-y-1 text-sm"
                          style={{ color: THEME.sub }}
                        >
                          {latestPrevention.content.analysis.reasons.map(
                            (r, i) => (
                              <li key={i}>{r}</li>
                            )
                          )}
                        </ul>
                      ) : (
                        <div className="text-sm" style={{ color: THEME.sub }}>
                          판단 근거 없음
                        </div>
                      )}
                    </div>
                  </>
                )}
              </div>

              <div
                className="rounded-2xl p-8"
                style={{
                  backgroundColor: THEME.panel,
                  border: `1px solid ${THEME.border}`,
                }}
              >
                <h2
                  className="text-2xl font-semibold mb-5 flex items-center"
                  style={{ color: THEME.text }}
                >
                  <ExternalLink className="mr-3" size={26} />
                  사례 출처 및 참고자료
                </h2>

                <div className="space-y-5">
                  {(() => {
                    const src =
                      selectedScenario?.source ??
                      (Array.isArray(scenarios)
                        ? scenarios[0]?.source
                        : null) ??
                      defaultCaseData?.case?.source ??
                      null;

                    if (!src) {
                      return (
                        <div
                          className="p-5 rounded-lg"
                          style={{
                            backgroundColor: THEME.bg,
                            color: THEME.sub,
                          }}
                        >
                          <h3
                            className="font-semibold text-lg mb-3"
                            style={{ color: THEME.text }}
                          >
                            참고 사례
                          </h3>
                          <p className="text-base mb-4 leading-relaxed">
                            출처 정보가 없습니다.
                          </p>
                        </div>
                      );
                    }

                    const { title, page, url } = src;

                    return (
                      <div
                        className="p-5 rounded-lg"
                        style={{ backgroundColor: THEME.bg, color: THEME.sub }}
                      >
                        <h3
                          className="font-semibold text-lg mb-3"
                          style={{ color: THEME.text }}
                        >
                          {title ?? "참고 사례"}
                        </h3>
                        {page && (
                          <div
                            className="text-base mb-2"
                            style={{ color: THEME.sub }}
                          >
                            페이지: {page}
                          </div>
                        )}
                        <p
                          className="text-base mb-4 leading-relaxed"
                          style={{ color: THEME.sub }}
                        >
                          {sessionResult?.caseSource ??
                            "본 시뮬레이션은 실제 보이스피싱 사례를 바탕으로 제작되었습니다."}
                        </p>
                        <div className="space-y-3">
                          <div className="flex items-center gap-3">
                            <span
                              className="text-base font-medium"
                              style={{ color: THEME.text }}
                            >
                              출처:
                            </span>
                            {url ? (
                              <a
                                href={url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-base underline"
                                aria-label="참고자료 링크 열기"
                                style={{ color: THEME.blurple }}
                              >
                                {url}
                              </a>
                            ) : (
                              <span
                                className="text-base"
                                style={{ color: THEME.sub }}
                              >
                                {sessionResult?.source ?? "-"}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })()}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div
            className="rounded-2xl p-8"
            style={{
              backgroundColor: THEME.panel,
              border: `1px solid ${THEME.border}`,
            }}
          >
            <p className="text-base" style={{ color: THEME.sub }}>
              세션 결과가 없습니다. 시뮬레이션을 먼저 실행해주세요.
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

export default ReportPage;