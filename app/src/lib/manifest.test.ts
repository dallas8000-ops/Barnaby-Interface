import { describe, expect, it } from "vitest";
import html from "../../index.html?raw";
import manifestText from "../../public/manifest.webmanifest?raw";
import icon192 from "../../public/icons/barnaby-192.png?inline";
import icon512 from "../../public/icons/barnaby-512.png?inline";

// Chrome on Android offers "Add to Home screen" as a full-screen app only when the
// manifest has these, and the icons it names actually exist at the right size.
const manifest = JSON.parse(manifestText);

function pngSize(dataUrl: string): [number, number] {
  const bytes = Uint8Array.from(atob(dataUrl.split(",")[1]!), (c) => c.charCodeAt(0));
  const view = new DataView(bytes.buffer);
  return [view.getUint32(16), view.getUint32(20)];
}

describe("tablet home-screen app manifest", () => {
  it("opens full screen at the app root", () => {
    expect(manifest.name).toBe("Barnaby");
    expect(manifest.short_name).toBe("Barnaby");
    expect(manifest.start_url).toBe("/");
    expect(manifest.scope).toBe("/");
    expect(manifest.display).toBe("standalone");
    expect(manifest.background_color).toMatch(/^#[0-9a-f]{6}$/i);
    expect(manifest.theme_color).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("names 192 and 512 px PNG icons whose files really are that size", () => {
    const bySize = Object.fromEntries(manifest.icons.map((i: { sizes: string; src: string; type: string }) => [i.sizes, i]));
    expect(bySize["192x192"]).toMatchObject({ src: "/icons/barnaby-192.png", type: "image/png" });
    expect(bySize["512x512"]).toMatchObject({ src: "/icons/barnaby-512.png", type: "image/png" });
    expect(pngSize(icon192)).toEqual([192, 192]);
    expect(pngSize(icon512)).toEqual([512, 512]);
  });

  it("is linked from the page", () => {
    expect(html).toContain('<link rel="manifest" href="/manifest.webmanifest"');
    expect(html).toMatch(/<meta name="theme-color"/);
    expect(html).toContain('rel="apple-touch-icon"');
  });
});
