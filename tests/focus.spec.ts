import { expect, test, type APIRequestContext } from "@playwright/test";

const API = "http://localhost:5000";
const DEFAULTS = { bark_after_seconds: 6, escape_after_seconds: 8, require_dock: true, motion_enabled: true };

/** Start every test from a clean backend: no session, fast escalation, no dock needed. */
async function resetBackend(request: APIRequestContext) {
  await request.post(`${API}/api/session/end`); // 409 when idle; that's fine
  const settings = await request.patch(`${API}/api/settings`, {
    data: { bark_after_seconds: 1, escape_after_seconds: 1, require_dock: false, motion_enabled: true },
  });
  expect(settings.ok()).toBe(true);
}
/** Pretend to be `python -m detector.agent` reporting one camera decision. */
async function reading(request: APIRequestContext, focused: boolean, reason: string) {
  const response = await request.post(`${API}/api/readings`, {
    data: { source: "laptop", reading: { focused, calibrated: true, reason, fps: 30 } },
  });
  expect(response.ok()).toBe(true);
}
const heading = (page: import("@playwright/test").Page, name: string) => page.getByRole("heading", { name });

test.beforeEach(async ({ request }) => resetBackend(request));
test.afterAll(async ({ request }) => {
  await request.post(`${API}/api/session/end`);
  await request.patch(`${API}/api/settings`, { data: DEFAULTS });
});

test("phone focus journey against the backend", async ({ page, request }) => {
  const task = `Write the demo script ${Date.now()}`;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("status").filter({ hasText: "LIVE" })).toBeVisible();
  await expect(heading(page, "Find your focus.")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  await page.getByLabel("I want to focus on").fill(task);
  await page.getByRole("button", { name: "Let’s focus" }).click();
  await expect(heading(page, "You do your thing.")).toBeVisible();
  await expect(page.getByLabel(/minutes .* seconds remaining/)).toBeVisible();

  // A camera says NO: nudge, then barking after 1 s, then escaped after 1 s more.
  await reading(request, false, "Looking Away Sideways");
  await expect(heading(page, "Hey, eyes over here.")).toBeVisible();
  await expect(page.getByText("Looking Away Sideways [laptop]")).toBeVisible();
  await reading(request, false, "Looking Away Sideways");
  await expect(heading(page, "Woof! Back to your one thing.")).toBeVisible();
  await reading(request, false, "Looking Away Sideways");
  await expect(heading(page, "Fido ran off with your phone.")).toBeVisible();
  await expect(page.getByText("tap the good-boy card")).toBeVisible();
  await expect(page.getByRole("button", { name: "Take a breath" })).toHaveCount(0);

  // Refocusing does not end an escape; catching Fido does.
  await reading(request, true, "Focused");
  await page.getByRole("button", { name: "I caught Fido" }).click();
  await expect(heading(page, "You do your thing.")).toBeVisible();

  await page.getByRole("button", { name: "Take a breath" }).click();
  await expect(page.getByRole("button", { name: "Keep going" })).toBeVisible();
  await page.getByRole("button", { name: "End session" }).click();
  await expect(page.getByRole("button", { name: "Let’s focus" })).toBeVisible();

  const nav = page.getByRole("navigation", { name: "Mobile navigation" });
  await nav.getByRole("button", { name: "Activity" }).click();
  await expect(heading(page, "Your focus journal")).toBeVisible();
  const row = page.locator(".history-row").filter({ hasText: task });
  await expect(row).toContainText("ended early");
  await expect(row).toContainText("1 distractions · 1 escapes");

  await nav.getByRole("button", { name: "Devices" }).click();
  await expect(heading(page, "Laptop camera")).toBeVisible();
  await expect(heading(page, "Vitals")).toBeVisible();
});

test("awaiting the phone until Fido reports it docked", async ({ page, request }) => {
  await request.patch(`${API}/api/settings`, { data: { require_dock: true } });
  await page.goto("/");
  await page.getByRole("button", { name: "Let’s focus" }).click();
  await expect(heading(page, "Place your phone on Fido.").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Start without docking" })).toBeVisible();
  await request.post(`${API}/api/robot/events`, { data: { event: "docked", info: { battery: 87 } } });
  await expect(heading(page, "You do your thing.")).toBeVisible();
  await expect(page.getByText("Phone docked", { exact: true })).toBeVisible();
  await request.post(`${API}/api/robot/events`, { data: { event: "undocked" } });
});

test("desktop settings edit the backend", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(page.getByRole("status").filter({ hasText: "LIVE" })).toBeVisible();
  await page.getByRole("button", { name: "Preferences" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  // Checkboxes mirror backend state, so they flip once the new snapshot arrives.
  const motion = dialog.getByRole("checkbox", { name: /Fido can move/ });
  await motion.click();
  await expect(motion).not.toBeChecked();
  await expect.poll(async () => (await (await request.get(`${API}/api/settings`)).json()).motion_enabled).toBe(false);

  await dialog.getByLabel("Bark after").fill("4");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(dialog).not.toBeVisible();
  expect((await (await request.get(`${API}/api/settings`)).json()).bark_after_seconds).toBe(4);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("offline banner when the backend is unreachable", async ({ page }) => {
  await page.route(`${API}/**`, route => route.abort());
  await page.goto("/");
  await expect(page.getByRole("alert").filter({ hasText: "Can’t reach Fido’s backend" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "OFFLINE" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Let’s focus" })).toBeDisabled();
});
