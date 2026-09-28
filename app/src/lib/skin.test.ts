import { afterEach, describe, expect, it } from "vitest";
import { DEFAULT_SKIN, SKIN_KEY, readSkin, saveSkin } from "./skin";

describe("visual style", () => {
  afterEach(() => localStorage.clear());

  it("defaults to Control Tower, saves per device, and ignores junk values", () => {
    expect(DEFAULT_SKIN).toBe("tower");
    expect(readSkin()).toBe(DEFAULT_SKIN);
    saveSkin("aurora");
    expect(document.documentElement.dataset.skin).toBe("aurora");
    expect(readSkin()).toBe("aurora");
    localStorage.setItem(SKIN_KEY, "<script>");
    expect(readSkin()).toBe(DEFAULT_SKIN);
  });
});
