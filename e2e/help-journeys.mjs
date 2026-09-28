// Browser journeys for the help center and Aiden's help answers.
// npm i playwright && node e2e/help-journeys.mjs <outdir>  (E2E_CHANNEL=msedge for Edge)
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "fs";

const OUT = process.argv[2] ?? "./help-out"; mkdirSync(`${OUT}/shots`, { recursive: true });
const BASE = "http://localhost:3000", API = "http://localhost:8000/api/v1";
const results = [];
const browser = await chromium.launch(process.env.E2E_CHANNEL ? { channel: process.env.E2E_CHANNEL } : {});
const expect = (c, m) => { if (!c) throw new Error(m); };

async function step(area, name, fn, page) {
  const t0 = Date.now();
  try {
    const detail = await fn();
    results.push({ area, name, status: "pass", ms: Date.now() - t0, detail: detail ?? "" });
    console.log("PASS", area, "›", name, detail ?? "");
  } catch (e) {
    if (page) await page.screenshot({ path: `${OUT}/shots/fail-${results.length + 1}.png`, fullPage: true }).catch(() => {});
    results.push({ area, name, status: "fail", ms: Date.now() - t0, detail: String(e.message ?? e).split("\n")[0].slice(0, 400) });
    console.log("FAIL", area, "›", name, "->", String(e.message ?? e).split("\n")[0].slice(0, 300));
  }
}
async function login(email, viewport = { width: 1440, height: 950 }) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const errors = [];
  page.on("response", (r) => { if (r.url().startsWith(API) && r.status() >= 500) errors.push(`${r.status()} ${r.url()}`); });
  page.on("pageerror", (e) => errors.push(String(e.message)));
  await page.goto(`${BASE}/login`);
  await page.fill("input[type=email]", email); await page.fill("input[type=password]", "cirra123");
  await page.click("button[type=submit]"); await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
  return { ctx, page, errors };
}
const shot = (page, name) => page.screenshot({ path: `${OUT}/shots/${name}.png` });

const ae = await login("priya@cirra.demo");
const p = ae.page;

await step("drawer", "the header help button shows help for the current page", async () => {
  await p.goto(`${BASE}/pipeline`); await p.waitForLoadState("networkidle");
  await p.getByRole("button", { name: "Help", exact: true }).click();
  const drawer = p.getByRole("dialog");
  await drawer.getByText("On this page: Pipeline, deals & stage gates").waitFor();
  await drawer.getByRole("button", { name: "How do I move a deal to the next stage?" }).click();
  await drawer.getByText("Drag the card, or open the deal and choose the stage").waitFor();
  await shot(p, "drawer-pipeline");
  await p.keyboard.press("Escape");
}, p);

await step("drawer", "the ? key opens help, and Aiden answers a how-to with steps and links", async () => {
  await p.goto(`${BASE}/tasks`); await p.waitForLoadState("networkidle");
  await p.evaluate(() => (document.activeElement instanceof HTMLElement ? document.activeElement.blur() : null));
  await p.keyboard.press("?");
  const drawer = p.getByRole("dialog");
  await drawer.getByText("Ask Aiden how to do something").waitFor();
  await drawer.locator("#help-ask").fill("How do I connect my calendar?");
  await drawer.getByRole("button", { name: "Ask", exact: true }).click();
  await drawer.getByText(/Open Settings/).first().waitFor({ timeout: 20000 });
  await drawer.getByText("From the help center").waitFor();
  await shot(p, "drawer-aiden");
  await drawer.getByRole("link", { name: "Open screen" }).first().click();
  await p.waitForURL(/\/tasks|\/settings/, { timeout: 10000 });
}, p);

await step("drawer", "help search finds how-tos and glossary entries", async () => {
  await p.getByRole("button", { name: "Help", exact: true }).click();
  const drawer = p.getByRole("dialog");
  await drawer.locator("#help-search").fill("MEDDPICC");
  await drawer.getByText("Metrics, Economic buyer").first().waitFor();
  await p.keyboard.press("Escape");
}, p);

