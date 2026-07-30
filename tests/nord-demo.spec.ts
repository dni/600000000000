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

    // combine both banknotes: 3 000 change + 6 000 melted charge = 9 000
    const notes = page.locator(".banknote");
    await notes.nth(0).click();
    await notes.nth(1).click();
    await page.getByRole("button", { name: /combine/i }).click();
    await expect(page.locator(".banknote")).toHaveCount(1);
    await expect(page.locator(".note-denom")).toHaveText("9 000");

    expect(errors).toEqual([]);
  });
});
