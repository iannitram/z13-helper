import {
  PanelSection,
  PanelSectionRow,
  ToggleField,
  DropdownItem,
  staticClasses,
} from "@decky/ui";
import { addEventListener, removeEventListener, definePlugin, toaster } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import { FaFan } from "react-icons/fa";
import {
  getMaxFans,
  setMaxFans,
  getProfile,
  setProfile,
  getProfiles,
  setAppProfile,
  getPowerState,
  PowerState,
  isWriteTimeout,
} from "./backend";
import { cancelPendingCommits } from "./hooks";
import { switchProfile } from "./profileSwitch";
import { clearAllDirty, dirtySet, mergePowerState, pendingEdits } from "./powerMerge";
import { TelemetryRow } from "./components/Telemetry";
import { PowerLimits } from "./components/PowerLimits";
import { BatteryLimit } from "./components/BatteryLimit";
import { KeyboardBrightness } from "./components/KeyboardBrightness";
import { ComputeHealth } from "./components/ComputeHealth";
import { PostUpdate } from "./components/PostUpdate";
import { Timeline } from "./components/Timeline";

const PLATFORM_PROFILES = [
  { data: "quiet", label: "Quiet" },
  { data: "balanced", label: "Balanced" },
  { data: "performance", label: "Performance" },
];

function Content() {
  const [maxFans, setMaxFansState] = useState<boolean>(false);
  const [profile, setProfileState] = useState<string>("balanced");
  const [options, setOptions] = useState<{ data: string; label: string }[]>(PLATFORM_PROFILES);
  const [appProfiles, setAppProfiles] = useState<boolean>(false);
  // The active profile's TDP / battery limit (+ last brightness). Re-read after a profile switch.
  const [power, setPower] = useState<PowerState | null>(null);

  // The profile_state listener below owns every write to `power`; this lets the dropdown request a refresh.
  const refreshRef = useRef<() => Promise<void>>(async () => {});

  useEffect(() => {
    let disposed = false;
    let sequence = 0;
    let lastActive: string | null = null; // active profile this effect last applied
    const refresh = async () => {
      const current = ++sequence;
      try {
        const [r, fans, nextPower] = await Promise.all([getProfiles(), getMaxFans(), getPowerState()]);
        if (disposed || current !== sequence) return;
        let active: string;
        if (r && r.names && r.names.length) {
          setOptions(r.names.map((n) => ({ data: n, label: n })));
          active = r.active;
          setAppProfiles(true);
        } else {
          active = await getProfile();
          if (disposed || current !== sequence) return;
        }
        setProfileState(active);
        setMaxFansState(fans);
        // A profile switch drops every pending edit. Any other event refreshes all fields except
        // those the user has edited and not yet committed.
        const changed = active !== lastActive;
        lastActive = active;
        if (changed) {
          cancelPendingCommits();
          clearAllDirty();
        }
        const dirty = dirtySet();
        const pending = pendingEdits();
        setPower((previous) =>
          mergePowerState<PowerState>({ previous, pending, dirty, incoming: nextPower, changed }).render,
        );
      } catch {
        // keep the previous state so pending edits stay protected by the next merge
      }
    };
    refreshRef.current = refresh;
    const listener = addEventListener("profile_state", refresh);
    void refresh();
    return () => {
      disposed = true;
      removeEventListener("profile_state", listener);
    };
  }, []);

  return (
    <>
      <PanelSection title="Z13-Helper">
        <TelemetryRow />
        <PanelSectionRow>
          <ToggleField
            label="Max Fans"
            description="Force both fans to 100%. Off restores your custom fan curve."
            checked={maxFans}
            onChange={async (v: boolean) => {
              setMaxFansState(v);
              const result = await setMaxFans(v);
              if (!result.ok) {
                setMaxFansState(!v); // revert on failure
                if (isWriteTimeout(result.message)) toaster.toast({ title: "Z13-Helper", body: result.message });
              }
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label="Profile"
            rgOptions={options}
            selectedOption={profile}
            onChange={async (o) => {
              const name = o.data as string;
              cancelPendingCommits(); // a slider edit must not land on the profile we switch to
              clearAllDirty();
              const previous = profile;
              setProfileState(name);
              const r = await switchProfile(
                {
                  apply: appProfiles ? setAppProfile : setProfile,
                  readProfile: async () =>
                    appProfiles ? (await getProfiles()).active : await getProfile(),
                  readMaxFans: getMaxFans,
                },
                name,
              );
              // A false result can still mean the profile is active (one step failed): show what
              // the backend reports; revert only if that re-read failed.
              if (r.profile) setProfileState(r.profile);
              else if (!r.ok) setProfileState(previous);
              if (r.maxFans !== undefined) setMaxFansState(r.maxFans);
              if (!r.ok) {
                toaster.toast({
                  title: "Z13-Helper",
                  body: r.message && isWriteTimeout(r.message)
                    ? r.message
                    : r.profile === name
                      ? `${name} is active, but some settings did not apply`
                      : `Could not switch to ${name}`,
                });
              }
              void refreshRef.current(); // TDP / battery limit belong to the (new) active profile
            }}
          />
        </PanelSectionRow>
      </PanelSection>
      <PowerLimits state={power} />
      <PanelSection title="Battery">
        <BatteryLimit state={power} />
      </PanelSection>
      <PanelSection title="Keyboard brightness">
        <KeyboardBrightness state={power} />
      </PanelSection>
      <ComputeHealth />
      <PanelSection title="After SteamOS update">
        <PostUpdate />
      </PanelSection>
      <Timeline />
    </>
  );
}

export default definePlugin(() => ({
  name: "Z13-Helper",
  titleView: (
    <div className={staticClasses?.Title ?? ""}>Z13-Helper</div>
  ),
  content: <Content />,
  icon: <FaFan />,
  onDismount() {},
}));
