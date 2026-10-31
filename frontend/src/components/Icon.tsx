import type { CSSProperties, ReactNode } from "react";

type IconName = "overview" | "lab" | "discovery" | "scenarios" | "agents" | "experiments" | "simulations" | "failures" | "arrow" | "refresh" | "activity" | "ghost";

const paths: Record<IconName, ReactNode> = {
  lab: <><path d="M4 4h6l2 2 2-2h6v16h-6l-2 2-2-2H4V4Zm8 2v16M7 8h2m-2 4h2m6-4h2m-2 4h2" /></>,
  discovery: <><circle cx="10" cy="10" r="6" /><path d="m15 15 6 6M7 10h6m-3-3v6" /></>,
  experiments: <><path d="M5 20V10m7 10V4m7 16V7M3 20h18" /><path d="m3 6 4-3m10 0 4 2" /></>,
  agents: <><rect x="5" y="6" width="14" height="13" rx="3" /><path d="M12 3v3M3 11h2m14 0h2M9 11v2m6-2v2m-6 3h6M8 19v2m8-2v2" /></>,
  overview: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
  scenarios: <><path d="M6 3v18M18 3v18M12 3v3m0 5v2m0 5v3" /><path d="m3 8 3-3 3 3m6 8 3 3 3-3" /></>,
  simulations: <><rect x="3" y="4" width="18" height="14" rx="2" /><path d="m10 8 5 3-5 3V8ZM8 21h8m-4-3v3" /></>,
  failures: <><path d="m12 3 10 18H2L12 3Zm0 6v5m0 3v.1" /></>,
  arrow: <><path d="M4 12h16m-6-6 6 6-6 6" /></>,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3a7 7 0 0 0 11.6-1" /></>,
  activity: <><path d="M3 12h4l3-8 4 16 3-8h4" /></>,
  ghost: <><path d="M5 21V10a7 7 0 0 1 14 0v11l-3.5-2-3.5 2-3.5-2L5 21Z" /><path d="M9 10v2m6-2v2" /></>,
};

export function Icon({ name, size = 20, style }: { name: IconName; size?: number; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}>{paths[name]}</svg>;
}
