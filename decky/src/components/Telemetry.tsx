import { Field, PanelSectionRow } from "@decky/ui";
import { useState } from "react";
import { getTelemetry, Telemetry as T } from "../backend";
import { usePollWhileVisible } from "../hooks";

const POLL_MS = 2000;

const temp = (v: number | null, src?: string | null) =>
  v == null ? "unavailable" : `${Math.round(v)}°C${src ? ` (${src})` : ""}`;
const rpm = (v: number | null) => (v == null ? "unavailable" : `${v}`);

/** CPU / GPU temperature (each with its sensor) and both fan RPMs; polled every 2 s only while the QAM is open. */
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
        <Field label="CPU" focusable={false} padding="compact" childrenLayout="inline">
          {temp(t?.apu ?? null, t?.apu_src)}
        </Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="GPU" focusable={false} padding="compact" childrenLayout="inline">
          {temp(t?.gpu ?? null, t?.gpu_src)}
        </Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="Fans" focusable={false} padding="compact" childrenLayout="inline">
          {t?.fan1 == null && t?.fan2 == null
            ? "unavailable"
            : `${rpm(t?.fan1 ?? null)} · ${rpm(t?.fan2 ?? null)} RPM`}
        </Field>
      </PanelSectionRow>
    </>
  );
}
