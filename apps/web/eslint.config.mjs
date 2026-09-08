import js from "@eslint/js";
import nextPlugin from "@next/eslint-plugin-next";
import reactPlugin from "eslint-plugin-react";
import hooksPlugin from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

// eslint-config-next is legacy-only and loads @rushstack/eslint-patch, which
// throws under ESLint 9 flat config. The plugin it wraps works directly.
export default tseslint.config(
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: {
      "@next/next": nextPlugin,
      react: reactPlugin,
      "react-hooks": hooksPlugin,
    },
    languageOptions: {
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    settings: { react: { version: "detect" } },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs["core-web-vitals"].rules,
      ...hooksPlugin.configs.recommended.rules,
      "react/jsx-uses-react": "off",
      "react/react-in-jsx-scope": "off",

      // A feature never imports another feature. Shared code lives in
      // src/shared — the same boundary the backend enforces with import-linter.
      // See docs/architecture/module-boundaries.md.
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@/features/*/*", "**/features/*/*"],
              message:
                "Features are independent. Import from @/shared instead, or ask the other feature's owner to promote it.",
            },
          ],
        },
      ],
    },
  },
  {
    // Generated: tokens.css has no JS, but the generator itself is plain node.
    files: ["scripts/**/*.mjs"],
    languageOptions: { globals: { process: "readonly", console: "readonly" } },
  },
);
