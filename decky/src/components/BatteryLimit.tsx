import { PanelSectionRow, SliderField } from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import { isWriteTimeout, PowerState, safely, setBatteryLimit } from "../backend";
import { useCommitOnRelease } from "../hooks";

/** Battery charge limit 40..100 % in steps of 5, applied on release and saved to the active profile. */
export function BatteryLimit({ state }: { state: PowerState | null }) {
  const [value, setValueState] = useState(80);
  const valueRef = useRef(value);
  const setValue = (v: number) => {
    valueRef.current = v;
    setValueState(v);
  };
  const [msg, setMsg] = useState("");

  const { schedule, cancel, handlers } = useCommitOnRelease<number>(async (v) => {
    setMsg("Applying…");
    const res = await safely(setBatteryLimit(v, state?.active ?? ""));
    setMsg(res.message);
    if (isWriteTimeout(res.message)) toaster.toast({ title: "Z13-Helper", body: res.message });
  });

  useEffect(() => {
    if (!state) return;
    cancel();
    setValue(state.battery_limit);
    setMsg("");
  }, [state, cancel]);

  return (
    <>
      <PanelSectionRow>
        <div {...handlers}>
          <SliderField
            label="Charge limit"
            description={state?.active ? `Saved to ${state.active}` : undefined}
            value={value}
            min={40}
            max={100}
            step={5}
            notchCount={7}
            notchTicksVisible
            showValue
            valueSuffix="%"
            onChange={(v: number) => {
              setValue(v);
              schedule(v);
            }}
          />
        </div>
      </PanelSectionRow>
      {msg && (
        <PanelSectionRow>
          <div style={{ fontSize: "12px", opacity: 0.8 }}>{msg}</div>
        </PanelSectionRow>
      )}
    </>
  );
}
