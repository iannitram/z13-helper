import { Field, PanelSectionRow } from "@decky/ui";
import { useState } from "react";
import { getTelemetry, Telemetry as T } from "../backend";
import { usePollWhileVisible } from "../hooks";

const POLL_MS = 2000;

const temp = (v: number | null) => (v == null ? "–" : `${Math.round(v)}°C`);
const rpm = (v: number | null) => (v == null ? "–" : `${v}`);

/** APU / GPU temperature and both fan RPMs; polled every 2 s only while the QAM is open. */
export function TelemetryRow() {
  const [t, setT] = useState<T | null>(null);
  usePollWhileVisible(() => {
    getTelemetry()
      .then(setT)
      .catch(() => setT(null));
  }, POLL_MS);

  return (
    <>
      <PanelSectionRow>
        <Field label="Temps" focusable={false} padding="compact" childrenLayout="inline">
          APU {temp(t?.apu ?? null)} · GPU {temp(t?.gpu ?? null)}
        </Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="Fans" focusable={false} padding="compact" childrenLayout="inline">
          {rpm(t?.fan1 ?? null)} · {rpm(t?.fan2 ?? null)} RPM
        </Field>
      </PanelSectionRow>
    </>
  );
}
