import { ButtonItem, Field, PanelSection, PanelSectionRow } from "@decky/ui";
import { useState } from "react";
import { ComputeHealth as H, getComputeHealth, runComputeSmoke } from "../backend";

const gib = (v: number | null) => (v == null ? "unavailable" : `${(v / 1024 ** 3).toFixed(1)} GiB`);

/** Memory breakdown and compute runtime status. Read on request; the allocation test
 *  runs only when its button is pressed (budget stated below the button). */
export function ComputeHealth() {
  const [h, setH] = useState<H | null>(null);
  const [busy, setBusy] = useState(false);
  const [smoke, setSmoke] = useState("");

  const load = () => {
    setBusy(true);
    getComputeHealth()
      .then(setH)
      .catch(() => setH(null))
      .finally(() => setBusy(false));
  };
  const runSmoke = () => {
    setBusy(true);
    setSmoke("Running…");
    runComputeSmoke()
      .then((r) => setSmoke(`${r.ok ? "Passed" : "Failed"}: ${r.detail}`))
      .catch(() => setSmoke("Failed: the backend did not answer"))
      .finally(() => setBusy(false));
  };
  const m = h?.memory;
  const rt = h?.runtime;

  return (
    <PanelSection title="Memory and compute">
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={load}>
          {h ? "Refresh" : "Check memory and compute"}
        </ButtonItem>
      </PanelSectionRow>
      {m && rt && h && (
        <>
          <PanelSectionRow>
            <Field label="Linux RAM" focusable={false} padding="compact" childrenLayout="inline">
              {gib(m.ram_available)} free of {gib(m.ram_total)}
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="VRAM (reserved)" focusable={false} padding="compact" childrenLayout="inline">
              {gib(m.vram_used)} of {gib(m.vram_reserved)}
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="GTT (inside RAM)" focusable={false} padding="compact" childrenLayout="inline">
              {gib(m.gtt_used)} of up to {gib(m.gtt_limit)}
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="GPU" focusable={false} padding="compact" childrenLayout="inline">
              {h.gpu_agents.length ? h.gpu_agents.map((a) => a.gfx).join(", ") : "no GPU agent"}
              {h.gfx1151 ? " (gfx1151)" : ""}
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="ROCm/HIP" focusable={false} padding="compact" childrenLayout="inline">
              {rt.ok ? (rt.hip_libs.length ? `found${rt.rocm_version ? ` ${rt.rocm_version}` : ""}` : "not found") : "unavailable"}
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Vulkan" focusable={false} padding="compact" childrenLayout="inline">
              {rt.ok ? (rt.vulkan_icds.length ? "AMD driver found" : "no AMD driver file") : "unavailable"}
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={runSmoke} description={h.smoke_budget}>
              Run allocation test
            </ButtonItem>
          </PanelSectionRow>
          {smoke && (
            <PanelSectionRow>
              <Field focusable={false} padding="compact">{smoke}</Field>
            </PanelSectionRow>
          )}
        </>
      )}
    </PanelSection>
  );
}
