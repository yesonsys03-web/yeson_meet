// === ANCHOR: MEETING_TITLE_START ===
/// Auto-generated meeting title for one-click start: "YYYY-MM-DD HH:mm 회의".
/// Operators can rename it afterwards.
export function formatMeetingTitle(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  const ymd = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const hm = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return `${ymd} ${hm} 회의`;
}
// === ANCHOR: MEETING_TITLE_END ===
