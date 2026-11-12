// Design tokens for the Decky panel: the same file the desktop app reads
// (src/design_tokens.json, docs/adr/0002-shared-touch-ui.md), bundled at
// build time. No colour is written anywhere else in decky/src;
// tests/test_no_hardcoded_colours.py fails if one is.
import design from "../../src/design_tokens.json";

// Steam's Quick Access Menu is always dark, so the panel uses the dark set.
const colours = design.color.dark;

export type ColourToken = keyof typeof colours;

/** The CSS variable for a token: "on_accent" -> "--z13-on-accent". */
export const cssName = (token: string): string => `--z13-${token.replace(/_/g, "-")}`;

/**
 * A CSS colour for a token, as `var(--z13-accent, <token value>)`: the
 * variable from TOKEN_CSS when it is defined, the token's own value otherwise.
 */
export const colour = (token: ColourToken): string => `var(${cssName(token)}, ${colours[token]})`;

/** One `:root` rule that defines every colour token as a CSS variable. */
export const TOKEN_CSS = `:root{${Object.entries(colours)
  .map(([token, value]) => `${cssName(token)}:${value}`)
  .join(";")}}`;

/** Smallest touch target, in px (the shared touch layout, #133). */
export const TOUCH_MIN_PX: number = design.size.touch_min_px;
