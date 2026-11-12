// Build-time source of truth is the framework-neutral desktop token JSON.
import tokens from "../../src/design_tokens.json";

export const designTokens = tokens;
export const touchTargetMin = tokens.touchTarget.minimum;
export const accentColors = tokens.accent;
export const spacing = tokens.spacing;
