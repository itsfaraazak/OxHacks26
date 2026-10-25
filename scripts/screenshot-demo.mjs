// Walks the real localhost stack through a full session and screenshots the app at each phase.
// Requires: backend on :5000 with a detector agent reporting, frontend on :3000.
// Usage: node scripts/screenshot-demo.mjs <output-dir>
import { chromium, devices } from "@playwright/test";

const API = "http://localhost:5000";
const out = process.argv[2] ?? ".";
const post = (path, body) =>
  fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
const state = async () => (await fetch(API + "/api/state")).json();
async function waitFor(phase, timeoutMs = 90_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if ((await state()).phase === phase) return;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`timed out waiting for ${phase}`);
}

const browser = await chromium.launch();
const laptop = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const phone = await (await browser.newContext({ ...devices["iPhone 13"] })).newPage();
await laptop.goto("http://localhost:3000");
await phone.goto("http://localhost:3000");
await laptop.waitForTimeout(1500);

const shot = async (name) => {
  await laptop.waitForTimeout(700);
  await laptop.screenshot({ path: `${out}/${name}.png` });
  console.log(`${name}: ${(await state()).phase}`);
};

await post("/api/session/end");
await shot("1-idle");
await laptop.getByLabel(/focus on/i).fill("Finish the OxHack backend");
await laptop.getByRole("button", { name: /let.s focus/i }).click();
await waitFor("awaiting_phone");
await shot("2-awaiting-phone");
await post("/api/robot/events", { event: "docked" });
await waitFor("focusing");
await shot("3-focusing");
await waitFor("nudge");
await shot("4-nudge");
await waitFor("barking");
await shot("5-barking");
await waitFor("escaped");
await shot("6-escaped");
await phone.waitForTimeout(500);
await phone.screenshot({ path: `${out}/6-escaped-phone.png` });
await post("/api/robot/events", { event: "caught" });
await waitFor("focusing");
await shot("7-caught");
await laptop.getByRole("button", { name: "Devices" }).first().click();
await shot("8-devices");
await browser.close();
