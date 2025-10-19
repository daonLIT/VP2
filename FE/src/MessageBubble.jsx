import { useEffect, useState } from "react";

function getRiskColors(pct) {
  const v = Math.max(0, Math.min(100, Number(pct) || 0));
  if (v >= 70)
    return { border: "rgba(239,68,68,0.75)", bg: "rgba(239,68,68,0.10)", text: "#EF4444", tagBg: "rgba(239,68,68,0.12)" };
  if (v >= 41)
    return { border: "rgba(245,158,11,0.75)", bg: "rgba(245,158,11,0.10)", text: "#F59E0B", tagBg: "rgba(245,158,11,0.12)" };
  return { border: "rgba(16,185,129,0.75)", bg: "rgba(16,185,129,0.10)", text: "#10B981", tagBg: "rgba(16,185,129,0.12)" };
}

const MessageBubble = ({ message, selectedCharacter, victimImageUrl, COLORS }) => {
  const isVictim = message.sender === "victim";
  const isScammer = message.sender === "offender";
  const isSystem = message.type === "system";
  const isAnalysis = message.type === "analysis";
  const isSpinner = isSystem && String(message.content || "").includes("🔄");

  const [animatedConvinced, setAnimatedConvinced] = useState(0);
  useEffect(() => {
    if (typeof message?.convincedPct === "number") {
      const t = setTimeout(() => setAnimatedConvinced(message.convincedPct), 150);
      return () => clearTimeout(t);
    }
  }, [message?.convincedPct]);

  // ✅ 백엔드 데이터에서 content/text/message 다 확인
  const displayText = message?.content || message?.message || message?.text || "";

  const convincedPct =
    typeof animatedConvinced === "number" ? Math.max(0, Math.min(100, animatedConvinced)) : null;

  // ✅ 테마 색상 기본값 안전하게 지정
  const themeText = COLORS?.text || "#E5E7EB";
  const themeBorder = COLORS?.border || "#3F4147";
  const themePanel = COLORS?.panel || "#2B2D31";
  const themeWhite = COLORS?.white || "#FFFFFF";

  const bubbleBg = isSystem
    ? "rgba(88,101,242,.12)"
    : isAnalysis
    ? "rgba(254,231,92,.12)"
    : isVictim
    ? themeWhite
    : themePanel;

  const bubbleTextColor = isSystem
    ? themeText
    : isAnalysis
    ? COLORS.warn || "#FEE75C"
    : isVictim
    ? "#111827"
    : themeText;

  const risk = getRiskColors(convincedPct ?? 0);

  return (
    <div className={`flex ${isVictim ? "justify-end" : "justify-start"} mb-3`}>
      <div
        className="max-w-md lg:max-w-lg px-5 py-3 rounded-2xl border"
        style={{
          backgroundColor: bubbleBg,
          color: bubbleTextColor,
          border: `1px solid ${themeBorder}`,
        }}
      >
        {/* 🔄 스피너 */}
        {isSpinner && (
          <div className="flex space-x-1 mb-4">
            {[0, 0.1, 0.2, 0.3, 0.4].map((d, i) => (
              <div key={i} className="w-1 h-8 bg-[#5865F2] animate-pulse" style={{ animationDelay: `${d}s` }} />
            ))}
          </div>
        )}

        {/* 👤 피싱범 헤더 */}
        {isScammer && (
          <div className="flex items-center mb-2">
            <img
              src={new URL("./assets/offender_profile.png", import.meta.url).href}
              alt="피싱범"
              className="w-8 h-8 rounded-full object-cover mr-2"
            />
            <span className="text-sm font-medium" style={{ color: COLORS.sub }}>
              피싱범
            </span>
          </div>
        )}

        {/* 🙍‍♀️ 피해자 헤더 */}
        {isVictim && selectedCharacter && (
          <div className="flex items-center mb-2">
            {victimImageUrl ? (
              <img src={victimImageUrl} alt={selectedCharacter.name} className="w-8 h-8 rounded-full object-cover mr-2" />
            ) : (
              <span className="text-lg mr-2">{selectedCharacter.avatar || "👤"}</span>
            )}
            <span className="text-sm font-medium" style={{ color: "#6B7280" }}>
              {selectedCharacter.name}
            </span>
            {typeof convincedPct === "number" && (
              <div className="flex items-center gap-1 min-w-[120px] ml-3">
                <div className="flex-1 h-2 bg-[#e5e7eb] rounded overflow-hidden">
                  <div
                    className="h-full transition-all duration-1000 ease-in-out"
                    style={{
                      width: `${convincedPct}%`,
                      backgroundColor:
                        convincedPct >= 70 ? "#EF4444" : convincedPct >= 41 ? "#F59E0B" : "#10B981",
                    }}
                  />
                </div>
                <span className="text-[10px] w-8 text-right text-gray-400">{convincedPct}%</span>
              </div>
            )}
          </div>
        )}

        {/* 💬 본문 */}
        <div
          className="leading-relaxed whitespace-pre-line text-[15px]"
          style={{ color: bubbleTextColor }}
        >
          {displayText.trim() ? `💬 ${displayText}` : "(메시지 없음)"}
        </div>

        {/* 🕒 타임스탬프 */}
        <div className="text-xs mt-2 opacity-70" style={{ color: COLORS.sub }}>
          {message.timestamp}
        </div>
      </div>
    </div>
  );
};

export default MessageBubble;
