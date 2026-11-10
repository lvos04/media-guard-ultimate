/**
 * Hooguit 4 achtergrond-GET's tegelijk naar MediaGuard (H5/N25). Browsers openen per host maar 6
 * HTTP/1.1-verbindingen; met de live-verbinding en een trage aanroep erbij liep alles vast (28–44 s
 * voor een paginawissel). Zo blijven er altijd verbindingen vrij voor navigatie en wijzigingen.
 */
const MAX_PARALLEL = 4;
let active = 0;
const waiting: Array<() => void> = [];

export async function limited<T>(task: () => Promise<T>): Promise<T> {
  if (active >= MAX_PARALLEL) await new Promise<void>((resolve) => waiting.push(resolve));
  active += 1;
  try {
    return await task();
  } finally {
    active -= 1;
    waiting.shift()?.();
  }
}
