// Browser journeys for Wave 3 (custom objects, field security, validation rules, sharing rules, config transfer):
// npm i playwright && node e2e/wave3-journeys.mjs <outdir>  (E2E_CHANNEL=msedge for Edge)
import { chromium } from "playwright";
import { mkdirSync, readFileSync, writeFileSync } from "fs";

const OUT = process.argv[2] ?? "./wave3-out"; mkdirSync(`${OUT}/shots`, { recursive: true });
const BASE = "http://localhost:3000", API = "http://localhost:8000/api/v1";
const RUN = Date.now().toString(36).slice(-5).replace(/^[0-9]/, "x");
const KEY = `survey_${RUN}`, LABEL = `Site survey ${RUN}`, PLURAL = `Site surveys ${RUN}`;
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
async function login(email) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 }, acceptDownloads: true });
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

const admin = await login("admin@cirra.demo");
const a = admin.page;
let ruleName = `Surveys need a result ${RUN}`, shareName = `EMEA to AEs ${RUN}`;

// ---- admin: define an object, its fields, field security and a validation rule ------------------------
await step("objects", "admin creates a custom object", async () => {
  await a.goto(`${BASE}/admin?tab=objects`); await a.waitForLoadState("networkidle");
  await a.fill("#ob-label", LABEL); await a.fill("#ob-plural", PLURAL); await a.fill("#ob-key", KEY);
  await a.fill("#ob-desc", "Pre-installation site checks");
  await a.getByRole("button", { name: "Create", exact: true }).click();
  await toast(a, "Object created");
  await a.locator("main").getByRole("link", { name: PLURAL }).waitFor();
  await shot(a, "admin-objects");
  return `${PLURAL} (${KEY})`;
}, a);

await step("objects", "admin adds typed fields to it", async () => {
  await a.goto(`${BASE}/admin?tab=fields`); await a.waitForLoadState("networkidle");
  for (const [label, key, type, options] of [["Result", "result", "select", "Pass, Fail"], ["Surveyor notes", "notes", "text", ""], ["Cost estimate", "cost", "number", ""]]) {
    await a.selectOption("#cf-e", { label: LABEL });
    await a.fill("#cf-l", label); await a.fill("#cf-k", key); await a.selectOption("#cf-t", type);
    if (options) await a.fill("#cf-o", options);
    await a.getByRole("button", { name: "Add", exact: true }).click();
    await toast(a, "Field added");
  }
  await a.locator("tbody tr", { hasText: LABEL }).filter({ hasText: "Cost estimate" }).waitFor();
  const rows = await a.locator("tbody tr", { hasText: LABEL }).count();
  expect(rows === 3, `${rows} rows`);
  return "Result (select), Surveyor notes (text), Cost estimate (number)";
}, a);

await step("security", "admin hides Cost estimate from Account Executives", async () => {
  const row = a.locator("tbody tr", { hasText: LABEL }).filter({ hasText: "Cost estimate" });
  await row.getByRole("button", { name: "Edit Cost estimate" }).click();
  const dlg = a.getByRole("dialog");
  await dlg.getByLabel("Account Executive access").selectOption("hidden");
  await dlg.getByLabel("SDR access").selectOption("read");
  await shot(a, "field-security");
  await dlg.getByRole("button", { name: "Save" }).click();
  await toast(a, "Field saved");
  await row.getByText("Account Executive: hidden").waitFor();
  return await row.locator("td").nth(5).innerText();
}, a);

await step("rules", "admin writes a validation rule and checks existing records", async () => {
  await a.goto(`${BASE}/admin?tab=rules`); await a.waitForLoadState("networkidle");
  await a.getByRole("button", { name: "New rule" }).click();
  const dlg = a.getByRole("dialog");
  await dlg.getByLabel("Record type").selectOption({ label: PLURAL });
  await dlg.locator("#vr-name").fill(ruleName);
  await dlg.getByRole("button", { name: "Add condition" }).click();
  await dlg.getByLabel("Filter field").selectOption({ label: "Result" });
  await dlg.getByLabel("Condition").selectOption({ label: "is empty" });
  await dlg.locator("#vr-msg").fill("Record the survey result before saving");
  await dlg.getByRole("button", { name: "Check existing records" }).click();
  await dlg.getByText(/existing records break this rule|No existing records/).waitFor();
  const check = await dlg.locator("p.rounded-md").innerText();
  await shot(a, "validation-rule");
  await dlg.getByRole("button", { name: "Save rule" }).click();
  await toast(a, "Rule saved");
  await a.locator("tbody").getByText(ruleName).waitFor();
  return check;
}, a);

// ---- a rep uses the object ------------------------------------------------------------------------------
const rep = await login("priya@cirra.demo");
const p = rep.page;
let recordUrl = "";
await step("objects", "rep finds the object in the sidebar and the rule blocks an incomplete record", async () => {
  await p.goto(`${BASE}/`); await p.waitForLoadState("networkidle");
  await p.locator("nav").getByRole("link", { name: PLURAL }).click();
  await p.waitForURL(new RegExp(`/objects/${KEY}$`));
  await p.getByRole("button", { name: `New ${LABEL.toLowerCase()}` }).click();
  const dlg = p.getByRole("dialog");
  await dlg.locator("#rec-name").fill(`Warehouse check ${RUN}`);
  await dlg.getByLabel("Account", { exact: true }).selectOption({ index: 1 });
  expect(await dlg.getByLabel("Cost estimate").count() === 0, "hidden field shown to AE");
  await dlg.locator("#cf-notes").fill("Loading dock needs a ramp");
  await dlg.getByRole("button", { name: `Create ${LABEL.toLowerCase()}` }).click();
  await toast(p, "Record the survey result before saving");
  await shot(p, "rule-blocks");
  await dlg.locator("#cf-result").selectOption("Pass");
  await dlg.getByRole("button", { name: `Create ${LABEL.toLowerCase()}` }).click();
  await p.waitForURL(/\/objects\/[a-z0-9_]+\/[0-9a-f-]{36}/);
  recordUrl = p.url();
  await p.getByRole("heading", { name: `Warehouse check ${RUN}` }).waitFor();
  await shot(p, "record-page");
  expect(rep.errors.length === 0, rep.errors.join(";"));
  return "blocked without Result, created with it";
}, p);

