/** Return a bounded live-history window without mutating React state. */
export function appendBounded<T>(current: readonly T[], value: T, maximum: number): T[] {
  if (!Number.isInteger(maximum) || maximum < 1) {
    throw new RangeError("maximum must be a positive integer");
  }
  if (current.length >= maximum) return [...current.slice(current.length - maximum + 1), value];
  return [...current, value];
}
