import js from "@eslint/js";
import nextPlugin from "@next/eslint-plugin-next";
import reactPlugin from "eslint-plugin-react";
import hooksPlugin from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

// A per-person share of speech under any of the names it has had in this repo.
const SPEECH_SHARE = "/speaking_?ratio|speakingratio|talk_?time|speech_?volume|speaker_?share/i";
const SPEECH_SHARE_MESSAGE =
  "No screen shows one person's speaking ratio to anyone else (invariant 11, privacy.md section 3). A screen for the speaker's own share disables this line with the reason.";

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
      // one person's speaking ratio to anyone else. A tripwire over the whole
      // app, so it fires in the PR that introduces the field; the contract-level
      // test in packages/contracts is what makes a shared payload unable to
      // carry it. privacy.md section 3 lets the speaker see their own share
      // (GET /me/speaking-ratio/{meeting_id}): a screen that shows only that
      // passes with `// eslint-disable-next-line no-restricted-syntax -- own
      // share only, privacy.md section 3`, which a reviewer then sees.
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
