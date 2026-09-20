/* Run against an isolated local workspace; creates one test account and a deck. */
const assert = require("node:assert/strict");
const { randomUUID } = require("node:crypto");
const { mkdtempSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const { chromium } = require(process.env.PREDEL_PLAYWRIGHT || "playwright");

const base = process.env.PREDEL_BASE_URL || "http://127.0.0.1:8000";
const template = process.env.PREDEL_TEST_TEMPLATE;
assert.ok(template, "Set PREDEL_TEST_TEMPLATE to a real PPTX fixture.");
const screenshots = mkdtempSync(join(tmpdir(), "predel-browser-"));

(async () => {
  const browser = await chromium.launch({
    channel: process.env.PREDEL_BROWSER_CHANNEL || undefined,
    headless: true,
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(base, { waitUntil: "domcontentloaded" });
    await page.screenshot({
      path: join(screenshots, "desktop.png"),
      animations: "disabled",
    });
    await page.locator("[data-prompt]").first().click();
    await page.locator("#quickStart button[type=submit]").click();
    assert.equal(await page.locator("#authForm input:visible").count(), 3);
    const username = "test_" + randomUUID().slice(0, 12);
    await page.locator("#username").fill(username);
    await page.locator("#password").fill("test-browser-password");
    await page.locator("#position").fill("Менеджер продукта");
    await page.screenshot({
      path: join(screenshots, "registration.png"),
      animations: "disabled",
    });
    await page.locator("#authSubmit").click();
    await page.waitForURL("**/studio");
    await page.locator("#studioLock").waitFor({ state: "hidden" });
    await page.waitForFunction(
      () =>
        !document.querySelector("#modelNote").textContent.includes("Проверяем"),
    );
    assert.ok(
      (await page.locator("#content").inputValue()).includes("квартальный"),
    );
    await page.screenshot({
      path: join(screenshots, "studio.png"),
      animations: "disabled",
    });
    await page.locator("#templateFile").setInputFiles(template);
    await page.locator("#uploadButton").click();
    await page.waitForFunction(() =>
      document.querySelector("#status").textContent.includes("Шаблон готов"),
    );
    await page
      .locator("#content")
      .fill(
        "# Развитие продукта\n## Проблема\nПодготовка слайдов отнимает время.\n## Решение\nАвтоматизация сохраняет фирменный стиль.\n## План\nИзучить шаблон, собрать варианты, проверить результат.",
      );
    await page.locator("#count").fill("5");
    if (await page.locator("#offline").isEnabled())
      await page.locator("#offline").check();
    await page.locator("#generateButton").click();
    await page.locator("#variants button").first().waitFor({ timeout: 120000 });
    assert.equal(await page.locator("#variants button").count(), 3);
    await page.locator("#variants button").nth(1).click();
    assert.equal(
      await page
        .locator("#variants button")
        .nth(1)
        .getAttribute("aria-pressed"),
      "true",
    );
    const downloaded = page.waitForEvent("download");
    await page.locator(".downloads a").first().click();
    assert.ok((await downloaded).suggestedFilename().endsWith(".pptx"));
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("#history").waitFor({ state: "visible" });
    await page.locator("#batches").selectOption({ index: 1 });
    await page.locator("#variants button").first().waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    await page.screenshot({
      path: join(screenshots, "studio-mobile.png"),
      animations: "disabled",
    });
    await page.locator("#logout").click();
    await page.waitForURL(base + "/");
    assert.equal(
      (await page.request.get(base + "/v1/templates")).status(),
      401,
    );
    await page.screenshot({
      path: join(screenshots, "mobile.png"),
      animations: "disabled",
    });
    await page.locator("[data-auth=login]").click();
    assert.equal(await page.locator("#authForm input:visible").count(), 2);
    await page.locator("#username").fill(username);
    await page.locator("#password").fill("wrong-password");
    await page.locator("#authSubmit").click();
    await page.waitForFunction(() =>
      document.querySelector("#authError").textContent.includes("Неверное"),
    );
    await page.locator("#password").fill("test-browser-password");
    await page.locator("#authSubmit").click();
    await page.waitForURL("**/studio");
    await page.locator("#history").waitFor({ state: "visible" });
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ result: "passed", screenshots }));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
