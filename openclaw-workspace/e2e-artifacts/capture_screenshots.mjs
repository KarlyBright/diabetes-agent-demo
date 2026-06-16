import { chromium } from 'playwright';
import path from 'node:path';

const outDir = '/d/OpenProject/diabetes-agent-demo/diabetes-agent-demo/diabetes-agent-demo/openclaw-workspace/e2e-artifacts';
const baseUrl = 'http://127.0.0.1:5173';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1800 }, deviceScaleFactor: 1 });

async function wait(ms) { return new Promise(r => setTimeout(r, ms)); }
async function safeScreenshot(locator, fileName) {
  const filePath = path.join(outDir, fileName);
  await locator.screenshot({ path: filePath });
  return filePath;
}

const results = [];
let pageOpened = false;
try {
  await page.goto(baseUrl, { waitUntil: 'networkidle', timeout: 30000 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  pageOpened = true;

  await page.locator('header h1').waitFor({ state: 'visible', timeout: 15000 });

  const dashboardPath = path.join(outDir, '01-dashboard-full.png');
  await page.screenshot({ path: dashboardPath, fullPage: true });
  results.push({ key: 'dashboard', path: dashboardPath });

  const chatInput = page.locator('input[placeholder="输入消息..."]');
  await chatInput.fill('我今天早餐吃了两个馒头和一杯豆浆，请给我简短建议。');
  await page.locator('button.send-btn').click();
  await page.locator('.message.user .message-text').last().waitFor({ state: 'visible', timeout: 10000 });
  await page.locator('.message.assistant .message-text').last().waitFor({ state: 'visible', timeout: 20000 });
  await wait(1200);

  const chatPanel = page.locator('.chat-section');
  results.push({ key: 'chat', path: await safeScreenshot(chatPanel, '02-chat-panel.png') });

  const mealTextarea = page.locator('textarea[placeholder*="请输入您吃了什么"]');
  await mealTextarea.fill('早餐吃了两个馒头、一杯豆浆和一个鸡蛋');
  await page.locator('button.analyze-btn').click();
  await page.locator('.meal-section .result-area').waitFor({ state: 'visible', timeout: 15000 });
  await wait(1000);
  results.push({ key: 'meal', path: await safeScreenshot(page.locator('.meal-section'), '03-meal-analysis.png') });

  results.push({ key: 'glucose', path: await safeScreenshot(page.locator('.right-panel .panel-card').nth(0), '04-glucose-card.png') });

  const adviceCard = page.locator('.right-panel .panel-card').nth(3);
  await adviceCard.scrollIntoViewIfNeeded();
  await wait(800);
  results.push({ key: 'advice', path: await safeScreenshot(adviceCard, '05-advice-card.png') });

  const medicationCard = page.locator('.right-panel .panel-card').nth(1);
  results.push({ key: 'medication', path: await safeScreenshot(medicationCard, '06-medication-card.png') });

  console.log(JSON.stringify({ pageOpened, results }, null, 2));
} catch (error) {
  console.log(JSON.stringify({ pageOpened, error: String(error), results }, null, 2));
  process.exitCode = 1;
} finally {
  await browser.close();
}
