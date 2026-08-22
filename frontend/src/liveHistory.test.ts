import { describe, expect, it } from "vitest";
import { appendBounded } from "./liveHistory";

describe("appendBounded", () => {
  it("never grows beyond its configured sample count", () => {
    let values: number[] = [];
    for (let index = 0; index < 10_000; index += 1) values = appendBounded(values, index, 60);
    expect(values).toHaveLength(60);
    expect(values[0]).toBe(9_940);
    expect(values.at(-1)).toBe(9_999);
  });

  it("rejects an unbounded configuration", () => {
    expect(() => appendBounded([], 1, 0)).toThrow(RangeError);
  });
});
