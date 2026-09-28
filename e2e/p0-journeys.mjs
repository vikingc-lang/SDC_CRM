// Browser journeys for the P0 depth work: AI governance and agent approvals, opportunity products / team / splits,
// tax on quotes, exchange rates, multi-step workflows, language and region, calendar sync settings.
// npm i playwright && node e2e/p0-journeys.mjs <outdir>  (E2E_CHANNEL=msedge for Edge)
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "fs";

const OUT = process.argv[2] ?? "./p0-out"; mkdirSync(`${OUT}/shots`, { recursive: true });
const BASE = "http://localhost:3000", API = "http://localhost:8000/api/v1";
const RUN = Date.now().toString(36).slice(-5);
const results = [];
process.on("unhandledRejection", (e) => console.log("(ignored late rejection)", String(e).slice(0, 160)));
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
  const token = await page.evaluate(() => localStorage.getItem("cirra.token"));
  return { ctx, page, token, errors };
}
const api = async (token, method, path, body) => {
  const r = await fetch(API + path, { method, headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined });
  const text = await r.text(); let data; try { data = JSON.parse(text); } catch { data = text; }
  return { status: r.status, data };
};
const toast = (page, text) => page.locator("[data-sonner-toast]", { hasText: text }).first().waitFor({ timeout: 15000 });
const shot = (page, name) => page.screenshot({ path: `${OUT}/shots/${name}.png` });

const admin = await login("admin@cirra.demo");
const priya = await login("priya@cirra.demo");
const a = admin.page, p = priya.page;
const originalAi = (await api(admin.token, "GET", "/ai/governance")).data;
const originalTax = (await api(admin.token, "GET", "/finance/tax")).data.policy;
const deals = (await api(priya.token, "GET", "/deals")).data;
const priyaId = (await api(priya.token, "GET", "/users/me")).data.id;
const diegoToken = (await api(null, "POST", "/auth/login", { username: "diego@cirra.demo", password: "cirra123" })).data.access_token;
let deal = null;  // hers, priced in USD, on an account Diego can't otherwise see (so only the team grants him access)
for (const d of deals.filter((d) => !d.is_won && !d.is_lost && d.currency === "USD" && d.owner?.id === priyaId)) {
  if ((await api(diegoToken, "GET", `/deals/${d.id}`)).status === 404) { deal = d; break; }
}
const diego = (await api(admin.token, "GET", "/users")).data.find((u) => u.email === "diego@cirra.demo");

// ---- AI governance ---------------------------------------------------------------------------------------------
await step("ai", "admin sees AI spend, trust signals and changes a budget", async () => {
  await a.goto(`${BASE}/admin?tab=ai`); await a.waitForLoadState("networkidle");
  await a.getByText("AI spend this month").waitFor();
  await a.getByText("Personal data masked").waitFor();
  await a.fill("#ai-user_daily_calls", "250");
  await a.getByRole("button", { name: "Save", exact: true }).first().click();
  await toast(a, "AI policy saved");
  const pol = (await api(admin.token, "GET", "/ai/governance")).data.policy;
  expect(pol.user_daily_calls === 250, `daily calls ${pol.user_daily_calls}`);
  await shot(a, "ai-governance");
}, a);

await step("ai", "admin sets the stage assistant to need approval", async () => {
  await a.selectOption("#agent-stage_assistant-mode", "approve");
  await a.getByRole("button", { name: "Save", exact: true }).nth(1).click();  // the agents card (the budgets card's Save comes first)
  await toast(a, "Agent permissions saved");
  const pol = (await api(admin.token, "GET", "/ai/governance")).data.agents.policy;
  expect(pol.stage_assistant.mode === "approve", "mode not saved");
}, a);

await step("ai", "an overdue deal gets a close-date suggestion the owner approves in the inbox", async () => {
  const past = new Date(Date.now() - 12 * 86400000).toISOString().slice(0, 10);
  expect((await api(priya.token, "PATCH", `/deals/${deal.id}`, { target_close_date: past })).status === 200, "could not backdate");
  await api(admin.token, "POST", "/admin/jobs/risk_scan");
  await p.goto(`${BASE}/approvals?tab=ai`); await p.waitForLoadState("networkidle");
  const card = p.locator("div.rounded-lg", { hasText: `Move the close date of ${deal.title}` }).first();
  await card.waitFor({ timeout: 15000 });
  await card.getByText("Close date:").waitFor();
  await shot(p, "ai-suggestion");
  await card.getByRole("button", { name: "Approve" }).click();
  await toast(p, "Approved and applied");
  const d = (await api(priya.token, "GET", `/deals/${deal.id}`)).data;
  expect(d.target_close_date > past, `close date still ${d.target_close_date}`);
}, p);

