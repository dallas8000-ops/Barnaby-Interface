import { expect, test, type Page } from "@playwright/test";
import { ENGINE_QUERY, TABLET_PORT } from "../playwright.config";

// The engine is shared across tests (one process), so each test starts a new session.
async function fresh(page: Page) {
  await page.goto(`/${ENGINE_QUERY}`);
  await expect(page.getByText("Connected")).toBeVisible();
  await page.getByRole("button", { name: "New" }).click();
}

async function holdToTalk(page: Page) {
  await page.locator("main").click({ position: { x: 20, y: 20 } });
  await page.keyboard.down("Space");
  await expect(page.getByTestId("caption")).toHaveText("Listening. Release to send.");
  await page.keyboard.up("Space");
}

test("voice turn: review draft, stream reply, tool strips, approval, resume", async ({ page }) => {
  await fresh(page);
  await expect(page.getByText("Hearing")).toBeVisible();
  await expect(page.locator("dd").filter({ hasText: "Ready" }).first()).toBeVisible();

  await holdToTalk(page);
  const draft = page.getByTestId("draft");
  await expect(draft).toBeVisible();
  await expect(page.getByLabel(/check what i heard/i)).toHaveValue("run the tests and fix anything failing");
  await page.keyboard.press("Enter");

  await expect(page.getByTestId("caption")).toHaveText(/Working/);
  await expect(page.getByTestId("strip").first()).toBeVisible();
  await expect(page.getByTestId("approval")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("approval")).toHaveAttribute("data-risk", "high");
  await expect(page.getByTestId("caption")).toHaveText(/Waiting for your OK|Speaking/);

  const strips = page.getByTestId("strip");
  await expect(strips).toHaveCount(5);
  await expect(strips.nth(0)).toHaveAttribute("data-status", "failed");
  await expect(strips.nth(3)).toHaveAttribute("data-status", "done");
  await expect(page.getByRole("button", { name: "Open in VS Code" }).first()).toBeVisible();

  await expect(page.getByTestId("conversation")).toContainText("All 42 tests pass now");
  await expect(page.getByTestId("conversation").locator("pre")).toContainText("selectTotal");

  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("approval")).toHaveCount(0);
  await expect(page.getByTestId("conversation")).toContainText("Pushed to origin", { timeout: 15_000 });
  await expect(page.getByTestId("conversation")).toContainText("You answered the approval request.");
});

test("typed instruction, stop mid-turn, session appears in sidebar", async ({ page }) => {
  await fresh(page);
  await page.getByLabel("Type an instruction").fill("refactor the cart store");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();
  await page.getByRole("button", { name: "Stop" }).click();
  await expect(page.getByTestId("caption")).toHaveText("Hold Space to talk");
  await expect(page.getByTestId("conversation")).toContainText("Stopped");
  await expect(page.getByRole("navigation").getByText("refactor the cart store")).toBeVisible();
});

test("settings dialog toggles review mode and closes with Escape", async ({ page }) => {
  await fresh(page);
  await page.getByRole("button", { name: "Settings" }).click();
  const dialog = page.getByRole("dialog", { name: "Settings" });
  await expect(dialog).toBeVisible();
  const review = dialog.getByRole("switch", { name: "Review before sending" });
  await expect(review).toHaveAttribute("aria-checked", "true");
  await review.click();
  await expect(review).toHaveAttribute("aria-checked", "false");
  await review.click();
  await expect(review).toHaveAttribute("aria-checked", "true");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
});

test("voice profile learns a correction after it repeats", async ({ page }) => {
  await fresh(page);
  for (let i = 0; i < 2; i++) {
    await holdToTalk(page);
    const box = page.getByLabel(/check what i heard/i);
    await expect(box).toBeVisible();
    await box.fill("run the specs and fix anything failing");
    await page.keyboard.press("Enter");
    if (i === 1) await expect(page.getByText(/Learned: “tests” means specs/)).toBeVisible();
    await expect(page.getByTestId("approval")).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "Decline" }).click();
    await expect(page.getByTestId("caption")).toHaveText("Hold Space to talk", { timeout: 15_000 });
  }
  await page.getByRole("button", { name: "Voice profile" }).click();
  const dialog = page.getByRole("dialog", { name: "Voice profile" });
  await expect(dialog.getByRole("cell", { name: "specs", exact: true })).toBeVisible();

  await page.keyboard.press("Escape");
  await holdToTalk(page);
  await expect(page.getByLabel(/check what i heard/i)).toHaveValue("run the specs and fix anything failing");
  await page.getByRole("button", { name: "Discard" }).click();
});

