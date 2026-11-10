// Unit-tests van de web-UI (pure functies + server-gerenderde markup van componenten). Draaien: npm run test:unit
import path from "node:path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": path.resolve(import.meta.dirname, ".") } },
  oxc: { jsx: { runtime: "automatic" } },
  test: {
    include: ["tests/unit/**/*.test.{ts,tsx}"],
    environment: "node",
    pool: "threads",
    maxWorkers: 2,
  },
});
