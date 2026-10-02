import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// ADR 0009: component tests only, in jsdom. `@/` resolves as tsconfig.json says.
export default defineConfig({
  plugins: [react()],
  // fileURLToPath, not `.pathname`: on Windows that is "/C:/...", which breaks `@/`.
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
});
