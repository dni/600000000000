import { expect, test } from "@playwright/test";

test.describe("nord demo", () => {
  test("booster mints cards with genesis events, melt and combine conserve value", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(String(error)));

    await page.goto("/nord.html");
    await page.getByRole("button", { name: /open booster/i }).click();

    await expect(page.locator(".tcg-card")).toHaveCount(3);
    await expect(page.locator(".banknote")).toHaveCount(1);
    await expect(page.locator(".evt-kind", { hasText: "GENESIS" })).toHaveCount(3);

    // artwork ids are computed from the shipped bytes, not hardcoded
    await expect(page.locator(".card-x").first()).toContainText(/x: [0-9a-f]{24}/);

    // melt the first card: the chain gains a melt event and the card's
    // charge comes home as a banknote
    await page.locator(".tcg-card button", { hasText: "Melt" }).first().click();
    await expect(page.locator(".tcg-card.melted")).toHaveCount(1);
    await expect(page.locator(".evt-kind", { hasText: "MELT" })).toHaveCount(1);
    await expect(page.locator(".banknote")).toHaveCount(2);

    // combine both banknotes: 30 change + 60 melted charge = 90
    const notes = page.locator(".banknote");
    await notes.nth(0).click();
    await notes.nth(1).click();
    await page.getByRole("button", { name: /combine/i }).click();
    await expect(page.locator(".banknote")).toHaveCount(1);
    await expect(page.locator(".note-denom")).toHaveText("90");

    expect(errors).toEqual([]);
  });

  test("two fixed starter decks mint five cards each for fastplay", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(String(error)));

    await page.goto("/nord.html");
    await page.getByRole("button", { name: /starter signal/i }).click();
    await expect(page.locator(".tcg-card")).toHaveCount(5);
    await expect(page.locator(".evt-kind", { hasText: "GENESIS" })).toHaveCount(5);

    // starters are deterministic and disjoint: stone adds five different cards
    await page.getByRole("button", { name: /starter stone/i }).click();
    await expect(page.locator(".tcg-card")).toHaveCount(10);
    await expect(page.locator(".evt-kind", { hasText: "GENESIS" })).toHaveCount(10);

    // two starters, priced to the sat: 2 x 600, no change banknotes
    await expect(page.locator(".banknote")).toHaveCount(0);
    await expect(page.locator("#total")).toHaveText("1 200 SATS HELD");

    expect(errors).toEqual([]);
  });
});
