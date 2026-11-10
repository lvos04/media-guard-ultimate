// Typen voor scripts/check-peil.mjs (tsconfig: allowJs false), alleen gebruikt door tests/unit/check-peil.test.ts.
export const PEIL_VERSION: string;
export function parseHeaded(text: string): { version: string | null; sha: string | null; commit: string | null; body: string };
export function checkCopies(webDir?: string, designDir?: string): string[];
