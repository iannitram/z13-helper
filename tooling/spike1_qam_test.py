#!/usr/bin/env python3
"""Spike 1 — validate the Game-Mode QAM trigger.

>>> RUN THIS IN GAME MODE, WITH A GAME LAUNCHED AND FOCUSED. <<<

We can't switch to Game Mode from the desktop session (it would tear the desktop
session down), so this is the one piece only you can run. It checks:

  1. The QAM opens via the pure-JS Navigation call (you confirm visually).
  2. It takes input focus over the running game  — the one unproven risk
     (GetFocusedWindowInstance() becomes non-null).
  3. No new controller/HID device appears — the pollution guardrail that protects
     the deck-uhid / phantom-controller fix.

Usage (from a Game-Mode terminal, or SSH into the Z13 while it's in Game Mode):
    python3 /home/deck/dev/z13-helper/tooling/spike1_qam_test.py
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import z13_qam  # noqa: E402


def input_snapshot():
    """Return (device_count, [suspicious controller/HID lines])."""
    devs = open("/proc/bus/input/devices").read()
    count = len(re.findall(r"(?m)^I: ", devs))
    # Flag anything that looks like a (re)introduced Valve/handheld gamepad, but
    # exclude the known steamos-manager Valve-VID *keyboard* (expected, not a pad).
    suspicious = [
        ln for ln in devs.splitlines()
        if re.search(r"(?i)valve|28de|handheld|uhid|virtual", ln)
        and "steamos-manager" not in ln
    ]
    return count, suspicious


def main():
    print("=== Spike 1: Game-Mode QAM trigger test ===")
    print("Make sure a GAME is running and focused before continuing.\n")

    try:
        before_focus = z13_qam.focused_window_present()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: cannot reach Steam CDP ({e}).")
        print("      Are you in Game Mode with the Steam UI up? Is CEF debugging enabled")
        print("      (~/.steam/steam/.cef-enable-remote-debugging)?")
        return 2

    cnt0, susp0 = input_snapshot()
    print(f"before: focused-window-present={before_focus}  input-devices={cnt0}")

    print("opening QAM  ->  window.DFL.Navigation.OpenQuickAccessMenu() ...")
    z13_qam.open_qam()
    time.sleep(1.2)

    after_focus = z13_qam.focused_window_present()
    cnt1, susp1 = input_snapshot()
    print(f"after:  focused-window-present={after_focus}  input-devices={cnt1}\n")

    ok_focus = after_focus
    new_susp = sorted(set(susp1) - set(susp0))
    ok_pollution = (cnt1 == cnt0) and not new_susp

    print(f"[{'PASS' if ok_focus else 'FAIL'}] QAM took input focus over the game "
          f"(GetFocusedWindowInstance non-null = {after_focus})")
    print(f"[{'PASS' if ok_pollution else 'FAIL'}] No controller-list pollution "
          f"(device count {cnt0}->{cnt1}; new suspicious devices: {new_susp or 'none'})")

    print("\n>>> LOOK AT THE SCREEN: did the Quick Access Menu slide in over the game,")
    print(">>> and does it accept touch / controller input? <<<")
    print("(dismissing it in 3s)")
    time.sleep(3)
    z13_qam.close_side_menus()

    print("\nReport back: (a) did it visibly open? (b) focus PASS/FAIL above? "
          "(c) pollution PASS/FAIL above?")
    return 0 if (ok_focus and ok_pollution) else 1


if __name__ == "__main__":
    sys.exit(main())
