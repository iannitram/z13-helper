// Run: node --test decky/tests/powerMerge.test.ts   (Node >= 22.18 strips types natively)
import { test } from "node:test";
import assert from "node:assert/strict";
import { mergePowerState } from "../src/powerMerge.ts";

type S = { active: string; battery_limit: number; pl1: number; pl2: number; pl3: number; brightness: string };
const prev: S = { active: "A", battery_limit: 80, pl1: 30, pl2: 40, pl3: 50, brightness: "low" };
const incoming: S = { active: "A", battery_limit: 60, pl1: 35, pl2: 45, pl3: 55, brightness: "high" };

test("same profile, nothing dirty: every field follows the server", () => {
  const r = mergePowerState<S>({ previous: prev, pending: {}, dirty: new Set(), incoming, changed: false });
  assert.deepEqual(r.render, incoming);
  assert.deepEqual(r.keep, []);
});

test("same profile: dirty fields keep the pending value, the rest update", () => {
  const r = mergePowerState<S>({
    previous: prev,
    pending: { battery_limit: 90 },
    dirty: new Set(["battery_limit"]),
    incoming,
    changed: false,
  });
  assert.deepEqual(r.render, { ...incoming, battery_limit: 90 });
  assert.deepEqual(r.keep, ["battery_limit"]);
});

test("dirty field without a pending value falls back to the previous value", () => {
  const r = mergePowerState<S>({ previous: prev, pending: {}, dirty: new Set(["pl1"]), incoming, changed: false });
  assert.equal(r.render.pl1, 30);
  assert.equal(r.render.pl2, 45);
});

test("profile changed: server state wins, dirty ignored", () => {
  const r = mergePowerState<S>({
    previous: prev,
    pending: { pl1: 70 },
    dirty: new Set(["pl1"]),
    incoming: { ...incoming, active: "B" },
    changed: true,
  });
  assert.equal(r.render.pl1, 35);
  assert.equal(r.render.active, "B");
  assert.deepEqual(r.keep, []);
});

test("no previous state: server state", () => {
  const r = mergePowerState<S>({ previous: null, pending: {}, dirty: new Set(["pl1"]), incoming, changed: false });
  assert.deepEqual(r.render, incoming);
});

test("inputs are not mutated", () => {
  const inc = { ...incoming };
  mergePowerState<S>({ previous: prev, pending: { pl1: 1 }, dirty: new Set(["pl1"]), incoming: inc, changed: false });
  assert.deepEqual(inc, incoming);
});
