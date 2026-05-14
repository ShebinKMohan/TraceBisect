import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(webRoot, "..", "..");
const baseUrl = process.env.STUDIO_URL ?? "http://127.0.0.1:3000";
const chromeExecutable =
  process.env.PLAYWRIGHT_CHROME_EXECUTABLE ??
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const screenshotDir = path.join(webRoot, ".artifacts", "studio-verification");

const fixtures = {
  baseline: path.join(repoRoot, "tests/fixtures/refund_search_baseline.tbtrace"),
  candidate: path.join(
    repoRoot,
    "tests/fixtures/refund_search_candidate_changed_tool_args.tbtrace",
  ),
};

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

async function expectOne(page, selector, label) {
  const locator = page.locator(selector);
  await locator.first().waitFor({ state: "attached", timeout: 10000 });
  const count = await locator.count();
  assert(count === 1, `Expected one ${label}, found ${count}`);
  return locator;
}

async function expectText(page, selector, text, label) {
  const locator = await expectOne(page, selector, label);
  await page.waitForFunction(
    ({ targetSelector, targetText }) =>
      document.querySelector(targetSelector)?.textContent?.includes(targetText),
    { targetSelector: selector, targetText: text },
    { timeout: 10000 },
  );
  const content = await locator.textContent();
  assert(content?.includes(text), `${label} did not include "${text}". Actual: ${content}`);
}

async function assertNoHorizontalOverflow(page, label) {
  const sizes = await page.evaluate(() => ({
    body: document.body.scrollWidth,
    doc: document.documentElement.scrollWidth,
    inner: window.innerWidth,
  }));
  assert(
    sizes.body <= sizes.inner && sizes.doc <= sizes.inner,
    `${label} has horizontal overflow: ${JSON.stringify(sizes)}`,
  );
}

async function expectTraceDetailTab(page, tabName, selector, expectedText) {
  await page.getByTestId("trace-detail-tabs").getByRole("tab", { name: tabName }).click();
  await expectText(page, selector, expectedText, `${tabName} trace detail tab`);
}

async function assertContrast(page, selector, label, minimum = 4.5) {
  const ratio = await page.locator(selector).evaluate((node) => {
    function parseColor(value) {
      const match = value.match(/rgba?\(([^)]+)\)/);
      if (!match) return null;
      const parts = match[1].split(",").map((part) => Number.parseFloat(part.trim()));
      return {
        r: parts[0],
        g: parts[1],
        b: parts[2],
        a: parts.length > 3 ? parts[3] : 1,
      };
    }

    function luminance(channel) {
      const value = channel / 255;
      return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    }

    function contrast(foreground, background) {
      const foregroundLum =
        0.2126 * luminance(foreground.r) +
        0.7152 * luminance(foreground.g) +
        0.0722 * luminance(foreground.b);
      const backgroundLum =
        0.2126 * luminance(background.r) +
        0.7152 * luminance(background.g) +
        0.0722 * luminance(background.b);
      const light = Math.max(foregroundLum, backgroundLum);
      const dark = Math.min(foregroundLum, backgroundLum);
      return (light + 0.05) / (dark + 0.05);
    }

    function nearestBackground(element) {
      let current = element;
      while (current) {
        const background = parseColor(window.getComputedStyle(current).backgroundColor);
        if (background && background.a > 0.4) return background;
        current = current.parentElement;
      }
      return parseColor(window.getComputedStyle(document.body).backgroundColor) ?? {
        r: 255,
        g: 255,
        b: 255,
        a: 1,
      };
    }

    const foreground = parseColor(window.getComputedStyle(node).color);
    const background = nearestBackground(node);
    if (!foreground) return 0;
    return contrast(foreground, background);
  });
  assert(
    ratio >= minimum,
    `${label} contrast ${ratio.toFixed(2)} is below ${minimum.toFixed(2)}`,
  );
}

