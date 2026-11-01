import { DialogButton, Focusable, PanelSectionRow } from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { BrightnessLevel, isWriteTimeout, PowerState, setBrightness } from "../backend";

const LEVELS: { level: BrightnessLevel; label: string }[] = [
  { level: "off", label: "Off" },
  { level: "low", label: "Low" },
  { level: "medium", label: "Med" },
  { level: "high", label: "High" },
];

/**
 * Off / Low / Medium / High. The backend writes the keyboard and the lightbar with
 * separate z13ctl calls, so a wedged lightbar is reported here without failing the keyboard.
 */
export function KeyboardBrightness({ state }: { state: PowerState | null }) {
  const [current, setCurrent] = useState<BrightnessLevel | "">("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [warn, setWarn] = useState(false);

  useEffect(() => {
    if (state) setCurrent(state.brightness);
  }, [state]);

  const pick = async (level: BrightnessLevel) => {
    setBusy(true);
    setMsg("Applying…");
    setWarn(false);
    try {
      const res = await setBrightness(level);
      if (res.ok) setCurrent(level);
      setMsg(res.message);
      setWarn(!res.ok || res.failed.length > 0);
      if (isWriteTimeout(res.message)) toaster.toast({ title: "Z13-Helper", body: res.message });
    } catch (e) {
      setMsg(`Backend error: ${String(e)}`);
      setWarn(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PanelSectionRow>
        <Focusable style={{ display: "flex", gap: "6px" }} flow-children="horizontal">
          {LEVELS.map(({ level, label }) => (
            <DialogButton
              key={level}
              disabled={busy}
              onClick={() => void pick(level)}
              style={{
                flex: 1,
                minWidth: 0,
                padding: "8px 0",
                ...(current === level
                  ? { background: "#1a9fff", color: "#fff" }
                  : {}),
              }}
            >
              {label}
            </DialogButton>
          ))}
        </Focusable>
      </PanelSectionRow>
      {msg && (
        <PanelSectionRow>
          <div style={{ fontSize: "12px", opacity: 0.85, color: warn ? "#ffb02e" : undefined }}>
            {msg}
          </div>
        </PanelSectionRow>
      )}
    </>
  );
}
