const AGENT_W_KEY = "ob-note:agent-w";

export const AGENT_W_DEFAULT = 360;
export const AGENT_W_MIN = 280;
export const AGENT_W_MAX = 640;

export function getAgentWidth(): number {
  try {
    const n = Number(localStorage.getItem(AGENT_W_KEY));
    if (Number.isFinite(n) && n >= AGENT_W_MIN && n <= AGENT_W_MAX) {
      return Math.round(n);
    }
  } catch {
    /* ignore */
  }
  return AGENT_W_DEFAULT;
}

export function setAgentWidth(width: number): void {
  const clamped = clampAgentWidth(width);
  try {
    localStorage.setItem(AGENT_W_KEY, String(clamped));
  } catch {
    /* ignore */
  }
}

export function clampAgentWidth(width: number): number {
  return Math.min(AGENT_W_MAX, Math.max(AGENT_W_MIN, Math.round(width)));
}
