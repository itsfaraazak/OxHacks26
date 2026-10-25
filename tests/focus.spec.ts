import { expect, test, type APIRequestContext } from "@playwright/test";

const API = `http://localhost:${process.env.FIDO_TEST_BACKEND_PORT ?? "5000"}`;
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

  await expect(page.getByText("25 minutes", { exact: true })).toBeVisible();
  await expect(page.locator(".duration-options")).toHaveCount(0);
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
  await expect(page.getByText("Session failed. Catch Fido")).toBeVisible();
  await expect(page.getByRole("button", { name: "Take a breath" })).toHaveCount(0);

  // Refocusing does not end an escape; catching Fido does.
  await reading(request, true, "Focused");
  await page.getByRole("button", { name: "Stop Fido" }).click();
  await expect(heading(page, "Fido stopped. Take a moment.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Keep going" })).toHaveCount(0);
  const failed = (await (await request.get(`${API}/api/state`)).json()).session;
  await page.getByLabel("I want to focus on").fill("A fresh attempt");
  await page.getByRole("button", { name: "Start", exact: true }).click();
  await expect(heading(page, "You do your thing.")).toBeVisible();
  const restarted = (await (await request.get(`${API}/api/state`)).json()).session;
  expect(restarted.id).not.toBe(failed.id);
  expect(restarted.remaining_seconds).toBeGreaterThan(1497);
  expect(restarted.escapes).toBe(0);

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

test("camera preview is opt-in, shows frames, and can be hidden", async ({ page }) => {
  let requests = 0;
  await page.route("http://localhost:5002/preview.mjpg**", route => {
    requests++;
    return route.fulfill({
      contentType: "image/png",
      body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=", "base64"),
    });
  });
  await page.goto("/");
  const preview = page.getByRole("region", { name: "Detector camera preview" });
  expect(requests).toBe(0);
  await preview.getByRole("button", { name: "Show camera" }).click();
  const image = preview.getByRole("img", { name: "Live detector camera with focus status overlay" });
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(1);
  await preview.getByRole("button", { name: "Hide camera" }).click();
  await expect(image).toHaveCount(0);
});

