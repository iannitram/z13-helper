import { PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { clampTdp, PowerState, safely, setTdp, TdpResult } from "../backend";
import { useCommitOnRelease } from "../hooks";

type Rails = { pl1: number; pl2: number; pl3: number };

const RAILS: { key: keyof Rails; label: string; description: string }[] = [
  { key: "pl1", label: "SPL", description: "CPU sustained" },
  { key: "pl2", label: "sPPT", description: "CPU long boost" },
  { key: "pl3", label: "fPPT", description: "CPU short boost" },
];

/**
 * G-Helper's "Power Limits" block (desktop Fans + Power window, left column):
 * SPL / sPPT / fPPT 5..93 W with sPPT >= SPL and fPPT >= sPPT, written on release,
 * plus "Apply power limits" (= the active profile has a TDP override).
 */
export function PowerLimits({ state }: { state: PowerState | null }) {
  const [enabled, setEnabled] = useState(false);
  const [rails, setRailsState] = useState<Rails>({ pl1: 70, pl2: 86, pl3: 86 });
  // Latest values without waiting for a re-render (D-pad steps can arrive back to back).
  const railsRef = useRef(rails);
  const setRails = (r: Rails) => {
    railsRef.current = r;
    setRailsState(r);
  };
  const [msg, setMsg] = useState("");
  const min = state?.pl_min ?? 5;
  const max = state?.pl_max ?? 93;
  const safeMax = state?.pl1_safe_max ?? 75;
  const haveProfile = !!state?.active;

  const push = async (on: boolean, r: Rails) => {
    setMsg("Applying…");
    const res = await safely<TdpResult>(setTdp(on, r.pl1, r.pl2, r.pl3));
    setMsg(res.message);
    if ("tdp" in res && res.tdp) setRails({ pl1: res.tdp.pl1, pl2: res.tdp.pl2, pl3: res.tdp.pl3 });
    return res;
  };

  const { schedule, cancel, handlers } = useCommitOnRelease<Rails>((r) => {
    void push(true, r);
  });

  // New power state (panel opened, or the profile changed): show it, drop anything pending.
  useEffect(() => {
    if (!state) return;
    cancel();
    setEnabled(state.tdp_enabled);
    setRails({ pl1: state.pl1, pl2: state.pl2, pl3: state.pl3 });
    setMsg("");
  }, [state, cancel]);

  return (
    <PanelSection title={state?.active ? `Power limits · ${state.active}` : "Power limits"}>
      <PanelSectionRow>
        <ToggleField
          label="Apply power limits"
          description={
            haveProfile
              ? "Saved to the active profile. Off hands the limits back to the firmware."
              : "Profile store unavailable"
          }
          checked={enabled}
          disabled={!haveProfile}
          onChange={async (on: boolean) => {
            cancel(); // the toggle writes the current values itself
            setEnabled(on);
            const res = await push(on, railsRef.current);
            if (res.saved === false) setEnabled(!on); // nothing changed: revert
          }}
        />
      </PanelSectionRow>
      {RAILS.map(({ key, label, description }) => (
        <PanelSectionRow key={key}>
          <div {...handlers}>
            <SliderField
              label={label}
              description={
                key === "pl1" && rails.pl1 > safeMax
                  ? `${description} · above ${safeMax} W runs with --force`
                  : description
              }
              value={rails[key]}
              min={min}
              max={max}
              step={1}
              showValue
              valueSuffix=" W"
              disabled={!enabled || !haveProfile}
              onChange={(v: number) => {
                const next = clampTdp({ ...railsRef.current, [key]: v }, min, max);
                setRails(next);
                schedule(next);
              }}
            />
          </div>
        </PanelSectionRow>
      ))}
      {msg && (
        <PanelSectionRow>
          <div style={{ fontSize: "12px", opacity: 0.8 }}>{msg}</div>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
