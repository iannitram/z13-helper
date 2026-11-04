import { ButtonItem, Field, PanelSectionRow } from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { getPostUpdateStatus, PostUpdateStatus } from "../backend";

/** After SteamOS update: the banner, the attribute check and the tasks that need attention.
 *  Checks only; run the tasks with ./install.sh --post-update (SSH or Desktop Mode). */
export function PostUpdate() {
  const [status, setStatus] = useState<PostUpdateStatus | null>(null);
  const [message, setMessage] = useState<string>("");

  const load = useCallback((ack: boolean) => {
    getPostUpdateStatus(ack)
      .then((r) => {
        setStatus(r.status);
        setMessage(r.message);
      })
      .catch(() => setMessage("Could not read the post-update status."));
  }, []);

  useEffect(() => {
    load(false);
  }, [load]);

  const attention = (status?.rows ?? []).filter((r) => r.state === "needs" || r.state === "error");
  const missing = status?.armoury && status.armoury.state === "needs" ? status.armoury.missing : [];
  return (
    <>
      <PanelSectionRow>
        <Field focusable={false} padding="compact" label={status?.banner ?? "No SteamOS update seen"}>
          {message || `${attention.length} need attention`}
        </Field>
      </PanelSectionRow>
      {missing.length > 0 && (
        <PanelSectionRow>
          <Field focusable={false} padding="compact" label="asus-armoury missing">
            {missing.join(", ")}
          </Field>
        </PanelSectionRow>
      )}
      {attention.map((r) => (
        <PanelSectionRow key={r.id || r.title}>
          <Field focusable={false} padding="compact" label={r.title}>
            {r.state === "error" ? r.detail : r.fixes}
          </Field>
        </PanelSectionRow>
      ))}
      {status?.updated && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => load(true)}>
            I re-verified
          </ButtonItem>
        </PanelSectionRow>
      )}
    </>
  );
}
