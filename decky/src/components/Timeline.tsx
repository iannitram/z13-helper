import { Field, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";
import { getTimeline, TimelineEntry } from "../backend";

const SHOWN = 5;

/** The newest timeline entries, newest first. An entry that can't be undone says why;
 *  compare and roll back live in the desktop app's Timeline window. */
export function TimelineList() {
  const [entries, setEntries] = useState<TimelineEntry[] | null>(null);
  useEffect(() => {
    getTimeline(SHOWN)
      .then(setEntries)
      .catch(() => setEntries([]));
  }, []);

  if (!entries || entries.length === 0) {
    return (
      <PanelSectionRow>
        <Field focusable={false} padding="compact">
          No changes recorded yet
        </Field>
      </PanelSectionRow>
    );
  }
  return (
    <>
      {entries.map((e) => (
        <PanelSectionRow key={e.id}>
          <Field
            label={e.summary + (e.ok ? "" : " (failed)")}
            description={
              e.rollback ? "Can be rolled back in the desktop app" : `Can't be undone: ${e.no_rollback_reason ?? "not reversible"}`
            }
            focusable={false}
            padding="compact"
          />
        </PanelSectionRow>
      ))}
    </>
  );
}
