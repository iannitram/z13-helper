import { Field, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";
import { getTimeline, TimelineEntry } from "../backend";

/** A compact newest-first recent-change list for the QAM. */
export function Timeline() {
  const [entries, setEntries] = useState<TimelineEntry[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    getTimeline(5).then((rows) => { if (active) setEntries(rows ?? []); })
      .catch(() => { if (active) setError("Timeline unavailable"); });
    return () => { active = false; };
  }, []);
  return (
    <PanelSection title="Recent changes">
      {error ? <PanelSectionRow><Field focusable={false}>{error}</Field></PanelSectionRow> : null}
      {!entries.length && !error ? <PanelSectionRow><Field focusable={false}>No recorded changes</Field></PanelSectionRow> : null}
      {entries.map((entry, i) => (
        <PanelSectionRow key={`${entry.ts ?? ""}-${i}`}>
          <Field focusable={false} padding="compact" label={`${entry.kind ?? "change"} · ${entry.ts ?? ""}`}>
            {entry.summary ?? entry.changed?.join(", ") ?? "Recorded change"}
          </Field>
        </PanelSectionRow>
      ))}
    </PanelSection>
  );
}
