import { callable } from "@decky/api";

// Backend methods (main.py). Names must match the Python method names exactly.

export interface WriteResult {
  ok: boolean;
  message: string;
}

export const isWriteTimeout = (message: string) =>
  message.startsWith("z13ctl write timed out after ");

// ---- Max Fans / profiles ----
export const getMaxFans = callable<[], boolean>("get_max_fans");
export const setMaxFans = callable<[enabled: boolean], WriteResult>("set_max_fans");
export const getProfile = callable<[], string>("get_profile");
export const setProfile = callable<[profile: string], WriteResult>("set_profile");
// App profiles (Silent / Balanced / Turbo / Boondock / custom…) from the shared
// profiles.json; falls back to the raw platform profiles if the engine is missing.
export const getProfiles = callable<[], { names: string[]; active: string }>("get_profiles");
export const setAppProfile = callable<[name: string], WriteResult>("set_app_profile");

// ---- Phase 11: power limits / battery / lighting / telemetry ----
export type BrightnessLevel = "off" | "low" | "medium" | "high";

export interface PowerState {
  active: string;          // active profile name ("" if profiles.json is unavailable)
  tdp_enabled: boolean;    // the active profile has a TDP override
  pl1: number;
  pl2: number;
  pl3: number;
  pl_min: number;
  pl_max: number;
  pl_ranges?: { pl1: [number, number]; pl2: [number, number]; pl3: [number, number] };
  tdp_note?: string;       // stored limits outside the kernel's range: requested vs shown
  pl1_safe_max: number;    // above this z13ctl needs --force
  battery_limit: number;
  brightness: BrightnessLevel | "";
}

export interface ApplyResult {
  ok: boolean;             // hardware write succeeded
  stale?: boolean;         // rejected: the request's profile is no longer the active one
  saved?: boolean;         // profiles.json updated
  message: string;
}

export interface TdpResult extends ApplyResult {
  tdp: { pl1: number; pl2: number; pl3: number; force: boolean };
}

export interface BrightnessResult extends ApplyResult {
  failed: string[];        // devices that didn't take the level ("keyboard" | "lightbar")
}

export interface Telemetry {
  apu: number | null;      // degC
  gpu: number | null;      // degC
  fan1: number | null;     // RPM
  fan2: number | null;     // RPM
}

export const getPowerState = callable<[], PowerState>("get_power_state");
export const setTdp = callable<
  [enabled: boolean, pl1: number, pl2: number, pl3: number, profile: string],
  TdpResult
>("set_tdp");
export const setBatteryLimit = callable<
  [value: number, profile: string],
  ApplyResult & { value: number }
>(
  "set_battery_limit",
);
export const setBrightness = callable<[level: BrightnessLevel], BrightnessResult>("set_brightness");
export const getTelemetry = callable<[], Telemetry>("get_telemetry");

/** Same rule as main.py clamp_tdp / the desktop Fans + Power window:
 *  sPPT >= SPL, fPPT >= sPPT, all within [min, max]. */
export function clampTdp(
  v: { pl1: number; pl2: number; pl3: number },
  min: number,
  max: number,
  ranges?: PowerState["pl_ranges"],
): { pl1: number; pl2: number; pl3: number } {
  const one = (x: number, k: "pl1" | "pl2" | "pl3") => {
    const [lo, hi] = ranges?.[k] ?? [min, max];
    return Math.max(lo, Math.min(hi, Math.round(x)));
  };
  const pl1 = one(v.pl1, "pl1");
  const pl2 = Math.max(one(v.pl2, "pl2"), pl1);
  const pl3 = Math.max(one(v.pl3, "pl3"), pl2);
  return { pl1, pl2, pl3 };
}

/** Run a backend call, turning a thrown error into a failed result. */
export async function safely<T extends ApplyResult>(p: Promise<T>): Promise<T | ApplyResult> {
  try {
    return await p;
  } catch (e) {
    return { ok: false, saved: false, message: `Backend error: ${String(e)}` };
  }
}
