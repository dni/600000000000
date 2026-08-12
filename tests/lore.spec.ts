import { expect, test } from "@playwright/test";

test("home page links to the living lore", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Lore", exact: true })).toHaveAttribute("href", "lore.html");
});

test("lore preserves the full current origin and two-asset arc", async ({ page }) => {
  await page.goto("/lore.html");

  await expect(page.getByRole("heading", { name: "THE LORE" })).toBeVisible();
  await expect(page.getByText("The stranger gave us the number. Oink gave it life.")).toBeVisible();
  await expect(page.getByText("At the first gathering in Austria were dni, sat, shillie, michael1011 and flx.", { exact: false })).toBeVisible();
  await expect(page.getByText("Oink memed 600 Billion into existence.", { exact: false })).toBeVisible();
  await expect(page.getByText("Oink is our Satoshi.")).toBeVisible();
  await expect(page.getByText("Christmas 2026", { exact: false })).toBeVisible();
  await expect(page.getByRole("link", { name: "600B Timelock TCG" })).toHaveAttribute("href", "https://github.com/BIMbeamFLX/600BillionTimelockTCG");
  await expect(page.getByRole("link", { name: "DJ David Clanker" })).toHaveAttribute("href", "https://github.com/labsBIMbeam/DjDavidClanker");
  await expect(page.getByText("NOT A CULT BUT CULTURE.")).toBeVisible();
});

test("lore has no horizontal overflow on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/lore.html");

  await expect(page.getByRole("heading", { name: "THE LORE" })).toBeVisible();
  const overflows = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(overflows).toBeFalsy();
});

test("lore page loads without browser errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error") errors.push(message.text());
  });

  await page.goto("/lore.html");
  expect(errors).toEqual([]);
});