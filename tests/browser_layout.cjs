/* Read-only UI checks, apart from browser-local form input. Build frontend first. */
const assert = require("node:assert/strict");
const { mkdtempSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const deps = [join(__dirname, "../frontend")];
const { chromium } = require(require.resolve("playwright", { paths: deps }));
const axe = require.resolve("axe-core/axe.min.js", { paths: deps });
const base = process.env.PREDEL_BASE_URL || "http://127.0.0.1:8000";
const screenshots = mkdtempSync(join(tmpdir(), "predel-layout-"));

(async () => {
  const browser = await chromium.launch({
    channel: process.env.PREDEL_BROWSER_CHANNEL || undefined,
    headless: true,
  });
  try {
    const page = await browser.newPage({ reducedMotion: "reduce" });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    for (const [width, height] of [
      [1440, 900],
      [1366, 768],
      [768, 1024],
      [390, 844],
      [375, 667],
      [320, 700],
    ]) {
      await page.setViewportSize({ width, height });
      await page.goto(base, { waitUntil: "domcontentloaded" });
      await page.locator(".hero-art img").evaluate((img) => img.decode());
      await page.evaluate(() => document.fonts.ready);
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
        `Overflow at ${width}`,
      );
      const cta = await page.locator(".hero-actions .button").boundingBox();
      assert.ok(
        cta && cta.y + cta.height <= height,
        `Hero CTA below viewport at ${width}×${height}`,
      );
      const boxes = await page.evaluate(() => ({
        art: document.querySelector(".hero-art").getBoundingClientRect().bottom,
        copy: document.querySelector(".hero-copy").getBoundingClientRect().top,
      }));
      assert.ok(
        boxes.art <= boxes.copy + 12,
        `Hero artwork overlaps text at ${width}×${height}`,
      );
      await page.screenshot({
        path: join(screenshots, `home-${width}.png`),
        fullPage: true,
      });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(base);
    await page.addScriptTag({ path: axe });
    async function audit(label) {
      const violations = await page.evaluate(async () =>
        (
          await axe.run(document, {
            runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
          })
        ).violations.map((v) => ({
          id: v.id,
          targets: v.nodes.map((n) => n.target),
        })),
      );
      assert.deepEqual(violations, [], label);
    }
    await audit("Landing accessibility");
    await page.locator(".variant-switch button").nth(1).click();
    await page.locator(".demo-stage .variant-1").waitFor();
    await audit("Second example accessibility");
    await page.locator("[data-auth=register]").click();
    assert.equal(await page.locator("#authForm input:visible").count(), 3);
    await audit("Registration accessibility");
    await page.keyboard.press("Escape");
    assert.equal(
      await page.locator("#authDialog").evaluate((dialog) => dialog.open),
      false,
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator(".menu-toggle").click();
    assert.equal(
      await page.locator(".menu-toggle").getAttribute("aria-expanded"),
      "true",
    );
    await page.locator(".mobile-menu a").first().click();
    assert.equal(await page.locator(".mobile-menu").count(), 0);
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({
        result: "passed",
        screenshots,
        widths: [1440, 1366, 768, 390, 375, 320],
        axe: "no WCAG A/AA violations in checked states",
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
