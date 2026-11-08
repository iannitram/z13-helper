// Run: node --test decky/tests/powerMerge.test.ts   (Node >= 22.18 strips types natively)
import { test } from "node:test";
import assert from "node:assert/strict";
import { clearAllDirty, clearDirtyIf, dirtySet, markDirty, mergePowerState, pendingEdits } from "../src/powerMerge.ts";

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

test("commit A returns ok after a newer edit B: B stays dirty", () => {
  clearAllDirty();
  markDirty("battery_limit", 70); // A, committed
  markDirty("battery_limit", 90); // B, dragged while A is in flight
  clearDirtyIf({ battery_limit: 70 });
  assert.deepEqual(dirtySet(), new Set(["battery_limit"]));
  assert.equal(pendingEdits().battery_limit, 90);
  clearAllDirty();
});

test("commit clears dirty when the stored value is the committed one", () => {
  clearAllDirty();
  markDirty("battery_limit", 70);
  clearDirtyIf({ battery_limit: 70 });
  assert.equal(dirtySet().size, 0);
});

test("power rails: any rail edited since the commit keeps all three dirty", () => {
  clearAllDirty();
  markDirty("pl1", 30);
  markDirty("pl2", 40);
  markDirty("pl3", 50);
  markDirty("pl2", 45); // newer edit
  clearDirtyIf({ pl1: 30, pl2: 40, pl3: 50 });
  assert.equal(dirtySet().size, 3);
  clearDirtyIf({ pl1: 30, pl2: 45, pl3: 50 });
  assert.equal(dirtySet().size, 0);
});

test("failed (null) previous state with dirty fields still keeps the pending values", () => {
  const r = mergePowerState<S>({
    previous: null,
    pending: { pl1: 70, battery_limit: 90 },
    dirty: new Set(["pl1", "battery_limit"]),
    incoming,
    changed: false,
  });
  assert.equal(r.render.pl1, 70);
  assert.equal(r.render.battery_limit, 90);
  assert.equal(r.render.pl2, 45);
});

test("null previous and nothing dirty: server state", () => {
  const r = mergePowerState<S>({ previous: null, pending: {}, dirty: new Set(), incoming, changed: false });
  assert.deepEqual(r.render, incoming);
});
