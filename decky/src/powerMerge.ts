// Pure merge of a fresh backend power state into what the panel shows, so that an event that
// does not change the active profile refreshes every field except the ones the user is editing.

/** Slider-backed fields: marked dirty on user change, cleared on commit or profile switch. */
export type EditableField = "battery_limit" | "pl1" | "pl2" | "pl3";

export interface MergeInput<S> {
  previous: S | null;                 // state currently rendered
  pending: Partial<S>;                // the user's uncommitted values (only dirty fields matter)
  dirty: ReadonlySet<EditableField & keyof S>;
  incoming: S;                        // fresh server state
  changed: boolean;                   // the active profile changed
}

export interface MergeResult<S> {
  render: S;                          // state to render
  keep: (EditableField & keyof S)[];  // fields left at the user's value
}

export function mergePowerState<S extends object>(input: MergeInput<S>): MergeResult<S> {
  const { previous, pending, dirty, incoming, changed } = input;
  if (changed || previous === null) return { render: incoming, keep: [] };
  const render: S = { ...incoming };
  const keep: (EditableField & keyof S)[] = [];
  dirty.forEach((f) => {
    const v = f in pending ? pending[f] : previous[f];
    render[f] = v as S[typeof f];
    keep.push(f);
  });
  return { render, keep };
}

// Shared edit tracking (module state, like the slider cancelers in hooks.ts).
const pendingValues = new Map<EditableField, number>();

export function markDirty(field: EditableField, value: number) {
  pendingValues.set(field, value);
}
export function clearDirty(...fields: EditableField[]) {
  fields.forEach((f) => pendingValues.delete(f));
}
export function clearAllDirty() {
  pendingValues.clear();
}
export function isDirty(...fields: EditableField[]): boolean {
  return fields.some((f) => pendingValues.has(f));
}
export function dirtySet(): Set<EditableField> {
  return new Set(pendingValues.keys());
}
export function pendingEdits(): Partial<Record<EditableField, number>> {
  return Object.fromEntries(pendingValues);
}
