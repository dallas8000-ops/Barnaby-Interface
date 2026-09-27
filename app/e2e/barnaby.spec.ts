import { expect, test, type Page } from "@playwright/test";
import { ENGINE_QUERY } from "../playwright.config";

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
