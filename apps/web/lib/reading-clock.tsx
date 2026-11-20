"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

const Clock = createContext<number | null>(null);

export function ReadingClock({ children }: { children: ReactNode }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const timer = window.setInterval(tick, 5_000);
    document.addEventListener("visibilitychange", tick);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, []);
  return <Clock.Provider value={now}>{children}</Clock.Provider>;
}

export function useReadingTime() {
  const tick = useContext(Clock);
  const [initial] = useState(Date.now);
  return tick ?? initial;
}