await step("objects", "rep edits a field on the record page", async () => {
  await p.getByRole("button", { name: "Edit fields" }).click();
  await p.locator("form").getByRole("textbox").first().fill("Ramp installed");
  await p.getByRole("button", { name: "Save", exact: true }).click();
  await toast(p, "Saved");
  await p.getByText("Ramp installed").waitFor();
  return "notes updated";
}, p);

await step("objects", "list page shows the record with sortable columns, and the account shows it as related", async () => {
  await p.goto(`${BASE}/objects/${KEY}`); await p.waitForLoadState("networkidle");
  await p.getByRole("link", { name: `Warehouse check ${RUN}` }).waitFor();
  const head = (await p.locator("thead").innerText()).replace(/\s+/g, " ");
  expect(!head.includes("Cost estimate"), "hidden column visible");
  await shot(p, "object-list");
  await p.goto(recordUrl); await p.waitForLoadState("networkidle");
  const accountLink = p.locator("main a[href^='/accounts/']").first();
  const accountName = await accountLink.innerText();
  await accountLink.click(); await p.waitForURL(/\/accounts\/[0-9a-f-]{36}/); await p.waitForLoadState("networkidle");
  await p.getByText("Related records").waitFor();
  await p.getByRole("link", { name: new RegExp(`Warehouse check ${RUN}`) }).waitFor();
  await shot(p, "account-related");
  return `columns: ${head}; related on ${accountName}`;
}, p);

// ---- sharing rule --------------------------------------------------------------------------------------
await step("sharing", "admin shares EMEA accounts with Account Executives after previewing the match", async () => {
  await a.goto(`${BASE}/admin?tab=sharing`); await a.waitForLoadState("networkidle");
  await a.getByRole("button", { name: "New sharing rule" }).click();
  const dlg = a.getByRole("dialog");
  await dlg.locator("#sr-name").fill(shareName);
  await dlg.getByRole("button", { name: "Add condition" }).click();
  await dlg.getByLabel("Filter field").selectOption({ label: "Region" });
  await dlg.getByLabel("Value").selectOption("EMEA");
  await dlg.getByLabel("Account Executive").check();
  await dlg.getByRole("button", { name: "Preview matching accounts" }).click();
  await dlg.getByText(/accounts? match today/).waitFor();
  const preview = await dlg.locator("p.rounded-md").innerText();
  await shot(a, "sharing-rule");
  const before = (await api(rep.token, "GET", "/accounts?limit=500")).data.length;
  await dlg.getByRole("button", { name: "Save rule" }).click();
  await toast(a, "Sharing rule saved");
  const after = (await api(rep.token, "GET", "/accounts?limit=500")).data.length;
  expect(after > before, `${before} -> ${after}`);
  return `${preview} Priya's accounts ${before} → ${after}`;
}, a);

// ---- config export / import ----------------------------------------------------------------------------
await step("config", "export the configuration and preview re-importing it", async () => {
  await a.goto(`${BASE}/admin?tab=config`); await a.waitForLoadState("networkidle");
  const [dl] = await Promise.all([a.waitForEvent("download"), a.getByRole("button", { name: "Export configuration" }).click()]);
  const file = `${OUT}/config-export.json`; await dl.saveAs(file);
  const bundle = JSON.parse(readFileSync(file, "utf8"));
  expect(bundle.custom_objects.some((o) => o.key === KEY) && bundle.validation_rules.some((r) => r.name === ruleName), "export misses new items");
  await a.getByLabel("Configuration file").setInputFiles(file);
  await a.getByText("preview (nothing changed yet)").waitFor();
  await a.getByText("Nothing would change.").waitFor();
  await shot(a, "config-preview");
  expect(admin.errors.length === 0, admin.errors.join(";"));
  return `${bundle.custom_objects.length} objects, ${bundle.custom_fields.length} fields, ${bundle.validation_rules.length} rules, ${bundle.workflows.length} workflows, ${bundle.reports.length} shared reports`;
}, a);

// ---- clean up so the demo data stays as it was -------------------------------------------------------------
const rules = (await api(admin.token, "GET", "/admin/validation-rules")).data.rules.filter((r) => r.name === ruleName);
for (const r of rules) await api(admin.token, "DELETE", `/admin/validation-rules/${r.id}`);
const shares = (await api(admin.token, "GET", "/admin/sharing-rules")).data.rules.filter((r) => r.name === shareName);
for (const r of shares) await api(admin.token, "DELETE", `/admin/sharing-rules/${r.id}`);
await api(admin.token, "DELETE", `/objects/${KEY}?confirm=true`);

await browser.close();
writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 1));
console.log(`\n${results.filter((r) => r.status === "pass").length}/${results.length} wave-3 checks passed`);
