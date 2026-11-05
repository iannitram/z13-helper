import { Field, PanelSectionRow } from "@decky/ui";
import { useState } from "react";
import { getTelemetry, Telemetry as T } from "../backend";
import { usePollWhileVisible } from "../hooks";

const POLL_MS = 2000;

const temp = (v: number | null, src?: string | null) =>
  v == null ? "unavailable" : `${Math.round(v)}°C${src ? ` (${src})` : ""}`;
const rpm = (v: number | null) => (v == null ? "unavailable" : `${v}`);

const fans = (fan1: number | null, fan2: number | null) => {
  if (fan1 == null && fan2 == null) return "unavailable";
  if (fan1 == null) return `Fan 1 unavailable · ${rpm(fan2)} RPM`;
  if (fan2 == null) return `${rpm(fan1)} RPM · Fan 2 unavailable`;
  return `${rpm(fan1)} · ${rpm(fan2)} RPM`;
};

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
          {fans(t?.fan1 ?? null, t?.fan2 ?? null)}
        </Field>
      </PanelSectionRow>
    </>
  );
}