test("engine switch: Cursor runs the turn, gets its own session, and is labelled", async ({ page }) => {
  await fresh(page);
  const cursor = page.getByRole("radio", { name: "Cursor" });
  await cursor.click();
  await expect(cursor).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText("Switched to Cursor.")).toBeVisible();

  await page.getByLabel("Type an instruction").fill("tidy the imports");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("approval")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "Decline" }).click();
  await expect(page.getByTestId("caption")).toHaveText("Hold Space to talk", { timeout: 15_000 });
  await expect(page.getByTestId("conversation").getByText("Cursor", { exact: true }).first()).toBeVisible();
  const row = page.getByRole("navigation").getByRole("button", { name: /tidy the imports/ });
  await expect(row.getByText("Cursor", { exact: true })).toBeVisible();

  // Voice-style routing back to Claude from the same box.
  await page.getByLabel("Type an instruction").fill("use claude");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("radio", { name: "Claude" })).toHaveAttribute("aria-checked", "true");
});

test("chained steps pause for each approval and finish in order", async ({ page }) => {
  await fresh(page);
  await page.getByLabel("Type an instruction").fill("lint the code, then run the tests");
  await page.keyboard.press("Enter");
  const chain = page.getByTestId("chain");
  await expect(chain).toContainText("Step 1 of 2");
  await expect(chain.locator("li")).toHaveText([/Lint the code/, /Run the tests/]);

  for (const step of [1, 2]) {
    await expect(page.getByTestId("approval")).toBeVisible({ timeout: 15_000 });
    await expect(chain).toContainText(`Step ${step} of 2`);
    await expect(chain).toContainText("waiting for your OK");
    await page.getByRole("button", { name: "Approve" }).click();
  }
  await expect(chain).toHaveCount(0, { timeout: 15_000 });
  const convo = page.getByTestId("conversation");
  await expect(convo).toContainText("Lint the code");
  await expect(convo).toContainText("Run the tests");
});

test("template picker runs a template and Stop ends it", async ({ page }) => {
  await fresh(page);
  await page.getByRole("button", { name: "Templates" }).click();
  await page.getByRole("dialog").getByRole("button", { name: /Code Review Checklist/ }).click();
  await page.getByLabel(/what should it work on/i).fill("the auth module");
  await page.getByRole("button", { name: "Run Code Review Checklist" }).click();
  const chain = page.getByTestId("chain");
  await expect(chain).toContainText("Step 1 of 5 · Code Review Checklist");
  await chain.getByRole("button", { name: /stop steps/i }).click();
  await expect(chain).toHaveCount(0);
  await expect(page.getByTestId("caption")).toHaveText("Hold Space to talk");
});

test("hands-free toggles on and off through the engine", async ({ page }) => {
  await fresh(page);
  const toggle = page.getByRole("switch", { name: /hands-free/i });
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await expect(page.getByTestId("caption")).toHaveText("Hands-free: just start talking");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  await expect(page.getByTestId("caption")).toHaveText("Hold Space to talk");
});