test("unavailable camera preview offers retry without breaking session controls", async ({ page }) => {
  await page.route("http://localhost:5002/preview.mjpg**", route => route.abort());
  await page.goto("/");
  await page.getByRole("button", { name: "Show camera" }).click();
  await expect(page.getByText("Camera preview unavailable.", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "retry preview" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Let’s focus" })).toBeEnabled();
});

// --- Fido's voice: captions and opt-in phone audio ------------------------------------------
// These tests replace EventSource with a stand-in that starts from the real backend's /api/state
// and lets the test inject `speech` cues, so they pass whether or not the backend emits them yet.
type FidoWindow = Window & { __speak(seq: number, text: string): void };
async function withSpeechFeed(page: import("@playwright/test").Page, first?: { seq: number; text: string }) {
  await page.addInitScript(initial => {
    const speech = (seq: number, text: string) => ({ seq, cue: "nudge", clip: "nudge-1", text, at: Date.now() / 1000, url: "/api/audio/nudge-1.mp3" });
    class FakeEventSource extends EventTarget {
      static CLOSED = 2;
      readyState = 0;
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      base: Record<string, unknown> | null = null;
      constructor(readonly url: string) {
        super();
        fetch(url.replace("/api/events", "/api/state")).then(r => r.json()).then(state => {
          this.base = { ...state, speech: initial ? speech(initial.seq, initial.text) : null };
          this.readyState = 1;
          this.onopen?.();
          this.emit(this.base);
        });
        (window as unknown as { __feed: FakeEventSource }).__feed = this;
      }
      emit(state: unknown) { this.dispatchEvent(new MessageEvent("state", { data: JSON.stringify(state) })); }
      close() {}
    }
    (window as unknown as { EventSource: unknown }).EventSource = FakeEventSource;
    (window as unknown as FidoWindow).__speak = (seq, text) => {
      const feed = (window as unknown as { __feed: FakeEventSource }).__feed;
      feed.emit({ ...feed.base, version: (feed.base!.version as number) + seq, speech: speech(seq, text) });
    };
  }, first ?? null);
}
const speak = (page: import("@playwright/test").Page, seq: number, text: string) =>
  page.evaluate(([n, t]) => (window as unknown as FidoWindow).__speak(n as number, t as string), [seq, text]);

test("caption shows for a new cue, not for the speech already in the first snapshot, then fades", async ({ page }) => {
  await withSpeechFeed(page, { seq: 5, text: "Old line from before the reload" });
  await page.goto("/");
  await expect(page.getByRole("status").filter({ hasText: "LIVE" })).toBeVisible();
  const caption = page.getByTestId("speech-caption");
  await expect(caption).toHaveAttribute("aria-live", "polite");
  await expect(page.getByText("Old line from before the reload")).toHaveCount(0);

  await speak(page, 5, "Old line from before the reload"); // same seq again: still not a new cue
  await expect(page.getByText("Old line from before the reload")).toHaveCount(0);

  await speak(page, 6, "Hey... eyes back on the prize?");
  await expect(caption).toBeVisible();
  await expect(caption).toContainText("Hey... eyes back on the prize?");
  await expect(caption).toBeHidden({ timeout: 9000 }); // about 6 s, then it fades
});

test("hearing Fido on this device is off by default, opt-in, and plays each cue once", async ({ page }) => {
  await page.addInitScript(() => {
    const played: string[] = [];
    (window as unknown as { __played: string[] }).__played = played;
    HTMLMediaElement.prototype.play = function () { played.push(this.muted ? "muted" : this.src); return Promise.resolve(); };
  });
  await withSpeechFeed(page, { seq: 1, text: "First" });
  await page.goto("/");
  await expect(page.getByRole("status").filter({ hasText: "LIVE" })).toBeVisible();
  const toggle = page.getByRole("button", { name: "Hear Fido on this device" });
  const played = () => page.evaluate(() => (window as unknown as { __played: string[] }).__played);
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  expect(await page.evaluate(() => localStorage.length)).toBe(0);

  await speak(page, 2, "Muted for audio, but captioned"); // off: caption only
  await expect(page.getByTestId("speech-caption")).toContainText("captioned");
  expect(await played()).toEqual([]);

  await toggle.click(); // the tap that unlocks audio
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  expect(await played()).toEqual(["muted"]);

  await speak(page, 3, "Third");
  await speak(page, 3, "Third");
  await speak(page, 2, "Older");
  const backend = new URL(`http://localhost:${process.env.FIDO_TEST_BACKEND_PORT ?? "5000"}`).origin;
  expect(await played()).toEqual(["muted", `${backend}/api/audio/nudge-1.mp3`]);
  const clip = await page.request.get(`${backend}/api/audio/nudge-1.mp3`);
  expect(clip.ok()).toBeTruthy();
  expect(clip.headers()["content-type"]).toContain("audio/mpeg");
  expect((await clip.body()).length).toBeGreaterThan(0);

  await toggle.click();
  await speak(page, 4, "Fourth");
  expect(await played()).toHaveLength(2);
});

test("preferences list the device toggle (off) and send Fido's voice to the backend", async ({ page }) => {
  await withSpeechFeed(page);
  const patches: unknown[] = [];
  await page.route(`${API}/api/settings`, route => {
    if (route.request().method() !== "PATCH") return route.fallback();
    patches.push(route.request().postDataJSON());
    return route.fulfill({ json: {} });
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.getByRole("button", { name: "Preferences" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("checkbox", { name: /Hear Fido on this device/ })).not.toBeChecked();
  await dialog.getByRole("checkbox", { name: /^Fido’s voice/ }).click();
  await expect.poll(() => patches).toEqual([{ voice_enabled: false }]);
});

test("global mute keeps captions and local opt-in without replaying muted cues", async ({ page }) => {
  await page.addInitScript(() => {
    const played: string[] = [];
    (window as unknown as { __played: string[] }).__played = played;
    HTMLMediaElement.prototype.play = function () { played.push(this.muted ? "muted" : this.src); return Promise.resolve(); };
  });
  await withSpeechFeed(page);
  await page.goto("/");
  await expect(page.getByRole("status").filter({ hasText: "LIVE" })).toBeVisible();
  const toggle = page.getByRole("button", { name: "Hear Fido on this device" });
  await toggle.click();
  const setVoice = (enabled: boolean) => page.evaluate(on => {
    const feed = (window as unknown as {
      __feed: { base: { settings: Record<string, unknown> }; emit(state: unknown): void };
    }).__feed;
    feed.base.settings.voice_enabled = on;
    (window as unknown as FidoWindow).__speak(1, "Caption while globally muted");
  }, enabled);
  await setVoice(false);
  await expect(page.getByTestId("speech-caption")).toContainText("Caption while globally muted");
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  const played = () => page.evaluate(() => (window as unknown as { __played: string[] }).__played);
  expect(await played()).toEqual(["muted"]);
  await setVoice(true); // same sequence must not play on unmute
  expect(await played()).toEqual(["muted"]);
  await speak(page, 2, "New audible cue");
  await expect.poll(played).toHaveLength(2);
});