// ---- opportunity products, team and splits --------------------------------------------------------------------
await step("deal", "owner adds products and the deal amount follows them", async () => {
  await p.goto(`${BASE}/deals/${deal.id}`); await p.waitForLoadState("networkidle");
  const card = p.locator("div.rounded-lg", { has: p.getByText("Products", { exact: true }) }).first();
  await card.getByRole("button", { name: /Add products|Edit/ }).first().click();
  await card.getByText("Add product", { exact: true }).click();
  const product = card.locator("#dl-0-product");
  const plat = (await api(priya.token, "GET", "/products")).data.find((x) => x.sku === "CIR-PLAT");  // add-ons require the platform (product rule)
  await product.selectOption(plat.id);
  await card.locator("#dl-0-qty").fill("5");
  await card.locator("#dl-from-lines").check();
  await card.getByRole("button", { name: "Save" }).click();
  await toast(p, "Products saved");
  const d = (await api(priya.token, "GET", `/deals/${deal.id}`)).data;
  expect(d.line_items.length === 1 && d.amount_source === "lines" && Math.abs(d.amount - d.line_items_total) < 0.01, `amount ${d.amount} vs ${d.line_items_total}`);
  await shot(p, "deal-products");
}, p);

await step("deal", "owner adds a teammate with read access and a 70/30 revenue split", async () => {
  const team = p.locator("div.rounded-lg", { has: p.getByText("Deal team", { exact: true }) }).first();
  await team.getByRole("button").first().click();
  await team.locator("#team-add-user").selectOption(diego.id);
  await team.locator("#team-add-role").selectOption("Sales Engineer");
  await team.getByRole("button", { name: "Add", exact: true }).click();
  await team.getByText("Diego Alvarez").waitFor();
  await team.getByRole("button", { name: "Edit" }).click();
  await team.getByText("Add split").click();
  await team.locator("#split-0-pct").fill("70");
  await team.getByText("Add split").click();
  await team.locator("#split-1-user").selectOption(diego.id);
  await team.locator("#split-1-pct").fill("30");
  await team.getByRole("button", { name: "Save" }).click();
  await toast(p, "Splits saved");
  const d = (await api(priya.token, "GET", `/deals/${deal.id}`)).data;
  expect(d.splits.length === 2 && d.team.some((m) => m.user.id === diego.id), JSON.stringify(d.splits));
  await shot(p, "deal-team");
}, p);

const diegoS = await login("diego@cirra.demo");
await step("deal", "the read-only teammate can open the deal but not edit it", async () => {
  await diegoS.page.goto(`${BASE}/deals/${deal.id}`); await diegoS.page.waitForLoadState("networkidle");
  await diegoS.page.getByText(deal.title).first().waitFor();
  const team = diegoS.page.locator("div.rounded-lg", { has: diegoS.page.getByText("Deal team", { exact: true }) }).first();
  await team.getByText("Sales Engineer · read only").waitFor();
  expect(await diegoS.page.locator("div.rounded-lg", { has: diegoS.page.getByText("Products", { exact: true }) }).getByRole("button", { name: "Edit" }).count() === 0, "edit shown");
  expect((await api(diegoS.token, "PATCH", `/deals/${deal.id}`, { po_number: "X" })).status === 403, "patch allowed");
}, diegoS.page);

