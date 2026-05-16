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

async function assertFullScreenAppShell(page, label) {
  const shell = await page.evaluate(() => {
    const frame = document.querySelector(".dashboard-frame");
    const bodyStyle = getComputedStyle(document.body);
    if (!frame) return { found: false };
    const frameStyle = getComputedStyle(frame);
    const rect = frame.getBoundingClientRect();
    return {
      found: true,
      bodyBackground: bodyStyle.backgroundColor,
      frameBackground: frameStyle.backgroundColor,
      borderRadius: frameStyle.borderRadius,
      boxShadow: frameStyle.boxShadow,
      height: Math.round(rect.height),
      left: Math.round(rect.left),
      top: Math.round(rect.top),
      width: Math.round(rect.width),
      windowHeight: window.innerHeight,
      windowWidth: window.innerWidth,
    };
  });
  assert(shell.found, `${label} app shell was not rendered`);
  assert(shell.top === 0 && shell.left === 0, `${label} app shell is offset: ${JSON.stringify(shell)}`);
  assert(
    shell.width === shell.windowWidth,
    `${label} app shell is not full width: ${JSON.stringify(shell)}`,
  );
  assert(
    shell.height >= shell.windowHeight,
    `${label} app shell is shorter than the viewport: ${JSON.stringify(shell)}`,
  );
  assert(shell.borderRadius === "0px", `${label} app shell still has card radius: ${JSON.stringify(shell)}`);
  assert(shell.boxShadow === "none", `${label} app shell still has card shadow: ${JSON.stringify(shell)}`);
  assert(
    shell.bodyBackground === shell.frameBackground,
    `${label} body and app shell backgrounds differ: ${JSON.stringify(shell)}`,
  );
}

async function assertWorkbenchLayout(page, testId, label) {
  const layout = await page.evaluate((id) => {
    const grid = document.querySelector(`[data-testid="${id}"]`);
    if (!grid) return { found: false };
    const children = Array.from(grid.children).map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        bottom: Math.round(rect.bottom),
        height: Math.round(rect.height),
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        width: Math.round(rect.width),
      };
    });
    return {
      found: true,
      children,
      columns: getComputedStyle(grid).gridTemplateColumns.split(" ").length,
      innerWidth: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
    };
  }, testId);
  assert(layout.found, `${label} was not rendered`);
  assert(layout.columns === 3, `${label} should use three workbench columns. Actual: ${layout.columns}`);
  assert(layout.children.length === 3, `${label} should have three panes. Actual: ${layout.children.length}`);
  const [first, second, third] = layout.children;
  assert(
    Math.max(first.top, second.top, third.top) - Math.min(first.top, second.top, third.top) <= 1,
    `${label} panes are not top-aligned: ${JSON.stringify(layout.children)}`,
  );
  assert(
    Math.max(first.bottom, second.bottom, third.bottom) - Math.min(first.bottom, second.bottom, third.bottom) <= 1,
    `${label} panes are not height-aligned: ${JSON.stringify(layout.children)}`,
  );
  assert(
    layout.scrollWidth <= layout.innerWidth,
    `${label} introduced horizontal overflow: ${JSON.stringify(layout)}`,
  );
}

async function expectTraceDetailTab(page, tabName, selector, expectedText) {
  await page.getByTestId("trace-detail-tabs").getByRole("tab", { name: tabName }).click();
  await expectText(page, selector, expectedText, `${tabName} trace detail tab`);
}

async function assertTreeConnectors(page, label) {
  const counts = await page.evaluate(() => ({
    eventBranches: document.querySelectorAll(".observation-row .event-tree-guide-branch").length,
    eventContinuations: document.querySelectorAll(
      ".observation-row .event-tree-guide-continue, .observation-row .event-tree-guide-branch-open",
    ).length,
    traceBranches: document.querySelectorAll(".trace-tree-row .tree-guide-branch").length,
    traceContinuations: document.querySelectorAll(
      ".trace-tree-row .tree-guide-continue, .trace-tree-row .tree-guide-branch-open",
    ).length,
  }));
  assert(counts.traceBranches >= 3, `${label} trace tree connectors missing: ${JSON.stringify(counts)}`);
  assert(counts.traceContinuations >= 2, `${label} trace continuation lines missing: ${JSON.stringify(counts)}`);
  assert(counts.eventBranches >= 3, `${label} event-list connectors missing: ${JSON.stringify(counts)}`);
  assert(counts.eventContinuations >= 2, `${label} event-list continuation lines missing: ${JSON.stringify(counts)}`);
}

