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

  test("two fixed starter decks mint forty real E1 cards each", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(String(error)));

    await page.goto("/nord.html");
    await page.getByRole("button", { name: /starter signal/i }).click();
    // 40 cards, 25 uniques rendered as grouped tiles - 16x the Basic
    // Resource is one tile with a count badge, not sixteen tiles
    await expect(page.locator(".proto-card")).toHaveCount(25);
    await expect(page.locator(".evt-kind", { hasText: "GENESIS" })).toHaveCount(40);
    await expect(page.locator(".proto-count").first()).toHaveText("×16");
    await expect(page.locator("#total")).toHaveText("600 SATS HELD");

    // starters are deterministic and disjoint: stone is the Power deck
    await page.getByRole("button", { name: /starter stone/i }).click();
    await expect(page.locator(".proto-card")).toHaveCount(50);
    await expect(page.locator(".evt-kind", { hasText: "GENESIS" })).toHaveCount(80);

    // priced to the sat: 2 x (40 x 15) = 1 200, no change banknotes
    await expect(page.locator(".banknote")).toHaveCount(0);
    await expect(page.locator("#total")).toHaveText("1 200 SATS HELD");

    // melt one single-copy card: its 15 sats come home as a banknote,
    // total conserved
    await page.locator(".proto-card button", { hasText: "Melt" }).last().click();
    await expect(page.locator(".banknote")).toHaveCount(1);
    await expect(page.locator(".note-denom")).toHaveText("15");
    await expect(page.locator("#total")).toHaveText("1 200 SATS HELD");

    expect(errors).toEqual([]);
  });
});
