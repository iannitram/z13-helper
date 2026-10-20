// Pure profile-switch flow (no Decky imports, so it is testable under plain Node).

export interface SwitchDeps {
  apply: (name: string) => Promise<boolean>;
  readProfile: () => Promise<string>;
  readMaxFans: () => Promise<boolean>;
}

export interface SwitchResult {
  ok: boolean;
  /** Authoritative state re-read after a successful switch (absent if that read failed). */
  profile?: string;
  maxFans?: boolean;
}

/** Apply a profile; on success re-read the active profile and Max Fans together
 *  (entering/leaving Turbo changes Max Fans). A false result or a rejected call is a failure. */
export async function switchProfile(deps: SwitchDeps, name: string): Promise<SwitchResult> {
  let ok = false;
  try {
    ok = await deps.apply(name);
  } catch {
    ok = false;
  }
  if (!ok) return { ok: false };
  const [profile, maxFans] = await Promise.all([
    deps.readProfile().catch(() => undefined),
    deps.readMaxFans().catch(() => undefined),
  ]);
  return { ok: true, profile, maxFans };
}
