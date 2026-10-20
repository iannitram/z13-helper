// Run: node --test decky/tests/profileSwitch.test.ts   (Node >= 22.18 strips types natively)
import { test } from "node:test";
import assert from "node:assert/strict";
import { switchProfile } from "../src/profileSwitch.ts";

function deps(applyResult: boolean | Error) {
  const calls: string[] = [];
  return {
    calls,
    d: {
      apply: async (n: string) => {
        calls.push(`apply:${n}`);
        if (applyResult instanceof Error) throw applyResult;
        return applyResult;
      },
      readProfile: async () => {
        calls.push("readProfile");
        return "Turbo";
      },
      readMaxFans: async () => {
        calls.push("readMaxFans");
        return true;
      },
    },
  };
}

test("false result, backend reports the new name active: state re-read, ok false", async () => {
  const { d, calls } = deps(false);
  const r = await switchProfile(d, "Turbo");
  assert.deepEqual(r, { ok: false, profile: "Turbo", maxFans: true });
  assert.ok(calls.includes("readProfile") && calls.includes("readMaxFans"));
});

test("false result, backend reports the old name: old name returned", async () => {
  const { d } = deps(false);
  d.readProfile = async () => "Balanced";
  const r = await switchProfile(d, "Turbo");
  assert.equal(r.ok, false);
  assert.equal(r.profile, "Balanced");
});

test("rejected promise and failed re-read: not thrown, no state (caller reverts)", async () => {
  const { d } = deps(new Error("backend gone"));
  d.readProfile = async () => {
    throw new Error("x");
  };
  const r = await switchProfile(d, "Turbo");
  assert.equal(r.ok, false);
  assert.equal(r.profile, undefined);
});

test("success: active profile and Max Fans are re-read from the backend", async () => {
  const { d, calls } = deps(true);
  const r = await switchProfile(d, "Turbo");
  assert.deepEqual(r, { ok: true, profile: "Turbo", maxFans: true });
  assert.ok(calls.includes("readMaxFans") && calls.includes("readProfile"));
});

test("success but refresh read fails: still ok, state fields absent", async () => {
  const { d } = deps(true);
  d.readMaxFans = async () => {
    throw new Error("x");
  };
  const r = await switchProfile(d, "Turbo");
  assert.equal(r.ok, true);
  assert.equal(r.maxFans, undefined);
});
