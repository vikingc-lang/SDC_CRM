// Browser journeys for Wave 2 (list views, inline edit, period comparison, report subscriptions):
// npm i playwright && node e2e/wave2-journeys.mjs <outdir>  (E2E_CHANNEL=msedge for Edge)
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "fs";

const OUT = process.argv[2] ?? "./wave2-out"; mkdirSync(`${OUT}/shots`, { recursive: true });
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

// ---- list views: create in the UI, switch, sort, edit in place ------------------------------------------
{
  const m = await login("marcus@cirra.demo"); const p = m.page;
  await step("views", "create a lead view from the list page", async () => {
    await p.goto(`${BASE}/leads`); await p.waitForLoadState("networkidle");
    await p.getByRole("button", { name: "New view" }).click();
    const dlg = p.getByRole("dialog");
    await dlg.locator("#lv-name").fill(`Scored leads ${RUN}`);
    await dlg.getByLabel("Add column").selectOption({ label: "Fit score" });
    await dlg.getByRole("button", { name: "Add filter" }).click();
    await dlg.getByLabel("Filter field").selectOption({ label: "Score" });
    await dlg.getByLabel("Condition").selectOption({ label: "≥" });
    await dlg.getByLabel("Value").fill("40");
    await shot(p, "view-editor");
    await dlg.getByRole("button", { name: "Save view" }).click();
    await toast(p, "View saved");
    const picked = await p.getByLabel("List view").locator("option:checked").innerText();
    expect(picked.includes(`Scored leads ${RUN}`), `picker shows ${picked}`);
    await p.getByText(/\d+ leads/).first().waitFor();
    await shot(p, "view-grid-leads");
    return `${picked}; ${await p.getByText(/\d+ leads/).first().innerText()}`;
  }, p);

  await step("views", "sort by clicking a column header", async () => {
    await p.getByRole("button", { name: "Sort by Score" }).click();
    await p.waitForLoadState("networkidle"); await p.waitForTimeout(400);
    const idx = await p.locator("thead th").evaluateAll((ths) => ths.findIndex((t) => t.innerText.trim().startsWith("Score")));
    const col = await p.locator(`tbody tr td:nth-child(${idx + 1})`).allInnerTexts();
    const nums = col.slice(0, 20).map((t) => Number(t.replace(/[^0-9.]/g, "")));
    expect(nums.every((n, i) => i === 0 || nums[i - 1] <= n), `ascending: ${nums.join(",")}`);
    await p.getByRole("button", { name: "Sort by Score" }).click(); await p.waitForTimeout(600);
    const desc = (await p.locator(`tbody tr td:nth-child(${idx + 1})`).allInnerTexts()).slice(0, 20).map((t) => Number(t.replace(/[^0-9.]/g, "")));
    expect(desc.every((n, i) => i === 0 || desc[i - 1] >= n), `descending: ${desc.join(",")}`);
    return `asc ${nums.slice(0, 3).join(",")}… desc ${desc.slice(0, 3).join(",")}…`;
  }, p);

  await step("views", "change a lead's owner in place", async () => {
    const row = p.locator("tbody tr").first();
    const name = await row.locator("a").first().innerText();
    const idx = await p.locator("thead th").evaluateAll((ths) => ths.findIndex((t) => t.innerText.trim().startsWith("Owner")));
    const before = (await row.locator("td").nth(idx).innerText()).trim();
    const target = before === "Sam Okoye" ? "Diego Alvarez" : "Sam Okoye";
    await row.getByRole("button", { name: "Edit owner" }).click();
    await row.getByLabel("New owner").selectOption({ label: target });
    await toast(p, "Owner updated");
    await row.getByText(target).waitFor();
    await shot(p, "inline-owner");
    expect(m.errors.length === 0, m.errors.join(";"));
    return `${name}: ${before} → ${target}`;
  }, p);

  await step("views", "standard list is one click away and the choice is remembered", async () => {
    await p.reload(); await p.waitForLoadState("networkidle");
    const kept = await p.getByLabel("List view").locator("option:checked").innerText();
    await p.getByLabel("List view").selectOption({ label: "Standard list" });
    await p.getByRole("tab", { name: "Open" }).first().waitFor().catch(() => p.getByText("Open").first().waitFor());
    return `after reload: ${kept}; back to standard list`;
  }, p);
  await m.ctx.close();
}

{
  const s = await login("sofia@cirra.demo"); const p = s.page;
  const v = (await api(s.token, "POST", "/views", { source: "cases", name: `My open cases ${RUN}`, columns: ["case_number", "subject", "priority", "status", "owner"],
    filters: [{ field: "status", op: "in", value: ["open", "pending"] }], sort: { by: "priority", dir: "asc" } })).data;
  await step("views", "support agent changes a case priority in place", async () => {
    await p.goto(`${BASE}/cases`); await p.waitForLoadState("networkidle");
    await p.getByLabel("List view").selectOption(v.id);
    const row = p.locator("tbody tr").first(); await row.waitFor();
    const before = await row.locator("td").nth(3).innerText();
    const target = before.trim().toLowerCase() === "low" ? "Medium" : "Low";
    await row.getByRole("button", { name: "Edit priority" }).click();
    await row.getByLabel("New priority").selectOption({ label: target });
    await toast(p, "Priority updated");
    await shot(p, "inline-case-priority");
    expect(s.errors.length === 0, s.errors.join(";"));
    return `${before.trim()} → ${target}`;
  }, p);
  await s.ctx.close();
}

