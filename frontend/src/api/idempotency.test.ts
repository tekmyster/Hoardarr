import { describe, expect, it, vi } from "vitest";
import { createIdempotencyKey } from "./idempotency";

describe("createIdempotencyKey", () => {
  it("uses the browser UUID implementation when it is available", () => {
    const randomUUID = vi.fn(() => "11111111-2222-4333-8444-555555555555" as `${string}-${string}-${string}-${string}-${string}`);
    const source = { randomUUID } as unknown as Crypto;

    expect(createIdempotencyKey(source)).toBe("11111111-2222-4333-8444-555555555555");
    expect(randomUUID).toHaveBeenCalledTimes(1);
  });

  it("creates an RFC 4122 version 4 UUID when randomUUID is unavailable", () => {
    const source = {
      getRandomValues: (target: Uint8Array) => {
        target.set(Array.from({ length: 16 }, (_value, index) => index));
        return target;
      },
    } as unknown as Crypto;

    expect(createIdempotencyKey(source)).toBe("00010203-0405-4607-8809-0a0b0c0d0e0f");
  });

  it("falls back to a safe unique request key when Web Crypto is absent", () => {
    const first = createIdempotencyKey(null);
    const second = createIdempotencyKey(null);

    expect(first).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/);
    expect(second).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/);
    expect(second).not.toBe(first);
  });
});
