let fallbackSequence = 0;

function formatUuid(bytes: Uint8Array): string {
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function createIdempotencyKey(source: Crypto | null | undefined = globalThis.crypto): string {
  if (typeof source?.randomUUID === "function") {
    try {
      return source.randomUUID();
    } catch {
      // Some browsers expose randomUUID but reject it on a plain-HTTP origin.
    }
  }

  if (typeof source?.getRandomValues === "function") {
    const bytes = source.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    return formatUuid(bytes);
  }

  fallbackSequence += 1;
  const origin = Math.floor(globalThis.performance?.timeOrigin ?? Date.now()).toString(36);
  return `request-${origin}-${Date.now().toString(36)}-${fallbackSequence.toString(36)}`;
}
