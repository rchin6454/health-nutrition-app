// The backend adds this notice to every response; the page footer shows it permanently,
// so message bubbles don't repeat it.
export const DISCLAIMER = "General information, not medical advice.";

// Emergency notices are the ones that point to India's emergency numbers (112 / 108).
export function isEmergencyNotice(notice: string): boolean {
  return /\b(112|108)\b/.test(notice);
}
