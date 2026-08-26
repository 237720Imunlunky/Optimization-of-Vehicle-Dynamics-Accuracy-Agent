/** 使用本机Edge完成Agent界面的桌面、交互和手机尺寸验收。 */

import { chromium } from "playwright-core";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const UI_ROOT = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.dirname(UI_ROOT);
const OUTPUT_ROOT = path.join(PROJECT_ROOT, "输出", "Agent交互界面", "视觉验收", "iteration_005");
const EDGE_PATH = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const TARGET_URL = "http://127.0.0.1:8765";
let activeBrowser = null;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function pageLayoutAudit(page, viewportName) {
  /** 检查横向溢出、核心元素尺寸和图表实际像素。 */
  const result = await page.evaluate(() => {
    const canvas = document.querySelector("#score-chart");
    const context = canvas.getContext("2d");
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let coloredPixels = 0;
    for (let index = 0; index < pixels.length; index += 16) {
      if (pixels[index + 3] > 0 && (pixels[index] < 245 || pixels[index + 1] < 245 || pixels[index + 2] < 245)) coloredPixels += 1;
    }
    const cards = [...document.querySelectorAll(".metric-card")].map((element) => {
      const rect = element.getBoundingClientRect();
      return { width: rect.width, height: rect.height };
    });
    return {
      viewportWidth: window.innerWidth,
      bodyScrollWidth: document.documentElement.scrollWidth,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      coloredChartPixels: coloredPixels,
      cardSizes: cards,
      activeViewVisible: document.querySelector('.view.active').getBoundingClientRect().width > 0,
    };
  });
  assert(!result.horizontalOverflow, `${viewportName}存在横向溢出`);
  assert(result.coloredChartPixels > 200, `${viewportName}精度图表为空白`);
  assert(result.cardSizes.every((item) => item.width > 0 && item.height >= 115), `${viewportName}指标卡尺寸异常`);
  assert(result.activeViewVisible, `${viewportName}主视图不可见`);
  return result;
}

async function run() {
  await mkdir(path.dirname(OUTPUT_ROOT), { recursive: true });
  await mkdir(OUTPUT_ROOT, { recursive: false });
  const browser = await chromium.launch({ executablePath: EDGE_PATH, headless: true });
  activeBrowser = browser;
  const consoleErrors = [];
  const desktop = await browser.newContext({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 });
  const page = await desktop.newPage();
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  await page.goto(TARGET_URL, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.querySelector("#score-overall")?.textContent !== "--");

  assert((await page.locator("#score-overall").textContent()) === "92.97", "综合精度未加载当前最优值");
  assert((await page.locator("#side-api").textContent()) === "待配置", "API未配置状态显示错误");
  const desktopAudit = await pageLayoutAudit(page, "桌面端");
  await page.screenshot({ path: path.join(OUTPUT_ROOT, "desktop_dashboard.png"), fullPage: true });

  await page.getByTestId("nav-control").click();
  await page.locator('input[name="run-mode"][value="dry_run"]').check({ force: true });
  assert(await page.getByTestId("start-job-button").isEnabled(), "干运行按钮不可用");
  await page.getByTestId("start-job-button").click();
  await page.waitForFunction(() => document.querySelector("#job-status-badge")?.textContent === "RUNNING", null, { timeout: 5000 });
  await page.waitForFunction(() => ["COMPLETED", "FAILED"].includes(document.querySelector("#job-status-badge")?.textContent || ""), null, { timeout: 30000 });
  assert((await page.locator("#job-status-badge").textContent()) === "COMPLETED", "界面干运行未完成");
  await page.screenshot({ path: path.join(OUTPUT_ROOT, "desktop_control_completed.png"), fullPage: true });

  await page.getByTestId("nav-settings").click();
  const configPath = await page.locator("#config-path").textContent();
  assert(configPath.includes("llm_api.local.json"), "API手动配置路径未展示");
  await page.screenshot({ path: path.join(OUTPUT_ROOT, "desktop_api_settings.png"), fullPage: true });

  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
  const mobilePage = await mobile.newPage();
  mobilePage.on("console", (message) => { if (message.type() === "error") consoleErrors.push(`mobile: ${message.text()}`); });
  mobilePage.on("pageerror", (error) => consoleErrors.push(`mobile: ${error.message}`));
  await mobilePage.goto(TARGET_URL, { waitUntil: "networkidle" });
  await mobilePage.waitForFunction(() => document.querySelector("#score-overall")?.textContent !== "--");
  const mobileAudit = await pageLayoutAudit(mobilePage, "手机端");
  const navRect = await mobilePage.locator(".sidebar").boundingBox();
  assert(navRect && navRect.y <= 1 && navRect.height <= 64, "手机端顶部导航位置异常");
  await mobilePage.screenshot({ path: path.join(OUTPUT_ROOT, "mobile_dashboard.png"), fullPage: true });

  await browser.close();
  activeBrowser = null;
  assert(consoleErrors.length === 0, `浏览器控制台错误：${consoleErrors.join(" | ")}`);
  const result = {
    target: TARGET_URL,
    desktop: desktopAudit,
    mobile: mobileAudit,
    consoleErrors,
    interactions: {
      dashboardLoaded: true,
      navigationPassed: true,
      dryRunCompleted: true,
      apiPathVisible: true,
    },
    screenshots: ["desktop_dashboard.png", "desktop_control_completed.png", "desktop_api_settings.png", "mobile_dashboard.png"],
  };
  await writeFile(path.join(OUTPUT_ROOT, "qa_result.json"), JSON.stringify(result, null, 2), "utf8");
  await writeFile(path.join(OUTPUT_ROOT, "README.md"), [
    "# Agent交互界面视觉验收", "",
    "本目录保存桌面看板、任务完成状态、API配置页和手机看板截图。",
    "`qa_result.json`记录横向溢出、图表像素、控件交互和控制台错误检查结果。", "",
    "运行方式：在服务启动后，于`Agent交互界面`目录执行`node visual_qa.mjs`。",
  ].join("\n"), "utf8");
  console.log(JSON.stringify(result));
}

run().catch(async (error) => {
  if (activeBrowser) await activeBrowser.close();
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
