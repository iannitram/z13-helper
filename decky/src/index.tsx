import {
  PanelSection,
  PanelSectionRow,
  ToggleField,
  DropdownItem,
  staticClasses,
} from "@decky/ui";
import { definePlugin, toaster } from "@decky/api";
import { useCallback, useEffect, useState } from "react";
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
} from "./backend";
import { cancelPendingCommits } from "./hooks";
import { switchProfile } from "./profileSwitch";
import { TelemetryRow } from "./components/Telemetry";
import { PowerLimits } from "./components/PowerLimits";
import { BatteryLimit } from "./components/BatteryLimit";
import { KeyboardBrightness } from "./components/KeyboardBrightness";

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

  const loadPower = useCallback(() => {
    getPowerState()
      .then(setPower)
      .catch(() => setPower(null));
  }, []);

  useEffect(() => {
    getMaxFans().then(setMaxFansState);
    getProfiles().then((r) => {
      if (r && r.names && r.names.length) {
        setOptions(r.names.map((n) => ({ data: n, label: n })));
        setProfileState(r.active);
        setAppProfiles(true);
      } else {
        getProfile().then(setProfileState);
      }
    });
    loadPower();
  }, [loadPower]);

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
              if (!(await setMaxFans(v))) setMaxFansState(!v); // revert on failure
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
                  body:
                    r.profile === name
                      ? `${name} is active, but some settings did not apply`
                      : `Could not switch to ${name}`,
                });
              }
              loadPower(); // TDP / battery limit belong to the (new) active profile
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
    </>
  );
}

export default definePlugin(() => ({
  name: "Z13-Helper",
  titleView: <div className={staticClasses.Title}>Z13-Helper</div>,
  content: <Content />,
  icon: <FaFan />,
  onDismount() {},
}));
