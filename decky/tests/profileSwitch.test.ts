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

test("false result: no state refresh, reports failure so the caller reverts", async () => {
  const { d, calls } = deps(false);
  const r = await switchProfile(d, "Turbo");
  assert.equal(r.ok, false);
  assert.deepEqual(calls, ["apply:Turbo"]);
});

test("rejected promise: treated as failure, not thrown", async () => {
  const { d } = deps(new Error("backend gone"));
  const r = await switchProfile(d, "Turbo");
  assert.equal(r.ok, false);
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