// ---- tax on quotes ---------------------------------------------------------------------------------------------
await step("tax", "a quote from the deal's products shows UK VAT", async () => {
  expect((await api(admin.token, "PUT", "/finance/tax", { policy: { engine: "builtin", seller: { country: "US" } } })).status === 200, "tax policy");
  await api(priya.token, "PATCH", `/deals/${deal.id}`, { bill_to: { line1: "1 High Street", city: "London", country: "United Kingdom" } });
  await p.goto(`${BASE}/deals/${deal.id}`); await p.waitForLoadState("networkidle");
  await p.getByRole("button", { name: "Create quote" }).click();
  await p.waitForURL(/\/quotes\//, { timeout: 15000 });
  await p.getByText("VAT 20%").waitFor({ timeout: 15000 });
  await p.getByText("Total incl. tax").waitFor();
  await shot(p, "quote-tax");
}, p);

// ---- exchange rates and the tax tool ---------------------------------------------------------------------------
await step("fx", "finance adds a dated exchange rate and previews German VAT", async () => {
  await a.goto(`${BASE}/admin?tab=tax`); await a.waitForLoadState("networkidle");
  await a.fill("#fx-cur", "SEK"); await a.fill("#fx-rate", "0.0951");
  await a.getByRole("button", { name: "Add rate" }).click();
  await toast(a, "Rate saved");
  await a.locator("td", { hasText: /^SEK$/ }).first().waitFor();
  await a.fill("#tax-try-country", "DE");
  await a.getByRole("button", { name: "Calculate" }).click();
  await a.getByText("VAT 19%:").waitFor();
  await shot(a, "tax-currency");
}, a);

// ---- multi-step workflows -----------------------------------------------------------------------------------------
let wfId = "";
await step("workflows", "admin builds a workflow with a wait and a branch", async () => {
  await a.goto(`${BASE}/admin/workflows/new`); await a.waitForLoadState("networkidle");
  await a.fill("#wf-name", `Nurture ${RUN}`);
  await a.selectOption("#wf-s0-type", "wait");
  await a.fill("#wf-s0-days", "3");
  await a.getByText("Add step", { exact: true }).last().click();
  await a.selectOption("#wf-s1-type", "branch");
  await a.locator("#wf-s1then0-type").waitFor();
  await a.getByRole("textbox").nth(2).fill("Renewal");  // the branch condition's value (after name and description)
  await a.fill("#wf-s1then0-msg", "{{title}} is ready for the next touch");
  await a.getByRole("button", { name: "Save" }).click();
  await toast(a, `Saved "Nurture ${RUN}"`);
  await a.waitForURL((u) => !u.pathname.endsWith("/new"), { timeout: 15000 });
  wfId = a.url().split("/").pop();
  const rule = (await api(admin.token, "GET", `/workflows/${wfId}`)).data;
  expect(rule.actions[0].type === "wait" && rule.actions[0].days === 3 && rule.actions[1].type === "branch", JSON.stringify(rule.actions).slice(0, 200));
  await a.locator("#wf-stop-unmatched").waitFor();
  await shot(a, "workflow-steps");
}, a);

// ---- language, region and calendar -------------------------------------------------------------------------------
const sam = await login("sam@cirra.demo");
await step("i18n", "a user switches to German and the app follows", async () => {
  const s = sam.page;
  await s.goto(`${BASE}/settings`); await s.waitForLoadState("networkidle");
  await s.selectOption("#pref-locale", "de-DE");
  await s.locator("[data-sonner-toast]", { hasText: /Einstellungen gespeichert|Preferences saved/ }).first().waitFor({ timeout: 15000 });
  await s.getByRole("link", { name: "Aufgaben" }).waitFor();
  await s.getByText("Sprache und Region").waitFor();
  await s.getByText(/1\.234\.567,89\s€/).first().waitFor();
  await shot(s, "settings-german");
  await s.goto(`${BASE}/pipeline`); await s.waitForLoadState("networkidle");
  await s.getByRole("link", { name: "Start" }).waitFor();
  await s.goto(`${BASE}/settings`); await s.waitForLoadState("networkidle");
  await s.selectOption("#pref-locale", "");
  await s.getByRole("link", { name: "Tasks" }).waitFor({ timeout: 15000 });
}, sam.page);

await step("calendar", "calendar sync lists both providers and says when they aren't set up", async () => {
  const s = sam.page;
  await s.getByText("Google Calendar", { exact: true }).first().waitFor();
  await s.getByText("Microsoft 365 / Outlook", { exact: true }).first().waitFor();
  expect(await s.getByText("Not set up on this server").count() === 2, "providers unexpectedly configured");
}, sam.page);

await step("mobile", "no horizontal scroll on the new screens", async () => {
  const m = await login("admin@cirra.demo", { width: 390, height: 844 });
  for (const path of ["/admin?tab=ai", "/admin?tab=tax", `/deals/${deal.id}`, "/approvals?tab=ai", "/settings"]) {
    await m.page.goto(`${BASE}${path}`); await m.page.waitForLoadState("networkidle");
    const over = await m.page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(over <= 1, `${path} overflows by ${over}px`);
  }
  await m.ctx.close();
});

// ---- restore -------------------------------------------------------------------------------------------------------
await api(admin.token, "PUT", "/ai/governance", { policy: originalAi.policy });
await api(admin.token, "PUT", "/ai/agents/policy", { policy: originalAi.agents.policy });
await api(admin.token, "PUT", "/finance/tax", { policy: originalTax });
await api(priya.token, "PUT", `/deals/${deal.id}/splits`, { splits: [] });
await api(priya.token, "DELETE", `/deals/${deal.id}/team/${diego.id}`);
await api(priya.token, "PUT", `/deals/${deal.id}/products`, { lines: [], amount_source: "manual" });
if (wfId) await api(admin.token, "DELETE", `/workflows/${wfId}`);

const errors = [admin, priya, diegoS, sam].flatMap((s) => s.errors);
results.push({ area: "health", name: "no server errors or page crashes", status: errors.length ? "fail" : "pass", detail: errors.slice(0, 5).join(" | ") });
console.log(errors.length ? `FAIL health › ${errors.slice(0, 5).join(" | ")}` : "PASS health › no server errors or page crashes");
writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 1));
console.log(`\n${results.filter((r) => r.status === "pass").length}/${results.length} P0 journey checks passed`);
await browser.close();
