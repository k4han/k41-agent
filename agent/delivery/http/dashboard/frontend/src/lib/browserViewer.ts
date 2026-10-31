export type BrowserHandoffResume = {
  action: "browser_handoff";
  handoff_id: string;
  decision: "return" | "cancel";
};

export function browserPointer(clientX: number, clientY: number,
  rect: { left: number; top: number; width: number; height: number }, width = 1280, height = 800) {
  return {
    x: Math.max(0, Math.min(width, (clientX - rect.left) * width / Math.max(1, rect.width))),
    y: Math.max(0, Math.min(height, (clientY - rect.top) * height / Math.max(1, rect.height))),
  };
}

export function browserModifiers(event: { altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }) {
  return Number(event.altKey) | (Number(event.ctrlKey) << 1) | (Number(event.metaKey) << 2) | (Number(event.shiftKey) << 3);
}

export function browserImageUrl(result: unknown): string | null {
  if (typeof result !== "string") return null;
  return result.match(/Browser image: (\/dashboard-api\/browser\/artifacts\/[a-f0-9]{32})\b/)?.[1] || null;
}