test("Devin: key saved in Settings enables Devin, which runs a turn; removing the key disables it", async ({ page }) => {
  await fresh(page);
  const devin = page.getByRole("radio", { name: "Devin" });
  await expect(devin).toBeDisabled();

  await page.getByRole("button", { name: "Settings" }).click();
  const dialog = page.getByRole("dialog", { name: "Settings" });
  const keyInput = dialog.getByPlaceholder("apk_user_…");
  await expect(keyInput).toHaveAttribute("type", "password");
  await keyInput.fill("apk_user_" + "e2eTestKey0".repeat(4));
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(dialog.getByText("Saved in Windows Credential Manager")).toBeVisible();
  await expect(keyInput).toHaveCount(0);
  await page.keyboard.press("Escape");

  await expect(devin).toBeEnabled();
  await page.getByLabel("Type an instruction").fill("Devin, add a health check endpoint");
  await page.keyboard.press("Enter");
  await expect(devin).toHaveAttribute("aria-checked", "true");
  const convo = page.getByTestId("conversation");
  await expect(convo.getByText(/opened a pull request/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("complementary", { name: "Activity" }).getByText("session https://app.devin.ai/sessions/mock")).toBeVisible();
  await expect(page.getByTestId("caption")).toHaveText("Hold Space to talk", { timeout: 15_000 });

  // Clean up the shared engine: remove the key, which also switches back to Claude.
  await page.getByRole("button", { name: "Settings" }).click();
  await dialog.getByRole("button", { name: "Remove" }).click();
  await expect(dialog.getByPlaceholder("apk_user_…")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(devin).toBeDisabled();
  await expect(page.getByRole("radio", { name: "Claude" })).toHaveAttribute("aria-checked", "true");
});

test("tablet: pair by QR link, hold to talk, approve on the tablet, hear it there, removed from the PC", async ({ page, browser }) => {
  // PC window: tablet access is on (see playwright.config); make a pairing link.
  await fresh(page);
  await page.getByRole("button", { name: "Settings" }).click();
  const dialog = page.getByRole("dialog", { name: "Settings" });
  await expect(dialog.getByText(`On, port ${TABLET_PORT}`)).toBeVisible({ timeout: 15_000 });
  await dialog.getByRole("button", { name: "Pair a tablet" }).click();
  await expect(dialog.getByTestId("pair-qr")).toBeVisible();
  const link = await dialog.locator("p.font-mono").innerText();
  const code = new URL(link).hash; // #pair=...
  expect(code).toMatch(/^#pair=[A-Za-z0-9_-]{16,}$/);
  await page.keyboard.press("Escape");

  // The tablet: its own browser context, like a separate device. The test machine's LAN
  // address may be unroutable here, so open the same server on loopback.
  const tablet = await browser.newContext({
    ignoreHTTPSErrors: true, // trust of Barnaby's CA chain is covered by the engine's TLS tests
    viewport: { width: 1280, height: 800 },
    hasTouch: true,
    permissions: ["microphone"],
    userAgent: "Mozilla/5.0 (Linux; Android 15; SM-X820) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
  });
  await tablet.addInitScript(() => {
    const spoken: string[] = [];
    (window as unknown as { __spoken: string[] }).__spoken = spoken;
    const fake = { speak: (u: { text: string }) => spoken.push(u.text), cancel: () => {} };
    Object.defineProperty(window, "speechSynthesis", { value: fake, configurable: true });
  });
  const t = await tablet.newPage();
  await t.goto(`https://127.0.0.1:${TABLET_PORT}/${code}`);
  await expect(t.getByTestId("caption")).toHaveText("Hold the circle to talk", { timeout: 15_000 });
  expect(new URL(t.url()).hash).toBe(""); // the one-time code was removed from the address bar

  // The PC lists the tablet as connected.
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(dialog.getByTestId("tablet-device")).toContainText("Galaxy Tab");
  await expect(dialog.getByTestId("tablet-device")).toContainText("connected");
  await page.keyboard.press("Escape");

  // Hold to talk on the tablet (Chromium's fake microphone), release to send.
  const ptt = t.getByTestId("ptt");
  await ptt.hover();
  await t.mouse.down();
  await expect(t.getByTestId("caption")).toHaveText("Listening. Release to send.");
  await t.waitForTimeout(1500);
  await t.mouse.up();
  // Review mode: the PC transcribed it; the draft appears on the tablet.
  const draft = t.getByLabel(/check what i heard/i);
  // "specs" when the earlier voice-profile test ran first: learned corrections apply to tablet audio too.
  await expect(draft).toHaveValue(/^run the (tests|specs) and fix anything failing$/, { timeout: 15_000 });
  await t.getByRole("button", { name: /^Send/ }).click();

  // The scripted turn asks to push: approve-or-decline on the tablet.
  await expect(t.getByTestId("approval")).toBeVisible({ timeout: 15_000 });
  await t.getByRole("button", { name: "Decline" }).click();
  await expect(t.getByTestId("caption")).toHaveText("Hold the circle to talk", { timeout: 15_000 });
  const spoken = await t.evaluate(() => (window as unknown as { __spoken: string[] }).__spoken);
  expect(spoken.length).toBeGreaterThan(0); // replies spoken by the tablet's own voice

  // Remove the tablet on the PC: it's disconnected and asked to pair again.
  await page.getByRole("button", { name: "Settings" }).click();
  await dialog.getByRole("button", { name: "Remove Galaxy Tab" }).click();
  await expect(t.getByTestId("pair-screen")).toBeVisible({ timeout: 10_000 });
  await expect(dialog.getByTestId("tablet-device")).toHaveCount(0);
  await tablet.close();
});
