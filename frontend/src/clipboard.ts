function copyWithSelection(text: string): boolean {
  if (typeof document.execCommand !== "function") return false;

  const field = document.createElement("textarea");
  field.value = text;
  field.readOnly = true;
  field.setAttribute("aria-hidden", "true");
  field.style.position = "fixed";
  field.style.inset = "0 auto auto -9999px";
  field.style.opacity = "0";
  document.body.appendChild(field);

  try {
    field.focus();
    field.select();
    field.setSelectionRange(0, field.value.length);
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    field.remove();
  }
}

export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Plain-HTTP private addresses commonly reject the modern Clipboard API.
      // Continue in the same user gesture with the selection-based fallback.
    }
  }

  return copyWithSelection(text);
}
