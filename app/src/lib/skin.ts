/** Visual style, stored per device (the PC and a tablet can differ). See skins.css. */
export const SKINS = [
  { id: "classic", label: "Classic (quiet, follows Theme)" },
  { id: "tower", label: "Control Tower (navy, radar cyan) - default" },
  { id: "aurora", label: "Aurora (violet, electric pink)" },
  { id: "sunrise", label: "Sunrise (bright, warm, indigo)" },
] as const;
export type Skin = (typeof SKINS)[number]["id"];
export const SKIN_KEY = "barnaby.skin";
export const DEFAULT_SKIN: Skin = "tower"; // chosen by the user, 2026-09-28

const isSkin = (v: string | null): v is Skin => SKINS.some((s) => s.id === v);

export function readSkin(): Skin {
  try {
    const v = localStorage.getItem(SKIN_KEY);
    return isSkin(v) ? v : DEFAULT_SKIN;
  } catch {
    return DEFAULT_SKIN;
  }
}

export function applySkin(skin: Skin): void {
  document.documentElement.dataset.skin = skin;
}

export function saveSkin(skin: Skin): void {
  applySkin(skin);
  try {
    localStorage.setItem(SKIN_KEY, skin);
  } catch {
    /* storage blocked: applies for this run */
  }
}
