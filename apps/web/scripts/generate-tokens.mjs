/**
 * Generate CSS custom properties from docs/design/design-tokens.json.
 *
 * The token file is the single source of truth. Never hand-edit the output —
 * run `pnpm run gen:tokens` and commit it. CI fails if the two disagree.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const TOKENS = resolve(here, "../../../docs/design/design-tokens.json");
const OUT = resolve(here, "../src/app/tokens.css");

const tokens = JSON.parse(readFileSync(TOKENS, "utf8"));

const value = (node) => (node && typeof node === "object" && "$value" in node ? node.$value : node);

/** onAccent -> on-accent, so the CSS reads like CSS. */
const kebab = (s) => s.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase();

/** Flatten a color group into `--color-<group>-<name>` declarations. */
function colorVars(theme) {
  const out = [];
  for (const [group, entry] of Object.entries(theme)) {
    if (group.startsWith("$")) continue;
    if (entry && typeof entry === "object" && "$value" in entry) {
      out.push([`--color-${kebab(group)}`, entry.$value]);
      continue;
    }
    for (const [name, leaf] of Object.entries(entry)) {
      if (name.startsWith("$")) continue;
      const v = value(leaf);
      if (typeof v === "string") out.push([`--color-${kebab(group)}-${kebab(name)}`, v]);
      else if (v && typeof v === "object") {
        for (const [sub, subLeaf] of Object.entries(v)) {
          if (sub.startsWith("$")) continue;
          out.push([`--color-${kebab(group)}-${kebab(name)}-${kebab(sub)}`, value(subLeaf)]);
        }
      }
    }
  }
  return out;
}

const decl = (pairs, indent = "  ") =>
  pairs.map(([k, v]) => `${indent}${k}: ${v};`).join("\n");

const { typography, spacing, shape, control, border, layout, motion } = tokens;

const staticVars = [
  ["--font-sans", typography.fontFamily.sans.$value],
  ["--font-mono", typography.fontFamily.mono.$value],
  ...Object.entries(typography.scale).flatMap(([name, s]) => {
    const rows = [[`--text-${name}`, `${s.size}px`], [`--text-${name}-weight`, String(s.weight)]];
    if (s.lineHeight) rows.push([`--text-${name}-leading`, String(s.lineHeight)]);
    if (s.letterSpacing) rows.push([`--text-${name}-tracking`, s.letterSpacing]);
    return rows;
  }),
  ...spacing.scale.$value.map((n) => [`--space-${n}`, `${n}px`]),
  ...["page", "card", "row", "topbar", "sidebar"].map((k) => [`--space-${k}`, `${value(spacing[k])}px`]),
  ...Object.entries(layout).map(([k, v]) => [`--layout-${k}`, `${value(v)}px`]),
  ["--radius", `${value(shape.radius)}px`],
  ["--dot-size", `${value(shape.dot)}px`],
  ["--bar-thickness", `${value(shape.bar)}px`],
  ["--shadow-overlay", value(shape.shadow)],
  ...Object.entries(control.height).map(([k, v]) => [`--control-h-${k}`, `${v}px`]),
  ...Object.entries(control.paddingX).map(([k, v]) => [`--control-px-${k}`, `${v}px`]),
  ...Object.entries(control.fontSize).map(([k, v]) => [`--control-text-${k}`, `${v}px`]),
  ["--control-weight", String(control.fontWeight)],
  ...Object.entries(border).map(([k, v]) => [`--border-${k}`, value(v)]),
  ["--recording-glow-from", motion.recording.glow.from],
  ["--recording-glow-to", motion.recording.glow.to],
  ["--recording-glow-duration", motion.recording.glow.duration],
];

const light = colorVars(tokens.color.light);
const dark = colorVars(tokens.color.dark);

const css = `/*
 * Generated from docs/design/design-tokens.json by scripts/generate-tokens.mjs.
 * Do not edit. Run \`pnpm run gen:tokens\` and commit the result.
 *
 * Light is defined on bare :root so a value always exists. Dark is redefined
 * twice — under the system preference, and under an explicit [data-theme] — so
 * the toggle wins in both directions. Dark is a user preference and never
 * signifies a functional state; recording is the red window frame, in either
 * theme. See docs/design/ui-spec.md.
 */

:root {
${decl(light)}

${decl(staticVars)}
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
${decl(dark, "    ")}
  }
}

:root[data-theme="dark"] {
${decl(dark)}
}
`;

writeFileSync(OUT, css);
console.log(`wrote ${OUT.replace(process.cwd() + "/", "")} (${light.length} light, ${dark.length} dark, ${staticVars.length} static)`);
