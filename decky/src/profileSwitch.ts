// Pure profile-switch flow (no Decky imports, so it is testable under plain Node).
import type { WriteResult } from "./backend";

export interface SwitchDeps {
  apply: (name: string) => Promise<boolean | WriteResult>;
  readProfile: () => Promise<string>;
  readMaxFans: () => Promise<boolean>;
}

export interface SwitchResult {
  ok: boolean;
  /** Authoritative state re-read after the call, success or not (absent if that read failed). */
  profile?: string;
  maxFans?: boolean;
  message?: string;
}

/** Apply a profile, then re-read the active profile and Max Fans together
 *  (entering/leaving Turbo changes Max Fans). A false result or a rejected call is `ok: false`,
 *  but the profile may still be active (apply returns false when any single step fails), so
 *  the state is re-read either way and the caller shows what the backend reports. */
export async function switchProfile(deps: SwitchDeps, name: string): Promise<SwitchResult> {
  let ok = false;
  let message: string | undefined;
  try {
    const result = await deps.apply(name);
    if (typeof result === "boolean") ok = result;
    else {
      ok = result.ok;
      message = result.message;
    }
  } catch {
    ok = false;
  }
  const [profile, maxFans] = await Promise.all([
    deps.readProfile().catch(() => undefined),
    deps.readMaxFans().catch(() => undefined),
  ]);
  return { ok, profile, maxFans, ...(message ? { message } : {}) };
}