await step("center", "your role: mission, a typical day, live permissions and a checklist", async () => {
  await p.goto(`${BASE}/help`); await p.waitForLoadState("networkidle");
  await p.getByText("You are a Account Executive").or(p.getByText(/You are an? Account Executive/)).first().waitFor();
  await p.getByText("What you can do").waitFor();
  await p.getByRole("cell", { name: "Opportunities" }).waitFor();
  await p.locator("#help-check-0").check();
  await p.getByText("1 of 4 done").waitFor();
  await shot(p, "center-role");
  await p.locator("#help-check-0").uncheck();
}, p);

await step("center", "capabilities lead to an area with how-tos", async () => {
  await p.getByRole("button", { name: "Capabilities" }).or(p.getByRole("tab", { name: "Capabilities" })).first().click();
  await p.getByRole("link", { name: /Products, quotes & approvals/ }).click();
  await p.waitForURL(/\/help\/areas\/cpq/);
  await p.getByRole("button", { name: "How do I create a quote?" }).click();
  await p.getByText("Add lines and discounts").waitFor();
  await p.getByRole("link", { name: "Lead to cash" }).waitFor();
}, p);

await step("center", "a process map step opens its screen", async () => {
  await p.goto(`${BASE}/help?tab=processes&p=case`); await p.waitForLoadState("networkidle");
  const map = p.getByRole("img", { name: "Process map: Customer case" });
  await map.waitFor();
  await shot(p, "center-process");
  await map.getByRole("link", { name: /^4\. Investigate & reply/ }).click();
  await p.waitForURL(/\/cases/, { timeout: 10000 });
}, p);

await step("center", "the data model explorer shows fields and related records", async () => {
  await p.goto(`${BASE}/help?tab=model`); await p.waitForLoadState("networkidle");
  await p.getByRole("img", { name: "Account and related records" }).waitFor();
  await p.getByRole("button", { name: /^Opportunity(\+\d+)?$/ }).first().click();
  await p.getByRole("img", { name: "Opportunity and related records" }).waitFor();
  await p.getByText(/^Amount/).first().waitFor();
  await shot(p, "center-model");
}, p);

await step("aiden", "Aiden's panel answers how-to questions with help links", async () => {
  await p.goto(`${BASE}/`); await p.waitForLoadState("networkidle");
  await p.getByRole("button", { name: /Aiden/ }).first().click();
  await p.getByRole("button", { name: "How do I add products to a deal?" }).click();
  await p.getByText("From the help center").waitFor({ timeout: 20000 });
  await shot(p, "aiden-help");
}, p);

const sofia = await login("sofia@cirra.demo");
await step("roles", "a support agent gets their own guide", async () => {
  await sofia.page.goto(`${BASE}/help`); await sofia.page.waitForLoadState("networkidle");
  await sofia.page.getByText("Resolve customer cases within their SLA").waitFor();
  await sofia.page.getByRole("link", { name: "Set yourself available" }).waitFor();
}, sofia.page);

await step("mobile", "no horizontal scroll on the help pages", async () => {
  const m = await login("marcus@cirra.demo", { width: 390, height: 844 });
  for (const path of ["/help", "/help?tab=processes", "/help?tab=model", "/help/areas/pipeline"]) {
    await m.page.goto(`${BASE}${path}`); await m.page.waitForLoadState("networkidle");
    const over = await m.page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(over <= 1, `${path} overflows by ${over}px`);
  }
  await m.ctx.close();
});

const errors = [ae, sofia].flatMap((s) => s.errors);
results.push({ area: "health", name: "no server errors or page crashes", status: errors.length ? "fail" : "pass", detail: errors.slice(0, 5).join(" | ") });
console.log(errors.length ? `FAIL health › ${errors.slice(0, 5).join(" | ")}` : "PASS health › no server errors or page crashes");
writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 1));
console.log(`\n${results.filter((r) => r.status === "pass").length}/${results.length} help journey checks passed`);
await browser.close();
