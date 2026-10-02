import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// ADR 0009: component tests only, in jsdom. `@/` resolves as tsconfig.json says.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": new URL("./src", import.meta.url).pathname } },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
});
