// Shared design tokens (ADR 0002). Rollup bundles src/design_tokens.json at build time.
import tokens from "../../src/design_tokens.json";

export const ACCENT = tokens.accents;
export const COLORS = tokens.colors;
export const SPACING = tokens.spacing;
export const TOUCH_MIN_PX = tokens.touch.touch_min_px;

/** Active-choice highlight and warning text colours, from the shared accents. */
export const ACTIVE_BG = ACCENT.balanced;
export const WARN_COLOR = ACCENT.custom;
