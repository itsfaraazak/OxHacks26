import { expect, test } from "@playwright/test";

test("phone focus journey and responsive layout", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Find your focus." })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Let’s focus" }).click();
  await expect(page.getByRole("button", { name: "Take a breath" })).toBeVisible();
  await page.getByRole("button", { name: "Simulate distraction" }).click();
  await expect(page.getByRole("heading", { name: "Hey, eyes over here." })).toBeVisible();
  await page.getByRole("button", { name: "Simulate distraction" }).click();
  await expect(page.getByRole("heading", { name: "A little less scrolling." })).toBeVisible();
  await page.getByRole("button", { name: "Simulate distraction" }).click();
  await expect(page.getByRole("heading", { name: "Your phone’s on a walk." })).toBeVisible();
  await page.getByRole("button", { name: "Refocus", exact: true }).click();
  await expect(page.getByRole("heading", { name: "You do your thing." })).toBeVisible();
  await page.getByRole("button", { name: "Take a breath" }).click();
  await expect(page.getByRole("button", { name: "Keep going" })).toBeVisible();
  await page.getByRole("button", { name: "End session" }).click();
  await expect(page.getByRole("button", { name: "Let’s focus" })).toBeVisible();
  await page.getByRole("navigation", { name: "Mobile navigation" }).getByRole("button", { name: "Devices" }).click();
  await expect(page.getByRole("button", { name: "Enable camera preview" })).toBeVisible();
  await page.getByRole("navigation", { name: "Mobile navigation" }).getByRole("button", { name: "Activity" }).click();
  await expect(page.getByRole("heading", { name: "Your focus journal" })).toBeVisible();
});

test("desktop navigation and preferences", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.getByRole("button", { name: "Preferences" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Sounds good" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
