import { useCallback, useEffect, useRef } from "react";
import { useQuickAccessVisible } from "@decky/api";

const RELEASE_EVENTS = ["pointerup", "pointercancel", "touchend", "touchcancel", "mouseup"];

/**
 * Commit a slider value "on release", not on every drag tick.
 *
 * Decky's SliderField has no release event, so a value is committed when:
 *  - the pointer/touch that was dragging it is lifted (wrap the slider in `handlers`), or
 *  - it hasn't changed for `settleMs` while no pointer is down (D-pad / stick steps), or
 *  - the panel unmounts with a value still pending (QAM closed mid-adjust).
 */
export function useCommitOnRelease<T>(commit: (v: T) => void, settleMs = 700) {
  const commitRef = useRef(commit);
  commitRef.current = commit;
  const pending = useRef<{ v: T } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const pointerDown = useRef(false);

  const clear = () => {
    if (timer.current !== undefined) {
      window.clearTimeout(timer.current);
      timer.current = undefined;
    }
  };

  const flush = useCallback(() => {
    clear();
    const p = pending.current;
    pending.current = null;
    if (p) commitRef.current(p.v);
  }, []);

  const schedule = useCallback(
    (v: T) => {
      pending.current = { v };
      clear();
      timer.current = window.setTimeout(() => {
        timer.current = undefined;
        if (!pointerDown.current) flush(); // still dragging: the pointer-up flushes
      }, settleMs);
    },
    [flush, settleMs],
  );

  // The release is caught on window, so it counts even if the finger slid off the slider.
  const up = useCallback(() => {
    RELEASE_EVENTS.forEach((ev) => window.removeEventListener(ev, up, true));
    pointerDown.current = false;
    flush();
  }, [flush]);
  const down = useCallback(() => {
    if (pointerDown.current) return;
    pointerDown.current = true;
    RELEASE_EVENTS.forEach((ev) => window.addEventListener(ev, up, true));
  }, [up]);

  useEffect(
    () => () => {
      RELEASE_EVENTS.forEach((ev) => window.removeEventListener(ev, up, true));
      pointerDown.current = false;
      flush(); // flush on unmount
    },
    [flush, up],
  );

  /** Drop a pending value without committing it. */
  const cancel = useCallback(() => {
    clear();
    pending.current = null;
  }, []);

  return {
    schedule,
    flush,
    cancel,
    // Spread onto a wrapper element around the slider.
    handlers: { onPointerDown: down, onTouchStart: down, onMouseDown: down },
  };
}

/** Call `fn` now and every `ms` while the Quick Access Menu is visible and we're mounted. */
// The loader injects this hook; if an older loader lacks it, fall back to "visible while mounted".
const useVisible: () => boolean =
  typeof useQuickAccessVisible === "function" ? useQuickAccessVisible : () => true;

export function usePollWhileVisible(fn: () => void, ms: number) {
  const visible = useVisible();
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    if (!visible) return;
    fnRef.current();
    const id = window.setInterval(() => fnRef.current(), ms);
    return () => window.clearInterval(id);
  }, [visible, ms]);
}
