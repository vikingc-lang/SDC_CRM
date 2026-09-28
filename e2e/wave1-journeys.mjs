// Browser journeys for the Wave 1 maturity pack: npm i playwright && node e2e/wave1-journeys.mjs <outdir>  (E2E_CHANNEL=msedge for Edge)
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "fs";

const OUT = process.argv[2] ?? "./wave1-out"; mkdirSync(`${OUT}/shots`, { recursive: true });
const BASE = "http://localhost:3000", API = "http://localhost:8000/api/v1";
const RUN = Date.now().toString(36).slice(-5);
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
async function login(email) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
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
  const r = await fetch(API + path, { method, headers: { Authorization: `Bearer ${token}`, "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const text = await r.text(); let data; try { data = JSON.parse(text); } catch { data = text; }
  return { status: r.status, data };
};
const toast = (page, text) => page.locator("[data-sonner-toast]", { hasText: text }).first().waitFor({ timeout: 15000 });
const shot = (page, name) => page.screenshot({ path: `${OUT}/shots/${name}.png` });

// ---- reports: drill-down and matrix --------------------------------------------------------------------
{
  const m = await login("marcus@cirra.demo"); const p = m.page;
  const byStatus = (await api(m.token, "POST", "/analytics/reports", { name: `Deals by status ${RUN}`, visibility: "shared",
    definition: { source: "deals", group_by: [{ field: "status" }], measures: [{ agg: "count" }], chart: { type: "bar" } } })).data;
  const matrix = (await api(m.token, "POST", "/analytics/reports", { name: `Pipeline x status ${RUN}`, visibility: "shared",
    definition: { source: "deals", group_by: [{ field: "pipeline" }, { field: "status" }], measures: [{ agg: "sum", field: "amount_usd" }], chart: { type: "matrix" } } })).data;
  await step("reports", "click a bar to see the records behind it, then open one", async () => {
    await p.goto(`${BASE}/reports/builder?id=${byStatus.id}`); await p.waitForLoadState("networkidle");
    await p.getByText("Click any bar, point, cell or row").waitFor();
    await p.locator("div.cursor-pointer", { hasText: "Won" }).first().click();
    const dlg = p.getByRole("dialog"); await dlg.getByText(/opportunities/).waitFor();
    const txt = await dlg.innerText();
    await shot(p, "drill-dialog");
    const link = dlg.locator("a").first(); const name = await link.innerText(); await link.click();
    await p.waitForURL(/\/deals\/[0-9a-f-]{36}/);
    return `${txt.match(/\d+ opportunities/)?.[0]}; opened ${name}`;
  }, p);
  await step("reports", "matrix (pivot) with totals; a cell drills down", async () => {
    await p.goto(`${BASE}/reports/builder?id=${matrix.id}`); await p.waitForLoadState("networkidle");
    await p.getByRole("button", { name: "Matrix (pivot)" }).waitFor();
    const table = p.locator("table", { hasText: "Pipeline / Status" }).first(); await table.waitFor();
    expect(await table.getByText("Total").count() >= 2, "totals missing");
    await shot(p, "matrix");
    await table.locator("td.cursor-pointer").first().click();
    await p.getByRole("dialog").getByText(/opportunities/).waitFor();
    await p.keyboard.press("Escape");
    return "matrix rendered, cell drill works";
  }, p);
  // ---- dashboards: period / owner filters -------------------------------------------------------------------
  const dash = (await api(m.token, "POST", "/analytics/dashboards", { name: `Wave1 dash ${RUN}`, visibility: "shared",
    tiles: [{ report_id: byStatus.id, size: "half" }, { report_id: matrix.id, size: "half" }] })).data;
  await step("dashboards", "owner and period filters re-run every tile; bars drill down", async () => {
    await p.goto(`${BASE}/reports/dashboards/${dash.id}`); await p.waitForLoadState("networkidle");
    const before = await p.locator("main").innerText();
    await p.getByLabel("Owner").selectOption({ label: "Priya Raman" }); await p.waitForTimeout(1500); await p.waitForLoadState("networkidle");
    await p.getByLabel("Period").selectOption("this_year"); await p.waitForTimeout(1500); await p.waitForLoadState("networkidle");
    const after = await p.locator("main").innerText();
    expect(before !== after, "tiles didn't change with the filters");
    await shot(p, "dashboard-filters");
    await p.locator("div.cursor-pointer").first().click();
    await p.getByRole("dialog").getByText(/opportunities/).waitFor();
    return "filters applied; drill opened";
  }, p);
  // ---- custom fields reach the builder -----------------------------------------------------------------------
  await m.ctx.close();
  const a = await login("admin@cirra.demo"); const q = a.page;
  await step("custom fields", "a new custom field is immediately reportable", async () => {
    const r = await api(a.token, "POST", "/admin/custom-fields", { entity: "deal", key: `region_code_${RUN}`, label: `Sales region ${RUN}`, field_type: "select", options: ["North", "South"] });
    expect(r.status === 201, `create ${r.status}`);
    const cat = (await api(a.token, "GET", "/analytics/sources")).data;
    const f = cat.sources.find((s) => s.key === "deals").fields.find((x) => x.key === `cf_region_code_${RUN}`);
    expect(f && f.type === "enum", "not in catalogue");
    await q.goto(`${BASE}/reports/builder`); await q.waitForLoadState("networkidle");
    const opts = await q.locator("select option").allInnerTexts();
    expect(opts.some((o) => o.includes(`Sales region ${RUN}`)), "not offered in the builder");
    return "offered as a grouping / filter field";
  }, q);
  // ---- workflows: Slack / webhook actions ---------------------------------------------------------------------
  await step("workflows", "Slack action form, save, test run says what it would post", async () => {
    await q.goto(`${BASE}/admin/workflows/new`); await q.waitForLoadState("networkidle");
    await q.locator("#wf-name").fill(`Slack big deals ${RUN}`);
    await q.getByLabel("Step type").first().selectOption("post_message");
    await q.getByLabel("Incoming webhook URL").fill("https://hooks.slack.com/services/T000/B000/XYZ");
    await q.getByLabel("Message").last().fill("{{title}} is now {{stage}}");
    await shot(q, "workflow-slack");
    await q.getByRole("button", { name: /Save/ }).first().click();
    await q.waitForURL(/\/admin\/workflows\/[0-9a-f-]{36}/, { timeout: 15000 });
    await q.getByRole("button", { name: "Test", exact: true }).click();
    await q.getByText(/Would post to Slack/).first().waitFor();
    return "saved; dry run: would post to Slack";
  }, q);
  await step("integration", "API & webhooks shows the integration endpoints reference", async () => {
    await q.goto(`${BASE}/admin?tab=developer`); await q.getByText("Integration endpoints").waitFor();
    await q.getByText("PUT /api/v1/upsert/{accounts|contacts|deals|leads}/{external_id}").waitFor();
  }, q);
  expect(a.errors.length === 0, a.errors.join(";"));
  await a.ctx.close();
}

// ---- bulk actions on every list --------------------------------------------------------------------------------
async function bulkJourney(email, path, action, valueLabel, n, expectText) {
  const s = await login(email); const p = s.page;
  await step("bulk", `${path}: select ${n}, ${action} → ${valueLabel ?? "first campaign"}`, async () => {
    await p.goto(`${BASE}${path}`); await p.waitForLoadState("networkidle");
    const boxes = p.locator("tbody input[type=checkbox]");
    await boxes.first().waitFor();
    for (let i = 0; i < n; i++) await boxes.nth(i).check();
    const bar = p.getByRole("toolbar", { name: "Bulk actions" }); await bar.getByText(`${n} selected`).waitFor();
    await bar.getByLabel("Bulk action").selectOption({ label: action });
    await bar.getByLabel("Value").selectOption(valueLabel ? { label: valueLabel } : { index: 1 });
    await shot(p, `bulk-${path.replace("/", "")}`);
    await bar.getByRole("button", { name: `Apply to ${n}` }).click();
    await toast(p, expectText);
    expect(s.errors.length === 0, s.errors.join(";"));
    return await p.locator("[data-sonner-toast]").first().innerText();
  }, p);
  await s.ctx.close();
}
await bulkJourney("marcus@cirra.demo", "/leads", "Assign owner", "Sam Okoye", 2, "updated");
await bulkJourney("marcus@cirra.demo", "/accounts", "Change tier", "Enterprise", 1, "updated");
await bulkJourney("nina@cirra.demo", "/contacts", "Add to campaign", null, 2, "updated");
await bulkJourney("sofia@cirra.demo", "/cases", "Change priority", "High", 1, "updated");

await browser.close();
writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 1));
console.log(`\n${results.filter((r) => r.status === "pass").length}/${results.length} wave-1 checks passed`);
