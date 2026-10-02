import js from "@eslint/js";
import nextPlugin from "@next/eslint-plugin-next";
import reactPlugin from "eslint-plugin-react";
import hooksPlugin from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

// A per-person share of speech under any of the names it has had in this repo.
const SPEECH_SHARE = "/speaking_?ratio|speakingratio|talk_?time|speech_?volume|speaker_?share/i";
const SPEECH_SHARE_MESSAGE =
  "No screen shows a person's speaking ratio (invariant 11, docs/architecture/privacy.md section 3).";

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

      // Invariant 11, its negative half (ADR 0009, decision 5): no screen shows
      // one person's speaking ratio to anyone else, and the speaker gets theirs
      // by DM, never in the app. A tripwire over the whole app, so it fires in
      // the PR that introduces the field; the contract-level test in
      // packages/contracts is what makes the payload unable to carry it.
      "no-restricted-syntax": [
        "error",
        {
          selector: `Identifier[name=${SPEECH_SHARE}]`,
          message: SPEECH_SHARE_MESSAGE,
        },
        {
          selector: `Literal[value=${SPEECH_SHARE}]`,
          message: SPEECH_SHARE_MESSAGE,
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