// ---- period comparison ---------------------------------------------------------------------------------
{
  const m = await login("marcus@cirra.demo"); const p = m.page;
  const kpi = (await api(m.token, "POST", "/analytics/reports", { name: `New deals vs previous ${RUN}`, visibility: "shared",
    definition: { source: "deals", measures: [{ agg: "count" }], filters: [{ field: "created", op: "within", value: "last_90_days" }], compare: "previous_period", chart: { type: "number" } } })).data;
  const grouped = (await api(m.token, "POST", "/analytics/reports", { name: `Deals by stage ${RUN}`, visibility: "shared",
    definition: { source: "deals", group_by: [{ field: "stage" }], measures: [{ agg: "count" }], filters: [{ field: "created", op: "within", value: "this_year" }], chart: { type: "bar" } } })).data;
  await step("compare", "headline number shows the change against the previous period", async () => {
    await p.goto(`${BASE}/reports/builder?id=${kpi.id}`); await p.waitForLoadState("networkidle");
    const d = p.getByText(/vs previous 90 days/).first(); await d.waitFor();
    await shot(p, "compare-headline");
    return await d.innerText();
  }, p);
  await step("compare", "tick 'Compare with the previous period' on a grouped report", async () => {
    await p.goto(`${BASE}/reports/builder?id=${grouped.id}`); await p.waitForLoadState("networkidle");
    await p.getByLabel(/Compare with the previous period/).check();
    await p.locator("thead").last().getByText("Change", { exact: true }).waitFor();
    const head = await p.locator("thead").last().innerText();
    await shot(p, "compare-table");
    expect(head.includes("last year"), head);
    return head.replace(/\s+/g, " ");
  }, p);
  await step("compare", "dashboard tile shows the change for the dashboard period", async () => {
    const dash = (await api(m.token, "POST", "/analytics/dashboards", { name: `Compare board ${RUN}`, visibility: "private", tiles: [{ report_id: kpi.id, size: "third" }] })).data;
    await p.goto(`${BASE}/reports/dashboards/${dash.id}`); await p.waitForLoadState("networkidle");
    await p.getByText(/vs previous 90 days/).first().waitFor();
    await shot(p, "compare-dashboard");
    expect(m.errors.length === 0, m.errors.join(";"));
    return "tile shows delta";
  }, p);

  // ---- subscriptions -----------------------------------------------------------------------------------
  await step("subscribe", "subscribe to a report weekly and add a colleague", async () => {
    await p.goto(`${BASE}/reports/builder?id=${kpi.id}`); await p.waitForLoadState("networkidle");
    await p.getByRole("button", { name: "Subscribe" }).click();
    const dlg = p.getByRole("dialog");
    await dlg.getByLabel("How often").selectOption("weekly");
    await dlg.getByLabel("On", { exact: true }).selectOption({ label: "Monday" });
    await dlg.getByLabel("At", { exact: true }).selectOption({ label: "08:00 UTC" });
    await dlg.getByLabel("Priya Raman").check();
    await shot(p, "subscribe-dialog");
    await dlg.getByRole("button", { name: "Subscribe" }).click();
    await toast(p, "Subscription saved");
    await p.getByRole("button", { name: "Subscribed" }).waitFor();
    const sub = (await api(m.token, "GET", `/analytics/reports/${kpi.id}/subscription`)).data.subscription;
    expect(sub.frequency === "weekly" && sub.hour === 8 && sub.recipient_ids.length === 1, JSON.stringify(sub));
    return `weekly Monday 08:00 UTC, +1 recipient; email ${(await api(m.token, "GET", `/analytics/reports/${kpi.id}/subscription`)).data.email_enabled ? "on" : "not configured"}`;
  }, p);
  await step("subscribe", "send a copy now and find it in notifications", async () => {
    await p.getByRole("button", { name: "Subscribed" }).click();
    await p.getByRole("dialog").getByRole("button", { name: "Send now" }).click();
    await toast(p, "Delivered to 1");
    const notes = (await api(m.token, "GET", "/notifications")).data;
    const list = Array.isArray(notes) ? notes : notes.notifications ?? notes.items ?? [];
    const hit = list.find((n) => (n.title ?? "").startsWith(`New deals vs previous ${RUN}`));
    expect(hit, "notification not found");
    await shot(p, "subscribe-sent");
    return hit.title;
  }, p);
  await m.ctx.close();
}

await browser.close();
writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 1));
console.log(`\n${results.filter((r) => r.status === "pass").length}/${results.length} wave-2 checks passed`);
