import { ButtonItem, ConfirmModal, Field, PanelSection, PanelSectionRow, showModal } from "@decky/ui";
import { useEffect, useState } from "react";
import { getTimeline, rollbackTimelineEntry, TimelineEntry } from "../backend";
import { toaster } from "@decky/api";

/** A compact newest-first recent-change list for the QAM. */
export function Timeline() {
  const [entries, setEntries] = useState<TimelineEntry[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const load = () => getTimeline(5).then((rows) => setEntries(rows ?? []))
    .catch(() => setError("Timeline unavailable"));
  useEffect(() => {
    let active = true;
    getTimeline(5).then((rows) => { if (active) setEntries(rows ?? []); })
      .catch(() => { if (active) setError("Timeline unavailable"); });
    return () => { active = false; };
  }, []);
  return (
    <PanelSection title="Recent changes">
      {error || message ? <PanelSectionRow><Field focusable={false}>{error || message}</Field></PanelSectionRow> : null}
      {!entries.length && !error ? <PanelSectionRow><Field focusable={false}>No recorded changes</Field></PanelSectionRow> : null}
      {entries.map((entry, i) => (
        <PanelSectionRow key={`${entry.ts ?? ""}-${i}`}>
          <Field focusable={false} padding="compact" label={`${entry.kind ?? "change"} · ${entry.ts ?? ""}`}>
            {entry.summary ?? entry.changed?.join(", ") ?? "Recorded change"}
          </Field>
          {!entry.rollback_supported ? (
            <Field focusable={false} padding="compact" label="Rollback unavailable">
              {entry.no_rollback_reason ?? (entry.rollback ? "This rollback record format is unsupported." : "No rollback record was saved.")}
            </Field>
          ) : null}
          {entry.rollback_supported && entry.id ? (
            <ButtonItem layout="below" onClick={() => showModal(
              <ConfirmModal strTitle="Roll back change?" strDescription={entry.summary ?? entry.kind ?? "Apply the saved prior state."}
                strOKButtonText="Roll back" onOK={() => rollbackTimelineEntry(entry.id!).then((r) => {
                  setMessage(r.message);
                  if (!r.ok) toaster.toast({ title: "Timeline rollback", body: r.message });
                  return load();
                }).catch(() => setMessage("Rollback failed."))} />,
            )}>
              Roll back…
            </ButtonItem>
          ) : null}
        </PanelSectionRow>
      ))}
    </PanelSection>
  );
}
