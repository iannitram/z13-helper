import { ButtonItem, ConfirmModal, Field, PanelSectionRow, showModal } from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { getPostUpdateStatus, getRepairPlan, PostUpdateStatus, RepairPlanInfo, runRepair } from "../backend";

/** What the confirm box shows: each step, its exact commands, whether it needs root, how to undo it. */
function planText(plan: RepairPlanInfo): string {
  return plan.lines.join("\n");
}

/** After SteamOS update: the banner, the attribute check and the tasks that need attention.
 *  "Repair" shows the plan (exact commands, root steps, undo notes) and runs it only after you
 *  confirm; run the recipe tasks with ./install.sh --post-update (SSH or Desktop Mode). */
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

  const repair = useCallback(() => {
    getRepairPlan()
      .then((r) => {
        if (!r.plan || r.plan.steps.length === 0) {
          setMessage(r.plan ? "Nothing to repair." : r.message);
          return;
        }
        const ids = r.plan.steps.map((s) => s.id);
        showModal(
          <ConfirmModal
            strTitle="Repair plan"
            strDescription={planText(r.plan)}
            strOKButtonText="Run"
            onOK={() =>
              runRepair(ids)
                .then((x) =>
                  setMessage(
                    x.result ? x.result.results.map(([i, s]) => `${i}: ${s}`).join("; ") : x.message,
                  ),
                )
                .then(() => load(false))
                .catch(() => setMessage("The repair did not report back."))
            }
          />,
        );
      })
      .catch(() => setMessage("Could not build the repair plan."));
  }, [load]);

  const attention = (status?.rows ?? []).filter(
    (r) => r.state === "needs" || r.state === "error" || r.state === "unknown",
  );
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
            {(r.state === "error" || r.state === "unknown") && r.detail ? r.detail : r.fixes}
          </Field>
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={repair}>
          Repair…
        </ButtonItem>
      </PanelSectionRow>
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