async function expectNotText(page, selector, text, label) {
  const locator = await expectOne(page, selector, label);
  const content = await locator.textContent();
  assert(!content?.includes(text), `${label} unexpectedly included "${text}". Actual: ${content}`);
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
  await assertFullScreenAppShell(page, "desktop light");
  await expectText(page, '[data-testid="studio-title"]', "Comparison history", "product title");
  const placeholder = await page.locator(".search-control input").getAttribute("placeholder");
  assert(
    placeholder === "Search comparisons, traces, events",
    `Search placeholder was not product-scoped. Actual: ${placeholder}`,
  );
  await expectText(page, ".sidebar", "Comparisons", "desktop sidebar labels");
  await expectText(page, '[data-testid="runs-table"]', "Refund search", "runs table");
  await expectText(page, '[data-testid="runs-table"]', "Tool arguments changed", "run divergence signal");
  await expectNotText(page, '[data-testid="runs-table"]', ".tbtrace", "runs table primary labels");
  await expectNotText(page, '[data-testid="runs-table"]', "changed_tool_args", "runs table primary labels");
  await assertWorkbenchLayout(page, "comparison-workbench", "comparison history workbench");
  await page.locator(".search-control input").fill("does-not-exist");
  await expectText(page, '[data-testid="runs-table"]', "No comparison runs match", "empty run filter");
  await page.getByTestId("clear-run-filters").click();
  await expectText(page, '[data-testid="runs-table"]', "Refund search", "cleared run filters");
  await page.getByTestId("sidebar-section-sources").click();
  await expectText(page, '[data-testid="studio-title"]', "Add trace sources", "sources title");
  await expectText(page, ".upload-panel", "Known-good baseline", "baseline upload label");
  await expectText(page, ".upload-panel", "New run to check", "candidate upload label");
  await expectText(page, ".upload-panel", "Find first behavior change", "compare action label");
  await expectText(page, '[data-testid="integration-otel"]', "OpenTelemetry", "OTel integration card");
  await assertNoHorizontalOverflow(page, "sources section");
  await page.getByTestId("sidebar-section-divergences").click();
  await expectText(page, '[data-testid="studio-title"]', "Review behavior changes", "divergences title");
  await expectText(page, '[data-testid="first-divergence-card"]', "search database", "divergences section");
  await assertWorkbenchLayout(page, "review-workbench", "regression review workbench");
  await assertNoHorizontalOverflow(page, "divergences section");
  await page.getByTestId("sidebar-section-cases").click();
  await expectText(page, '[data-testid="studio-title"]', "Regression guardrails", "cases title");
  await expectText(page, '[data-testid="regression-case-library"]', "Saved guardrails", "cases section");
  await assertNoHorizontalOverflow(page, "cases section");
  await page.getByTestId("sidebar-section-setup").click();
  await expectText(page, '[data-testid="studio-title"]', "Ship CI protection", "setup title");
  await expectText(page, '[data-testid="setup-section"]', "Operational checklist", "setup section");
  await expectText(page, '[data-testid="setup-section"]', "baseline trace", "setup baseline guidance");
  await assertNoHorizontalOverflow(page, "setup section");
  await page.getByTestId("sidebar-section-runs").click();
  await expectText(page, '[data-testid="studio-title"]', "Comparison history", "runs title after section navigation");
  await expectText(page, '[data-testid="trace-tree"]', "Tool call", "trace tree event type");
  await expectText(page, '[data-testid="trace-tree"]', "search_database", "trace tree event name");
  await expectText(page, '[data-testid="details-panel"]', "search_database", "details panel");
  await expectTraceDetailTab(
    page,
    "Summary",
    '[data-testid="trace-detail-metadata"]',
    "Run context",
  );
  await expectTraceDetailTab(
    page,
    "Event list",
    '[data-testid="trace-detail-observations"]',
    "search_database",
  );
  await assertTreeConnectors(page, "event hierarchy");
  await expectTraceDetailTab(
    page,
    "Timing",
    '[data-testid="trace-detail-timeline"]',
    "search_database",
  );
  await expectTraceDetailTab(
    page,
    "Raw payload",
    '[data-testid="trace-detail-payload"]',
    "query",
  );
  await page.getByTestId("sidebar-section-divergences").click();
  await expectText(
    page,
    '[data-testid="first-divergence-card"]',
    "search database used different tool arguments",
    "first divergence card",
  );
  await expectText(page, '[data-testid="metric-divergences"]', "3", "divergence metric");
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
  await assertFullScreenAppShell(page, "desktop dark");
  await assertContrast(page, '[data-testid="studio-title"]', "dark title");
  await assertContrast(page, '[data-testid="first-divergence-card"] h2', "dark divergence heading");
  await assertContrast(page, ".payload-expected code", "dark expected payload");
  await assertContrast(page, ".payload-actual code", "dark actual payload");
  await assertContrast(page, '[data-testid="metric-divergences"]', "dark divergence metric");
  await assertNoHorizontalOverflow(page, "desktop dark");
  await page.screenshot({ path: path.join(screenshotDir, "desktop-dark.png"), fullPage: true });

  await page.getByTestId("sidebar-section-sources").click();
  await page.getByTestId("baseline-upload").setInputFiles(fixtures.baseline);
  await page.getByTestId("candidate-upload").setInputFiles(fixtures.candidate);
  await page.getByTestId("compare-button").click();
  await page.getByTestId("sidebar-section-divergences").click();
  await page.getByTestId("first-divergence-card").waitFor({ state: "visible" });
  await expectText(
    page,
    '[data-testid="comparison-summary"]',
    "Refund baseline → Refund regression",
    "comparison summary",
  );
  await page.getByTestId("sidebar-section-runs").click();
  await expectText(
    page,
    '[data-testid="runs-table"]',
    "Refund search",
    "uploaded comparison in run history",
  );
  await page.getByTestId("run-filter-failing").click();
  await expectText(
    page,
    '[data-testid="runs-table"]',
    "Regression found",
    "failing run filter",
  );
  await page.getByTestId("sidebar-section-cases").click();
  await page.getByTestId("save-regression-case").click();
  await expectText(
    page,
    '[data-testid="regression-case-library"]',
    "Refund Regression Guardrail",
    "saved regression case",
  );
  await expectText(
    page,
    '[data-testid="regression-case-library"]',
    "Tool arguments changed",
    "regression case divergence",
  );
  await page.getByTestId("regression-case-library").getByRole("button", { name: "Rerun" }).first().click();
  await expectText(page, '[data-testid="regression-case-library"]', "Needs review", "regression case run status");

  await page.getByTestId("sidebar-section-setup").click();
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
  await assertFullScreenAppShell(page, "mobile light");
  await expectText(page, '[data-testid="studio-title"]', "Comparison history", "mobile product title");
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
