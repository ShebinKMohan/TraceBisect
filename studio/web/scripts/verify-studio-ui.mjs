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

async function assertSidebarViewportLocked(page, label) {
  const lock = await page.evaluate(() => {
    const main = document.querySelector(".dashboard-main");
    const sidebar = document.querySelector(".sidebar");
    const profile = document.querySelector(".local-workspace-note");
    const settings = document.querySelector('[data-testid="sidebar-settings-link"]');
    if (main) main.scrollTop = main.scrollHeight;
    const sidebarRect = sidebar?.getBoundingClientRect();
    const profileRect = profile?.getBoundingClientRect();
    const settingsRect = settings?.getBoundingClientRect();
    return {
      bodyScrollHeight: document.body.scrollHeight,
      bodyClientHeight: document.body.clientHeight,
      mainScrollTop: Math.round(main?.scrollTop ?? 0),
      profileVisible:
        Boolean(profileRect) &&
        profileRect.top >= 0 &&
        profileRect.bottom <= window.innerHeight,
      settingsVisible:
        Boolean(settingsRect) &&
        settingsRect.top >= 0 &&
        settingsRect.bottom <= window.innerHeight,
      sidebarHeight: Math.round(sidebarRect?.height ?? 0),
      windowHeight: window.innerHeight,
    };
  });
  assert(
    lock.profileVisible && lock.settingsVisible,
    `${label} sidebar footer is not viewport pinned: ${JSON.stringify(lock)}`,
  );
  assert(
    Math.abs(lock.sidebarHeight - lock.windowHeight) <= 1,
    `${label} sidebar is not viewport height: ${JSON.stringify(lock)}`,
  );
  assert(
    lock.bodyScrollHeight <= lock.bodyClientHeight + 1,
    `${label} page body scrolls instead of dashboard main: ${JSON.stringify(lock)}`,
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
  assert(layout.children.length >= 1, `${label} should have rendered panes. Actual: ${layout.children.length}`);
  if (testId === "comparison-workbench") {
    assert(layout.columns === 2, `${label} should use reference two-column split. Actual: ${layout.columns}`);
    assert(layout.children.length === 3, `${label} should have list, inspector, and trace panes. Actual: ${layout.children.length}`);
    const [list, inspector, trace] = layout.children;
    assert(
      Math.abs(list.top - inspector.top) <= 1,
      `${label} list and inspector are not top-aligned: ${JSON.stringify(layout.children)}`,
    );
    assert(
      Math.abs(inspector.bottom - trace.top) <= 1,
      `${label} inspector and execution trace should stack without a gap: ${JSON.stringify(layout.children)}`,
    );
    assert(
      Math.abs(list.bottom - trace.bottom) <= 1,
      `${label} left list should span inspector plus trace: ${JSON.stringify(layout.children)}`,
    );
  }
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
  const counts = await page.evaluate(() => {
    const branch = document.querySelector(".trace-tree-row .tree-guide-branch");
    const row = document.querySelector(".trace-tree-row");
    return {
      traceBranches: document.querySelectorAll(".trace-tree-row .tree-guide-branch").length,
      traceRootRails: document.querySelectorAll(".trace-tree-row .tree-guide-root").length,
      traceContinuations: document.querySelectorAll(
        ".trace-tree-row .tree-guide-continue, .trace-tree-row .tree-guide-branch-open",
      ).length,
      traceArrows: document.querySelectorAll(".trace-tree-row .tree-arrow").length,
      traceConnectorColor: branch ? getComputedStyle(branch, "::before").backgroundColor : "",
      traceRowTransition: row ? getComputedStyle(row).transitionDuration : "0s",
    };
  });
  assert(counts.traceBranches >= 3, `${label} trace tree connectors missing: ${JSON.stringify(counts)}`);
  assert(counts.traceRootRails >= 1, `${label} trace root rail missing: ${JSON.stringify(counts)}`);
  assert(counts.traceContinuations >= 2, `${label} trace continuation lines missing: ${JSON.stringify(counts)}`);
  assert(counts.traceArrows >= 3, `${label} trace arrowheads missing: ${JSON.stringify(counts)}`);
  assert(
    counts.traceConnectorColor === "rgb(63, 142, 199)" || counts.traceConnectorColor === "rgb(98, 168, 232)",
    `${label} trace connector should use the reference blue rail: ${JSON.stringify(counts)}`,
  );
  assert(
    counts.traceRowTransition !== "0s",
    `${label} trace interaction animation missing: ${JSON.stringify(counts)}`,
  );
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
  await expectText(page, '[data-testid="studio-title"]', "Welcome to TraceBisect", "product title");
  await expectText(page, '[data-testid="home-page"]', "One workflow, three steps", "beginner workflow");
  await expectText(page, '[data-testid="home-page"]', "Plain-English glossary", "beginner glossary");
  await expectText(page, ".sidebar", "Home", "desktop sidebar labels");
  await expectText(page, ".sidebar", "Compare runs", "desktop sidebar labels");
  await expectText(page, ".sidebar", "Trace library", "desktop sidebar labels");
  await expectText(page, ".sidebar", "Sessions", "desktop sidebar labels");
  await expectText(page, ".sidebar", "Issues", "desktop sidebar labels");
  await expectText(page, ".sidebar", "Guardrails", "desktop sidebar labels");
  await expectText(page, ".sidebar", "Setup guide", "desktop sidebar labels");
  await page.getByTestId("sidebar-section-runs").click();
  await expectText(page, '[data-testid="studio-title"]', "Compare two runs", "comparison title");
  await expectText(page, '[data-testid="workflow-steps"]', "Choose traces", "comparison workflow");
  const placeholder = await page.locator(".search-control input").getAttribute("placeholder");
  assert(placeholder === "Search comparisons", `Search placeholder was not page-specific. Actual: ${placeholder}`);
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
  await expectText(page, '[data-testid="studio-title"]', "Choose your traces", "traces title");
  await expectText(page, '[data-testid="trace-table"]', "Captured traces", "trace table");
  await expectText(page, '[data-testid="trace-table"]', "gpt-4o-mini", "trace table model");
  await expectText(page, '[data-testid="trace-table"]', "refund_search:v3", "trace table prompt");
  await expectText(page, '[data-testid="trace-detail-panel"]', "Selected trace", "trace detail panel");
  await expectText(page, '[data-testid="trace-detail-panel"]', "Prompt version", "trace detail prompt metadata");
  await expectText(page, '[data-testid="trace-detail-panel"]', "Code SHA", "trace detail code metadata");
  await expectText(page, '[data-testid="trace-detail-panel"]', "Session", "trace detail session metadata");
  await page.getByTestId("trace-sort").selectOption("total_tokens");
  await expectText(page, '[data-testid="trace-detail-panel"]', "106", "trace detail follows sorted selection");
  await page.getByTestId("trace-filter-source").selectOption("native");
  await expectText(page, '[data-testid="trace-detail-panel"]', "TraceBisect", "trace source filter keeps selected detail");
  await expectText(page, ".upload-panel", "Known-good baseline", "baseline upload label");
  await expectText(page, ".upload-panel", "New run to check", "candidate upload label");
  await expectText(page, ".upload-panel", "Find first behavior change", "compare action label");
  await expectText(page, '[data-testid="integration-otel"]', "OpenTelemetry", "OTel integration card");
  await assertNoHorizontalOverflow(page, "sources section");
  await page.getByTestId("sidebar-section-sessions").click();
  await expectText(page, '[data-testid="studio-title"]', "Browse sessions", "sessions title");
  await expectText(page, '[data-testid="sessions-table"]', "Refund search", "sessions table");
  await expectText(page, '[data-testid="sessions-table"]', "Models / sources", "sessions model/source column");
  await expectText(page, '[data-testid="sessions-table"]', "Trace ID", "sessions expanded trace rows");
  await expectText(page, '[data-testid="sessions-table"]', "Success", "sessions status");
  await page.getByTestId("session-sort").selectOption("tokens");
  await expectText(page, '[data-testid="sessions-table"]', "Most tokens", "sessions sort control");
  const openSessionsBeforeToggle = await page.locator(".session-expanded").count();
  assert(openSessionsBeforeToggle >= 1, `Expected an expanded session group, found ${openSessionsBeforeToggle}`);
  await page.locator(".session-group-open .session-row").first().click();
  const openSessionsAfterCollapse = await page.locator(".session-expanded").count();
  assert(
    openSessionsAfterCollapse === openSessionsBeforeToggle - 1,
    `Session row did not collapse: before=${openSessionsBeforeToggle} after=${openSessionsAfterCollapse}`,
  );
  await page.locator(".session-row").first().click();
  await expectText(page, '[data-testid="sessions-table"]', "Trace ID", "sessions row re-expanded");
  await assertNoHorizontalOverflow(page, "sessions section");
  await page.getByTestId("sidebar-section-divergences").click();
  await expectText(page, '[data-testid="studio-title"]', "Review repeated issues", "issues title");
  await expectText(page, '[data-testid="issues-table"]', "Tool arguments changed", "issues table");
  await expectText(page, '[data-testid="issues-table"]', "Open issues", "issues summary");
  await expectText(page, '[data-testid="issues-table"]', "Failing comparisons", "issues failing comparisons");
  await expectText(page, '[data-testid="issues-table"]', "Behavior changes", "issues behavior changes");
  await expectText(page, '[data-testid="issues-table"]', "Frequency", "issues metric");
  await page.getByTestId("issue-sort").selectOption("frequency");
  await expectText(page, '[data-testid="issues-table"]', "Frequency", "issues sorted by frequency");
  await page.getByRole("button", { exact: true, name: "Critical" }).click();
  await expectText(page, '[data-testid="issues-table"]', "Tool arguments changed", "critical issue filter");
  const selectedIssueCards = await page.locator(".issue-card-active").count();
  assert(selectedIssueCards === 1, `Expected one selected issue card, found ${selectedIssueCards}`);
  await expectText(page, '[data-testid="review-workbench"]', "Tool arguments changed", "issues frame");
  await assertNoHorizontalOverflow(page, "divergences section");
  await page.getByTestId("sidebar-section-cases").click();
  await expectText(page, '[data-testid="studio-title"]', "Protect fixed behavior", "cases title");
  await expectText(page, '[data-testid="regression-case-library"]', "Saved guardrails", "cases section");
  const guardrailItems = await page.locator(".guardrail-list-item").count();
  if (guardrailItems > 0) {
    await expectText(page, '[data-testid="guardrail-detail-panel"]', "Selected guardrail", "guardrail selected detail");
    await expectText(page, '[data-testid="guardrail-detail-panel"]', "Saved versions", "guardrail saved version count");
    await expectText(page, '[data-testid="guardrail-detail-panel"]', "Pytest integration", "guardrail pytest integration");
    await expectText(page, '[data-testid="guardrail-detail-panel"]', "Latest saved result", "guardrail failure detail");
    await page.getByTestId("guardrail-sort").selectOption("severity");
    await expectText(page, '[data-testid="regression-case-library"]', "Highest risk", "guardrail sort control");
  } else {
    await expectText(page, '[data-testid="regression-case-library"]', "No saved guardrails yet", "guardrail empty state");
  }
  await assertNoHorizontalOverflow(page, "cases section");
  await page.getByTestId("sidebar-settings-link").click();
  await expectText(page, '[data-testid="studio-title"]', "Setup guide", "setup title");
  await expectText(page, '[data-testid="setup-section"]', "Running as a local workspace", "truthful setup mode");
  await expectText(page, '[data-testid="setup-section"]', "Choose the easiest way to start", "setup paths");
  await expectText(page, '[data-testid="setup-section"]', "What is not enabled yet", "setup boundary");
  await expectNotText(page, '[data-testid="setup-section"]', "tb_live_", "fake API keys");
  await assertNoHorizontalOverflow(page, "setup section");
  await assertSidebarViewportLocked(page, "settings section");

  const topbarThemeToggleCount = await page.getByTestId("theme-toggle").count();
  assert(topbarThemeToggleCount === 0, `Theme toggle leaked into topbar: ${topbarThemeToggleCount}`);
  const themeButton = page.getByTestId("profile-theme-toggle");
  assert((await themeButton.count()) === 1, "Expected one sidebar theme button");
  await themeButton.click();
  let theme = await page.locator("html").getAttribute("data-theme");
  assert(theme === "dark", `Theme toggle did not set dark mode. Actual: ${theme}`);
  await themeButton.click();
  theme = await page.locator("html").getAttribute("data-theme");
  assert(theme === "light", `Theme toggle did not restore light mode. Actual: ${theme}`);

  await page.getByTestId("sidebar-collapse-button").click();
  const collapsedShell = await page.evaluate(() => {
    const frame = document.querySelector(".dashboard-frame");
    const sidebar = document.querySelector(".sidebar");
    return {
      frameCollapsed: frame?.classList.contains("dashboard-frame-sidebar-collapsed") ?? false,
      sidebarCollapsed: sidebar?.classList.contains("sidebar-collapsed") ?? false,
      sidebarWidth: Math.round(sidebar?.getBoundingClientRect().width ?? 0),
    };
  });
  assert(
    collapsedShell.frameCollapsed && collapsedShell.sidebarCollapsed && collapsedShell.sidebarWidth <= 80,
    `Sidebar did not collapse cleanly: ${JSON.stringify(collapsedShell)}`,
  );
  await assertNoHorizontalOverflow(page, "collapsed sidebar");
  await page.getByTestId("sidebar-collapse-button").click();
  const expandedShell = await page.evaluate(() => {
    const frame = document.querySelector(".dashboard-frame");
    const sidebar = document.querySelector(".sidebar");
    return {
      frameCollapsed: frame?.classList.contains("dashboard-frame-sidebar-collapsed") ?? false,
      sidebarCollapsed: sidebar?.classList.contains("sidebar-collapsed") ?? false,
      sidebarWidth: Math.round(sidebar?.getBoundingClientRect().width ?? 0),
    };
  });
  assert(
    !expandedShell.frameCollapsed && !expandedShell.sidebarCollapsed && expandedShell.sidebarWidth >= 160,
    `Sidebar did not expand cleanly: ${JSON.stringify(expandedShell)}`,
  );

  await page.getByTestId("sidebar-section-runs").click();
  await expectText(page, '[data-testid="studio-title"]', "Compare two runs", "runs title after section navigation");
  await expectText(page, '[data-testid="trace-tree"]', "Tool call", "trace tree event type");
  await expectText(page, '[data-testid="trace-tree"]', "search_database", "trace tree event name");
  await expectText(page, '[data-testid="details-panel"]', "Expected · known-good run", "details panel");
  const duplicateEventListTabs = await page
    .getByTestId("trace-detail-tabs")
    .getByRole("tab", { name: "Event list" })
    .count();
  assert(duplicateEventListTabs === 0, `Inspector should not duplicate the execution trace as Event list: ${duplicateEventListTabs}`);
  await expectTraceDetailTab(
    page,
    "Run details",
    '[data-testid="trace-detail-metadata"]',
    "Run context",
  );
  await expectTraceDetailTab(
    page,
    "First change",
    '[data-testid="trace-detail-change"]',
    "Expected · known-good run",
  );
  await assertTreeConnectors(page, "event hierarchy");
  await expectTraceDetailTab(
    page,
    "Timeline",
    '[data-testid="trace-detail-timeline"]',
    "search_database",
  );
  await expectTraceDetailTab(
    page,
    "Raw data",
    '[data-testid="trace-detail-payload"]',
    "query",
  );
  await page.getByTestId("sidebar-section-divergences").click();
  await expectText(page, '[data-testid="issues-table"]', "Tool arguments changed", "issues card");
  await page.getByTestId("sidebar-section-runs").click();
  await assertContrast(page, '[data-testid="studio-title"]', "light title");
  await assertContrast(page, '[data-testid="trace-detail-change"] .change-card strong', "light divergence heading");
  await assertContrast(page, ".change-value-grid div:first-child pre", "light expected payload");
  await assertContrast(page, ".change-value-grid div:last-child pre", "light actual payload");
  await assertNoHorizontalOverflow(page, "desktop light");
  await page.screenshot({ path: path.join(screenshotDir, "desktop-light.png"), fullPage: true });

  await page.getByTestId("profile-theme-toggle").click();
  theme = await page.locator("html").getAttribute("data-theme");
  assert(theme === "dark", `Theme toggle did not set dark mode. Actual: ${theme}`);
  await assertFullScreenAppShell(page, "desktop dark");
  await assertContrast(page, '[data-testid="studio-title"]', "dark title");
  await assertContrast(page, '[data-testid="trace-detail-change"] .change-card strong', "dark divergence heading");
  await assertContrast(page, ".change-value-grid div:first-child pre", "dark expected payload");
  await assertContrast(page, ".change-value-grid div:last-child pre", "dark actual payload");
  await assertNoHorizontalOverflow(page, "desktop dark");
  await page.screenshot({ path: path.join(screenshotDir, "desktop-dark.png"), fullPage: true });

  await page.getByTestId("sidebar-section-sources").click();
  await page.getByTestId("baseline-upload").setInputFiles(fixtures.baseline);
  await page.getByTestId("candidate-upload").setInputFiles(fixtures.candidate);
  await page.getByTestId("compare-button").click();
  await page.getByTestId("sidebar-section-divergences").click();
  await expectText(page, '[data-testid="issues-table"]', "Tool arguments changed", "uploaded issue card");
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
  await page.getByTestId("regression-case-library").getByRole("button", { name: "Recheck saved trace" }).first().click();
  await expectText(page, '[data-testid="regression-case-library"]', "Needs review", "regression case run status");

  await page.evaluate(() => {
    window.localStorage.setItem("tracebisect-theme", "light");
    document.documentElement.dataset.theme = "light";
  });
  await page.setViewportSize({ width: 390, height: 900 });
  await page.reload({ waitUntil: "load" });
  await page.getByTestId("studio-title").waitFor({ state: "visible" });
  await assertFullScreenAppShell(page, "mobile light");
  await expectText(page, '[data-testid="studio-title"]', "Welcome to TraceBisect", "mobile product title");
  await expectText(page, ".mobile-nav", "Compare", "mobile navigation");
  await page.getByRole("button", { exact: true, name: "Compare" }).click();
  await expectText(page, '[data-testid="studio-title"]', "Compare two runs", "mobile comparison title");
  await expectText(page, '[data-testid="details-panel"]', "First behavior change", "mobile detail-first comparison");
  await assertNoHorizontalOverflow(page, "mobile light");
  await page.screenshot({ path: path.join(screenshotDir, "mobile-light.png"), fullPage: false });

  await page.evaluate(() => {
    window.localStorage.setItem("tracebisect-theme", "dark");
    document.documentElement.dataset.theme = "dark";
  });
  await assertNoHorizontalOverflow(page, "mobile dark");
  await page.screenshot({ path: path.join(screenshotDir, "mobile-dark.png"), fullPage: false });

  assert(consoleErrors.length === 0, `Browser console errors:\n${consoleErrors.join("\n")}`);
  await browser.close();
  console.log(`Studio visual verification passed at ${baseUrl}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