async function main() {
  await mkdir(screenshotDir, { recursive: true });
  const browser = await chromium.launch({
    executablePath: chromeExecutable,
    headless: true,
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 980 },
    colorScheme: "light",
  });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await page.goto(baseUrl, { waitUntil: "load" });
  await page.getByTestId("studio-title").waitFor({ state: "visible" });
  await expectText(page, '[data-testid="studio-title"]', "Trace runs", "product title");
  await expectText(page, '[data-testid="runs-table"]', "Refund regression", "runs table");
  await expectText(page, '[data-testid="runs-table"]', "changed tool args", "run divergence signal");
  await page.locator(".search-control input").fill("does-not-exist");
  await expectText(page, '[data-testid="runs-table"]', "No comparison runs match", "empty run filter");
  await page.getByTestId("clear-run-filters").click();
  await expectText(page, '[data-testid="runs-table"]', "Refund regression", "cleared run filters");
  await expectText(page, '[data-testid="trace-tree"]', "TOOL_CALL", "trace tree event type");
  await expectText(page, '[data-testid="trace-tree"]', "search_database", "trace tree event name");
  await expectText(page, '[data-testid="details-panel"]', "search_database", "details panel");
  await expectTraceDetailTab(
    page,
    "Metadata",
    '[data-testid="trace-detail-metadata"]',
    "Trace id",
  );
  await expectTraceDetailTab(
    page,
    "Observations",
    '[data-testid="trace-detail-observations"]',
    "search_database",
  );
  await expectTraceDetailTab(
    page,
    "Timeline",
    '[data-testid="trace-detail-timeline"]',
    "search_database",
  );
  await expectTraceDetailTab(
    page,
    "Payload",
    '[data-testid="trace-detail-payload"]',
    "query",
  );
  await expectText(
    page,
    '[data-testid="first-divergence-card"]',
    "TOOL_CALL search_database arguments differ",
    "first divergence card",
  );
  await expectText(page, '[data-testid="metric-divergences"]', "3", "divergence metric");
  await expectText(page, '[data-testid="integration-otel"]', "OpenTelemetry", "OTel integration card");
  await assertContrast(page, '[data-testid="studio-title"]', "light title");
  await assertContrast(page, '[data-testid="first-divergence-card"] h2', "light divergence heading");
  await assertContrast(page, ".payload-expected code", "light expected payload");
  await assertContrast(page, ".payload-actual code", "light actual payload");
  await assertContrast(page, '[data-testid="metric-divergences"]', "light divergence metric");
  await assertNoHorizontalOverflow(page, "desktop light");
  await page.screenshot({ path: path.join(screenshotDir, "desktop-light.png"), fullPage: true });

  await page.getByTestId("theme-toggle").click();
  const theme = await page.locator("html").getAttribute("data-theme");
  assert(theme === "dark", `Theme toggle did not set dark mode. Actual: ${theme}`);
  await assertContrast(page, '[data-testid="studio-title"]', "dark title");
  await assertContrast(page, '[data-testid="first-divergence-card"] h2', "dark divergence heading");
  await assertContrast(page, ".payload-expected code", "dark expected payload");
  await assertContrast(page, ".payload-actual code", "dark actual payload");
  await assertContrast(page, '[data-testid="metric-divergences"]', "dark divergence metric");
  await assertNoHorizontalOverflow(page, "desktop dark");
  await page.screenshot({ path: path.join(screenshotDir, "desktop-dark.png"), fullPage: true });

  await page.getByTestId("baseline-upload").setInputFiles(fixtures.baseline);
  await page.getByTestId("candidate-upload").setInputFiles(fixtures.candidate);
  await page.getByTestId("compare-button").click();
  await page.getByTestId("first-divergence-card").waitFor({ state: "visible" });
  await expectText(
    page,
    '[data-testid="comparison-summary"]',
    "refund_search_candidate_changed_tool_args.tbtrace",
    "comparison summary",
  );
  await expectText(
    page,
    '[data-testid="runs-table"]',
    "refund_search_candidate_changed_tool_args.tbtrace",
    "uploaded comparison in run history",
  );
  await page.getByTestId("run-filter-failing").click();
  await expectText(
    page,
    '[data-testid="runs-table"]',
    "refund_search_candidate_changed_tool_args.tbtrace",
    "failing run filter",
  );
  await page.getByTestId("save-regression-case").click();
  await expectText(
    page,
    '[data-testid="regression-case-library"]',
    "refund_search_candidate_changed_tool_args.tbtrace regression",
    "saved regression case",
  );
  await expectText(
    page,
    '[data-testid="regression-case-library"]',
    "changed_tool_args",
    "regression case divergence",
  );
  await page.getByTestId("regression-case-library").getByRole("button", { name: "Rerun" }).click();
  await expectText(page, '[data-testid="regression-case-library"]', "failing", "regression case run status");

  await page.getByTestId("copy-pytest").click();
  await page.getByTestId("copy-pytest").filter({ hasText: "Copied" }).waitFor({ state: "visible" });
  await expectText(page, '[data-testid="copy-pytest"]', "Copied", "copy button");

  await page.evaluate(() => {
    window.localStorage.setItem("tracebisect-theme", "light");
    document.documentElement.dataset.theme = "light";
  });
  await page.setViewportSize({ width: 390, height: 900 });
  await page.reload({ waitUntil: "load" });
  await page.getByTestId("studio-title").waitFor({ state: "visible" });
  await expectText(page, '[data-testid="studio-title"]', "Trace runs", "mobile product title");
  await assertNoHorizontalOverflow(page, "mobile light");
  await page.screenshot({ path: path.join(screenshotDir, "mobile-light.png"), fullPage: true });

  await page.getByTestId("theme-toggle").click();
  await assertNoHorizontalOverflow(page, "mobile dark");
  await page.screenshot({ path: path.join(screenshotDir, "mobile-dark.png"), fullPage: true });

  assert(consoleErrors.length === 0, `Browser console errors:\n${consoleErrors.join("\n")}`);
  await browser.close();
  console.log(`Studio visual verification passed at ${baseUrl}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
