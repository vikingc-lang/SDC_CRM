// Browser E2E for the Cirra lead-to-order flow (62 checks, 12 journeys).
// Needs the API on :8000 and the web app on :3000 with freshly seeded demo data, plus Playwright:
//   npm i playwright && node e2e/lead-to-order.mjs ./e2e-output
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "fs";

const OUT = process.argv[2]; mkdirSync(`${OUT}/e2e`, { recursive: true });
const BASE = "http://localhost:3000", API = "http://localhost:8000/api/v1", PW = "cirra123";
const RUN = Date.now().toString(36).slice(-5);
const results = [];
let current = "";
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" }).catch(() => chromium.launch());

async function step(journey, name, fn, page) {
  const t0 = Date.now(); current = `${journey} › ${name}`;
  try {
    const detail = await fn();
    results.push({ journey, name, status: "pass", ms: Date.now() - t0, detail: detail ?? "" });
    console.log("PASS", current, detail ?? "");
    return true;
  } catch (e) {
    const shot = page ? `e2e/fail-${results.length + 1}.png` : null;
    if (page && shot) await page.screenshot({ path: `${OUT}/${shot}`, fullPage: true }).catch(() => {});
    results.push({ journey, name, status: "fail", ms: Date.now() - t0, detail: String(e.message ?? e).split("\n")[0].slice(0, 300), screenshot: shot });
    console.log("FAIL", current, String(e.message ?? e).split("\n")[0]);
    return false;
  }
}
const blocked = (journey, name, why) => { results.push({ journey, name, status: "blocked", ms: 0, detail: why }); console.log("BLOCKED", journey, name); };
const expect = (cond, msg) => { if (!cond) throw new Error(msg); };

async function login(email) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/login`);
  await page.fill("input[type=email]", email); await page.fill("input[type=password]", PW);
  await page.click("button[type=submit]"); await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 15000 });
  const token = await page.evaluate(() => localStorage.getItem("cirra.token"));
  return { ctx, page, token };
}
const api = async (token, method, path, body) => {
  const r = await fetch(API + path, { method, headers: { Authorization: `Bearer ${token}`, "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const text = await r.text(); let data; try { data = JSON.parse(text); } catch { data = text; }
  return { status: r.status, data };
};
const toast = (page, text) => page.locator("[data-sonner-toast]", { hasText: text }).first().waitFor({ timeout: 12000 });
const moveTo = async (page, stage) => { await page.locator(`button[title="Move to ${stage}"]`).click(); };

// ---------------------------------------------------------------------------------------------------------------
const company = `Nordlicht Maschinenbau ${RUN}`, email = `anna.keller@nordlicht-${RUN}.de`;
let leadId, dealId, quoteId, orderFormUrl, orderId;

// J1 web form capture ---------------------------------------------------------------------------------------------
{
  const J = "1. Lead capture (public web form)";
  const ctx = await browser.newContext({ viewport: { width: 900, height: 900 } }); const p = await ctx.newPage();
  await step(J, "Hosted form loads for a valid key", async () => {
    await p.goto(`${BASE}/forms/cf_demo_webform_cirra`);
    await p.getByRole("heading", { name: "Website demo form" }).waitFor();
  }, p);
  await step(J, "Invalid key shows a friendly error", async () => {
    const q = await ctx.newPage(); await q.goto(`${BASE}/forms/not-a-key`);
    await q.getByText("This form is no longer available").waitFor(); await q.close();
  }, p);
  await step(J, "Prospect submits the form with consent", async () => {
    await p.fill("#f-first_name", "Anna"); await p.fill("#f-last_name", "Keller"); await p.fill("#f-email", email);
    await p.fill("#f-company_name", company); await p.fill("#f-job_title", "VP Operations"); await p.fill("#f-country", "Germany");
    await p.fill("#f-employee_count", "1200"); await p.fill("#f-message", "Need CPQ integrated with SAP");
    await p.getByLabel("I agree to receive product updates").check();
    await p.getByRole("button", { name: "Submit" }).click();
    await p.getByText("Thank you").waitFor();
  }, p);
  await ctx.close();
}

// J2 lead workspace ------------------------------------------------------------------------------------------------
const mgr = await login("marcus@cirra.demo");
{
  const J = "2. Lead qualification & scoring"; const p = mgr.page;
  await step(J, "New lead appears in the Leads list", async () => {
    await p.goto(`${BASE}/leads`); await p.fill('input[placeholder="Name, email or company"]', company);
    await p.getByRole("link", { name: "Anna Keller" }).click(); await p.waitForURL(/\/leads\/.+/);
    leadId = p.url().split("/").pop();
  }, p);
  await step(J, "Lead is normalised, enriched, consented and routed", async () => {
    await p.getByText("Germany").first().waitFor();
    const txt = await p.locator("main").innerText();
    expect(txt.includes("EMEA"), "region EMEA not derived"); expect(/Granted · GDPR/.test(txt), "GDPR consent not recorded");
    expect(/owned by (Priya Raman|Diego Alvarez)/.test(txt), "not routed by the EMEA round-robin rule");
    return txt.match(/owned by [A-Za-z ]+/)[0];
  }, p);
  await step(J, "Engagement events raise the score and promote to MQL", async () => {
    for (const ev of ["webinar_attended", "pricing_page_visit", "meeting_booked"]) {
      await p.locator("form:has(button:text-is('Log')) select").selectOption(ev);
      await p.getByRole("button", { name: "Log", exact: true }).click(); await toast(p, "Engagement logged");
      await p.waitForTimeout(400);
    }
    await p.reload(); await p.getByText("MQL", { exact: true }).first().waitFor();
    return "score " + (await p.locator("span.tabular").first().innerText());
  }, p);
  await step(J, "Conversion is blocked below the BANT minimum", async () => {
    const r = await api(mgr.token, "POST", `/leads/${leadId}/convert`, { deal_title: "x" });
    expect(r.status === 422 && /BANT/.test(r.data.detail), `expected 422 BANT, got ${r.status}`);
    return r.data.detail;
  }, p);
  await step(J, "BANT 4/4 with evidence makes the lead an SQL", async () => {
    for (const k of ["Budget", "Authority", "Need", "Timeline"]) await p.getByRole("button", { name: k, exact: true }).click();
    await p.locator('input[placeholder="Evidence (who said what, when)"]').first().fill("FY27 budget confirmed by CFO");
    await p.getByRole("button", { name: "Save qualification" }).click(); await toast(p, "Sales qualified");
  }, p);
}

// J3 conversion -----------------------------------------------------------------------------------------------------
{
  const J = "3. Conversion to account, contact, opportunity"; const p = mgr.page;
  await step(J, "Convert dialog creates all three and opens the opportunity", async () => {
    await p.getByRole("button", { name: "Convert" }).first().click();
    const d = p.getByRole("dialog");
    await d.locator("#cv-title").fill(`${company}: Revenue platform`); await d.locator("#cv-amt").fill("150000");
    await d.locator("#cv-owner").selectOption({ label: "Priya Raman" }); await d.locator("#cv-role").selectOption("Economic Buyer");
    await d.getByRole("button", { name: "Convert" }).click();
    await p.waitForURL(/\/deals\/.+/); dealId = p.url().split("/").pop();
    await p.getByText("Enterprise Solution Sale").first().waitFor();
  }, p);
  await step(J, "Deal starts in Discovery with the lead history carried over", async () => {
    await p.getByText(/Lead converted: Anna Keller/).first().waitFor();
    const r = await api(mgr.token, "GET", `/deals/${dealId}`);
    expect(r.data.stage === "Discovery", `stage ${r.data.stage}`); expect(r.data.owner.full_name === "Priya Raman", "owner not Priya");
    return `${r.data.stage}, owner ${r.data.owner.full_name}`;
  }, p);
  await step(J, "Lead page shows converted with links", async () => {
    await p.goto(`${BASE}/leads/${leadId}`); await p.getByText(/Converted/).first().waitFor();
    await p.getByRole("link", { name: "Opportunity" }).waitFor();
  }, p);
}

// J4 opportunity stages -----------------------------------------------------------------------------------------------
{
  const J = "4. Solution-sale stages & gates"; const p = mgr.page;
  await step(J, "Gate blocks Solution Design until evidence exists", async () => {
    await p.goto(`${BASE}/deals/${dealId}`); await moveTo(p, "Solution Design / Demo");
    await p.getByText("Entry criteria for Solution Design / Demo").waitFor();
    const unmet = await p.getByRole("dialog").getByText("Not yet").count();
    await p.getByRole("button", { name: "Cancel" }).click();
    expect(unmet >= 2, "expected unmet criteria"); return `${unmet} unmet criteria shown`;
  }, p);
  // test data: buying committee + meeting notes (the UI paths for these are covered by the pre-existing suite)
  const d = (await api(mgr.token, "GET", `/deals/${dealId}`)).data;
  for (const [first, role] of [["Jonas", "Champion"], ["Mia", "Evaluator"], ["Felix", "Legal Counsel"]])
    await api(mgr.token, "POST", "/contacts", { account_id: d.account.id, first_name: first, last_name: "Keller", email: `${first.toLowerCase()}@nordlicht-${RUN}.de`, buying_role: role });
  await api(mgr.token, "POST", "/activities", { account_id: d.account.id, deal_id: dealId, activity_type: "meeting", subject: "Solution workshop",
    summary: "Pain: manual quoting bottleneck. PoC scope agreed for SAP integration. Business case shows 11-month payback, budget approved." });
  await api(mgr.token, "PATCH", `/deals/${dealId}`, { target_close_date: new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10) });
  for (const stage of ["Solution Design / Demo", "Technical Evaluation / PoC", "Business Case Validation"]) {
    await step(J, `Moves to ${stage} once gates are met`, async () => {
      await p.reload(); await moveTo(p, stage); await toast(p, `Moved to ${stage}`);
    }, p);
  }
  await step(J, "Negotiation & Legal is blocked without an approved quote", async () => {
    await p.reload(); await moveTo(p, "Negotiation & Legal");
    await p.getByText("Entry criteria for Negotiation & Legal").waitFor();
    const txt = await p.getByRole("dialog").innerText(); await p.getByRole("button", { name: "Cancel" }).click();
    expect(/Approved quote[^\n]*\n?\s*Not yet/.test(txt) || txt.includes("Not yet"), "gate not shown");
  }, p);
}

// J5 CPQ & approvals ---------------------------------------------------------------------------------------------------
{
  const J = "5. CPQ, deal desk & approval chain"; const p = mgr.page;
  await step(J, "New quote from the deal becomes the primary quote", async () => {
    await p.goto(`${BASE}/deals/${dealId}`); await p.getByRole("button", { name: "New quote" }).click();
    await p.waitForURL(/\/quotes\/.+/); quoteId = p.url().split("/").pop();
    await p.getByText("Primary", { exact: true }).waitFor();
  }, p);
  await step(J, "Bundle rule: Ambient AI alone is rejected (requires Platform)", async () => {
    const r = await api(mgr.token, "PUT", `/quotes/${quoteId}`, { lines: [{ product_id: (await api(mgr.token, "GET", "/products")).data.find((x) => x.sku === "CIR-AI").id, quantity: 10, discount_pct: 0 }] });
    expect(r.status === 422 && /requires/.test(r.data.detail), `got ${r.status}`); return r.data.detail;
  }, p);
  await step(J, "Rep builds lines, regional price book and promo apply", async () => {
    await p.reload();
    await p.getByRole("button", { name: "Add line" }).click(); await p.getByRole("button", { name: "Add line" }).click();
    const prods = p.getByLabel("Product"); await prods.nth(0).selectOption({ label: "Cirra Platform · CIR-PLAT" }); await prods.nth(1).selectOption({ label: "Ambient AI add-on · CIR-AI" });
    const qty = p.getByLabel("Quantity"); await qty.nth(0).fill("150"); await qty.nth(1).fill("150");
    await p.getByLabel("Discount %").nth(0).fill("25");
    await p.locator("select").filter({ hasText: "USD" }).first().selectOption("EUR");
    await p.getByPlaceholder("e.g. LAUNCH-AI").fill("LAUNCH-AI");
    await p.getByRole("button", { name: "Save" }).click(); await toast(p, "Quote saved");
    await p.getByText("Priced by the server").waitFor();
    const txt = await p.locator("main").innerText();
    expect(txt.includes("regional:EMEA 2026 regional"), "EMEA regional price book not applied");
    expect(/promo −15%/.test(txt), "LAUNCH-AI promo not applied");
    return "EMEA price book + LAUNCH-AI 15% applied";
  }, p);
  await step(J, "Submit routes to Sales Manager then Deal Desk (sequential)", async () => {
    await p.getByRole("button", { name: "Submit for approval" }).click(); await toast(p, "Submitted for approval");
    await p.reload(); await p.getByText("1. Sales Manager").waitFor(); await p.getByText("2. Deal Desk").waitFor();
    expect(await p.getByText("queued").count() >= 1, "later level not queued");
  }, p);
  const dana = await login("dana@cirra.demo");
  await step(J, "Deal Desk cannot decide before the Sales Manager", async () => {
    await dana.page.goto(`${BASE}/approvals`);
    const card = dana.page.locator("div.p-4", { hasText: company }).filter({ hasText: "Deal Desk" });
    await card.getByText("Waiting for an earlier level").waitFor();
  }, dana.page);
  await step(J, "Sales Manager approves level 1 in the Approvals inbox", async () => {
    await p.goto(`${BASE}/approvals`);
    const card = p.locator("div.p-4", { hasText: company }).filter({ hasText: "Level 1" });
    await card.getByRole("button", { name: "Approve" }).click(); await toast(p, "Approved");
  }, p);
  await step(J, "Deal Desk approves level 2; quote becomes approved", async () => {
    await dana.page.reload();
    const card = dana.page.locator("div.p-4", { hasText: company }).filter({ hasText: "Level 2" });
    await card.getByRole("button", { name: "Approve" }).click(); await toast(dana.page, "Approved");
    const q = (await api(mgr.token, "GET", `/quotes/${quoteId}`)).data; expect(q.status === "approved", `quote ${q.status}`);
    return `TCV ${q.currency} ${q.tcv.toLocaleString()}`;
  }, dana.page);
  await dana.ctx.close();
  await step(J, "Negotiation & Legal now opens (approved quote + Legal Counsel)", async () => {
    await p.goto(`${BASE}/deals/${dealId}`); await moveTo(p, "Negotiation & Legal"); await toast(p, "Moved to Negotiation & Legal");
  }, p);
}

// J6 negotiation & signature ----------------------------------------------------------------------------------------------
{
  const J = "6. Negotiation, redlines & e-signature"; const p = mgr.page;
  let msaId;
  await step(J, "Generate an MSA from the deal", async () => {
    await p.goto(`${BASE}/deals/${dealId}`); await p.getByRole("button", { name: "MSA" }).click();
    await p.waitForURL(/\/documents\/.+/); msaId = p.url().split("/").pop();
    await p.getByText("Negotiation").first().waitFor();
  }, p);
  await step(J, "Open clause comment blocks sending for signature", async () => {
    await p.getByPlaceholder(/^Clause \(optional\)/).fill("6. Limitation of liability");
    await p.getByPlaceholder("Internal comment for legal / deal desk").fill("Customer asks for a 2x liability cap");
    await p.getByRole("button", { name: "Comment", exact: true }).click(); await p.getByText("Customer asks for a 2x liability cap").waitFor();
    await p.locator("select").filter({ hasText: "Pick from buying committee" }).selectOption({ index: 1 });
    await p.getByRole("button", { name: "Send for e-signature" }).click(); await toast(p, "open redline comment");
  }, p);
  await step(J, "Resolve comment and save a revised version; redline diff shows the change", async () => {
    await p.getByRole("button", { name: "Resolve" }).click(); await p.waitForTimeout(600);
    await p.getByRole("button", { name: "Revise" }).click();
    const ta = p.locator("textarea.font-mono"); const body = await ta.inputValue();
    await ta.fill(body.replace("twelve (12) months", "twelve (12) months, capped at two times (2x) annual fees"));
    await p.getByPlaceholder(/What changed and why/).fill("2x liability cap agreed");
    await p.getByRole("button", { name: "Save version 2" }).click(); await toast(p, "New version saved");
    await p.getByText(/\+\d+ \/ −\d+ lines/).waitFor(); await p.getByText("v2", { exact: true }).first().waitFor();
    return await p.getByText(/\+\d+ \/ −\d+ lines/).innerText();
  }, p);
  await step(J, "Generate Order Form from the approved primary quote", async () => {
    await p.goto(`${BASE}/quotes/${quoteId}`); await p.getByRole("button", { name: "Generate Order Form" }).click();
    await p.waitForURL(/\/documents\/.+/); orderFormUrl = p.url();
  }, p);
  let links = [];
  await step(J, "Send Order Form for built-in e-signature", async () => {
    const sel = p.locator("select").filter({ hasText: "Pick from buying committee" });
    const val = await sel.locator("option", { hasText: "Economic Buyer" }).first().getAttribute("value"); await sel.selectOption(val);
    await p.getByRole("button", { name: "Send for e-signature" }).click(); await toast(p, "Sent for signature");
    await p.getByLabel("Signing link", { exact: true }).first().waitFor();
    links = await p.getByLabel("Signing link", { exact: true }).evaluateAll((els) => els.map((e) => new URL(e.value).pathname));
    expect(links.length === 2, "expected 2 signing links");
  }, p);
  await step(J, "Company cannot sign before the customer (signing order)", async () => {
    const c = await browser.newContext(); const s = await c.newPage(); await s.goto(BASE + links[1]);
    await s.getByText("Waiting for an earlier signer").waitFor(); await c.close();
  }, p);
  await step(J, "Customer signs from the public link", async () => {
    const c = await browser.newContext(); const s = await c.newPage(); await s.goto(BASE + links[0]);
    await s.locator("#sig-name").fill("Anna Keller"); await s.getByRole("checkbox").last().check();
    await s.getByRole("button", { name: "Sign document" }).click(); await s.getByText("your signature is recorded").waitFor(); await c.close();
  }, p);
  await step(J, "Company countersigns; document fully executed", async () => {
    const c = await browser.newContext(); const s = await c.newPage(); await s.goto(BASE + links[1]);
    await s.locator("#sig-name").fill("Marcus Vance"); await s.getByRole("checkbox").last().check();
    await s.getByRole("button", { name: "Sign document" }).click(); await s.getByText("Fully executed").waitFor(); await c.close();
    await p.reload(); await p.getByText(/^completed$/i).first().waitFor();
  }, p);
}

// J7 closed-won & order ------------------------------------------------------------------------------------------------------
{
  const J = "7. Closed-Won validation, order & ERP"; const p = mgr.page;
  await step(J, "Order readiness shows missing PO and addresses", async () => {
    await p.goto(`${BASE}/deals/${dealId}`); await p.getByText("Order readiness").waitFor();
    const r = (await api(mgr.token, "GET", `/deals/${dealId}/order-readiness`)).data;
    const missing = r.checks.filter((c) => !c.met).map((c) => c.criterion);
    expect(!r.ready && missing.includes("Customer PO number"), "readiness wrong"); return `missing: ${missing.join(", ")}`;
  }, p);
  await step(J, "Closed-Won is blocked by the order checks", async () => {
    await moveTo(p, "Closed-Won"); await p.getByText("Entry criteria for Closed-Won").waitFor();
    await p.getByRole("button", { name: "Cancel" }).click();
  }, p);
  await step(J, "Rep captures PO, bill-to and ship-to on the deal", async () => {
    await p.locator("#po").fill(`PO-E2E-${RUN}`); await p.locator("#inco").fill("DAP");
    for (const [f, v] of [["Street", "Hafenstrasse 12"], ["City", "Hamburg"], ["Postal code", "20457"], ["Country", "DE"]]) await p.getByLabel(`Bill to ${f}`).fill(v);
    await p.getByLabel("Ship to billing address").check();
    await p.getByRole("button", { name: "Save order details" }).click(); await toast(p, "Order details saved");
    const r = (await api(mgr.token, "GET", `/deals/${dealId}/order-readiness`)).data; expect(r.ready, "still not ready: " + JSON.stringify(r.checks.filter((c) => !c.met)));
  }, p);
  await step(J, "Closed-Won locks the quote and raises the order automatically", async () => {
    await p.reload(); await moveTo(p, "Closed-Won"); await toast(p, "Moved to Closed-Won");
    await p.reload(); const link = p.getByRole("link", { name: /^ORD-/ }); await link.waitFor();
    const q = (await api(mgr.token, "GET", `/quotes/${quoteId}`)).data; expect(q.locked_at, "quote not locked");
    await link.click(); await p.waitForURL(/\/orders\/.+/); orderId = p.url().split("/").pop();
    return (await p.locator("h1").innerText()) + " · quote locked";
  }, p);
  await step(J, "Order carries PO, lines and a billing schedule", async () => {
    const o = (await api(mgr.token, "GET", `/orders/${orderId}`)).data;
    expect(o.po_number === `PO-E2E-${RUN}` && o.lines.length >= 2, "header/lines wrong");
    const plat = o.lines.find((l) => l.sku === "CIR-PLAT"); const sum = plat.billing_schedule.reduce((a, b) => a + b.amount, 0);
    expect(Math.abs(sum - plat.line_total) < 0.05, `schedule ${sum} != line ${plat.line_total}`);
    return `${o.lines.length} lines, ${o.currency} ${o.total.toLocaleString()}, ${plat.billing_schedule.length} invoices on CIR-PLAT`;
  }, p);
  await step(J, "Push to ERP returns a sales-order number", async () => {
    await p.getByRole("button", { name: "Push now" }).click(); await toast(p, "ERP sales order SO-");
    await p.reload(); await p.getByText("Acknowledged").first().waitFor();
    return (await api(mgr.token, "GET", `/orders/${orderId}`)).data.erp_order_id;
  }, p);
  await step(J, "Second order for the same deal is refused", async () => {
    const r = await api(mgr.token, "POST", `/deals/${dealId}/orders`); expect(r.status === 422, `got ${r.status}`); return r.data.detail;
  }, p);
  await step(J, "Order appears in the Orders list as In ERP", async () => {
    await p.goto(`${BASE}/orders`); await p.getByRole("tab", { name: "In ERP" }).click(); await p.getByText(company).first().waitFor();
  }, p);
}

// J8 governance: overrides -------------------------------------------------------------------------------------------------------
{
  const J = "8. Governance: gate overrides";
  const ae = await login("diego@cirra.demo");
  const crescent = (await api(ae.token, "GET", "/deals?status=all")).data.find((x) => x.title.startsWith("Crescent"));
  await step(J, "Rep sees the unmet criteria but no 'Move anyway' button", async () => {
    await ae.page.goto(`${BASE}/deals/${crescent.id}`); await moveTo(ae.page, "Closed-Won");
    await ae.page.getByText("Entry criteria for Closed-Won").waitFor();
    await ae.page.getByText("ask your sales manager").waitFor();
    expect(await ae.page.getByRole("button", { name: "Move anyway" }).count() === 0, "Move anyway offered to a rep");
    await ae.page.getByRole("button", { name: "Cancel" }).click();
  }, ae.page);
  await step(J, "Sales rep cannot force Closed-Won past the order checks", async () => {
    const d = (await api(ae.token, "GET", `/deals/${crescent.id}`)).data; const won = d.stages.find((s) => s.is_closed_won);
    const r = await api(ae.token, "PATCH", `/deals/${crescent.id}/stage`, { stage_id: won.id, override_gates: true });
    const after = (await api(ae.token, "GET", `/deals/${crescent.id}`)).data;
    expect(r.status !== 200 && after.stage !== "Closed-Won", `rep override accepted (HTTP ${r.status}); deal is now ${after.stage} with no order`);
    return `refused with HTTP ${r.status}`;
  }, ae.page);
  await step(J, "Sales rep cannot skip mid-funnel gates either", async () => {
    const d = (await api(ae.token, "GET", `/deals/${crescent.id}`)).data;
    if (d.stage === "Closed-Won") throw new Error("deal already force-closed by previous step");
    const next = d.stages.find((s) => s.name === "Solution Design / Demo");
    const r = await api(ae.token, "PATCH", `/deals/${crescent.id}/stage`, { stage_id: next.id, override_gates: true });
    expect(r.status === 403 || r.status === 409, `rep override accepted (HTTP ${r.status})`); return `HTTP ${r.status}`;
  }, ae.page);
  await ae.ctx.close();
}

// J9 admin ---------------------------------------------------------------------------------------------------------------------
{
  const J = "9. Admin configuration"; const adm = await login("admin@cirra.demo"); const p = adm.page;
  let raw;
  await step(J, "Issue a web-form key and receive the embed URL once", async () => {
    await p.goto(`${BASE}/admin`); await p.getByRole("tab", { name: "Lead management" }).click();
    await p.getByRole("tab", { name: "Web forms & webhooks" }).click();
    const form = p.locator("form", { hasText: "Issue key" });
    await form.locator("input").first().fill(`E2E form ${RUN}`); await form.locator("input").last().fill(`E2E campaign ${RUN}`);
    await p.getByRole("button", { name: "Issue key" }).click(); await p.getByText("Key issued").waitFor();
    raw = await p.locator("input.font-mono").first().inputValue(); expect(raw.startsWith("cf_"), "bad key"); return raw.slice(0, 10) + "…";
  }, p);
  await step(J, "The new key's hosted form captures a lead tagged with the campaign", async () => {
    const c = await browser.newContext(); const f = await c.newPage(); await f.goto(`${BASE}/forms/${raw}`);
    await f.fill("#f-email", `e2e.${RUN}@kiwi-foods.co.nz`); await f.fill("#f-company_name", `Kiwi Foods ${RUN}`); await f.fill("#f-country", "New Zealand");
    await f.getByRole("button", { name: "Submit" }).click(); await f.getByText("Thank you").waitFor(); await c.close();
    const leads = (await api(adm.token, "GET", `/leads?q=${RUN}`)).data; const l = leads.find((x) => x.email === `e2e.${RUN}@kiwi-foods.co.nz`);
    expect(l && l.campaign === `E2E campaign ${RUN}` && l.region === "APAC", "lead missing or not tagged"); return `${l.region}, owner ${l.owner?.full_name ?? "unassigned"}`;
  }, p);
  await step(J, "Scoring settings save and rescore open leads", async () => {
    await p.goto(`${BASE}/admin`); await p.getByRole("tab", { name: "Lead management" }).click();
    await p.getByRole("button", { name: "Save & rescore" }).click(); await toast(p, "open leads rescored");
  }, p);
  await step(J, "Approval chain shows five levels with approvers", async () => {
    await p.getByRole("tab", { name: "Approval chain" }).click();
    for (const l of ["1. Sales Manager", "2. Deal Desk", "3. VP Sales", "4. Finance", "5. Legal"]) await p.getByText(l, { exact: true }).waitFor();
  }, p);
  await step(J, "Stage editor adds and deletes a stage", async () => {
    await p.getByRole("tab", { name: "Stages", exact: true }).click();
    const form = p.locator("form", { hasText: "New stage" }); await form.locator("input").first().fill(`E2E stage ${RUN}`);
    await p.getByRole("button", { name: "Add stage" }).click(); await toast(p, "Stage added");
    const row = p.locator("div.rounded-md.border", { has: p.locator(`input[value="E2E stage ${RUN}"]`) });
    p.once("dialog", (d) => d.accept()); await row.getByRole("button", { name: "Delete stage" }).click(); await toast(p, "Stage removed");
  }, p);
  await step(J, "Price books and promotions are listed on Products", async () => {
    await p.goto(`${BASE}/products`); await p.getByRole("tab", { name: "Price books" }).click(); await p.getByText("EMEA 2026 regional").waitFor();
    await p.getByRole("tab", { name: "Promotions" }).click(); await p.getByText("LAUNCH-AI").first().waitFor();
  }, p);
  await adm.ctx.close();
}

// J10 role-based access ------------------------------------------------------------------------------------------------------------
{
  const J = "10. Role-based access";
  const aud = await login("viewer@cirra.demo");
  await step(J, "Auditor sees leads read-only (no New lead, no Convert)", async () => {
    await aud.page.goto(`${BASE}/leads`); await aud.page.getByRole("heading", { name: "Leads" }).waitFor();
    expect(await aud.page.getByRole("button", { name: "New lead" }).count() === 0, "New lead visible");
    await aud.page.goto(`${BASE}/leads/${leadId}`); await aud.page.getByText("Anna Keller").first().waitFor();
    expect(await aud.page.getByRole("button", { name: "Convert" }).count() === 0, "Convert visible");
  }, aud.page);
  await step(J, "Auditor cannot create orders via the API", async () => {
    const r = await api(aud.token, "POST", `/deals/${dealId}/orders`); expect(r.status === 403, `got ${r.status}`);
  }, aud.page);
  await aud.ctx.close();
  const sdr = await login("sam@cirra.demo");
  await step(J, "SDR only sees own or unassigned leads", async () => {
    const leads = (await api(sdr.token, "GET", "/leads")).data;
    const bad = leads.filter((l) => l.owner && l.owner.full_name !== "Sam Okoye");
    expect(bad.length === 0, `${bad.length} foreign leads visible`); return `${leads.length} visible`;
  }, sdr.page);
  await sdr.ctx.close();
}

// J11 page sweep ------------------------------------------------------------------------------------------------------------------------
{
  const J = "11. Page sweep (every screen, every role)";
  const ids = (await api(mgr.token, "GET", `/deals/${dealId}`)).data;
  const routes = ["/", "/leads", `/leads/${leadId}`, "/pipeline", "/accounts", `/accounts/${ids.account.id}`, `/deals/${dealId}`, "/contacts", "/tasks",
    "/ask", "/quotes", `/quotes/${quoteId}`, "/approvals", new URL(orderFormUrl).pathname, "/orders", `/orders/${orderId}`, "/products", "/reports",
    "/success", "/finance", "/partners", "/admin", "/settings"];
  for (const who of ["admin@cirra.demo", "marcus@cirra.demo", "priya@cirra.demo", "sam@cirra.demo", "viewer@cirra.demo"]) {
    const s = await login(who); const errs = [];
    s.page.on("pageerror", (e) => errs.push(`pageerror ${e.message}`));
    s.page.on("console", (m) => m.type() === "error" && !/Failed to load resource/.test(m.text()) && errs.push(`console ${m.text().slice(0, 120)}`));
    s.page.on("response", (r) => r.url().includes("/api/v1/") && r.status() >= 500 && errs.push(`HTTP ${r.status()} ${new URL(r.url()).pathname}`));
    await step(J, `${who.split("@")[0]}: ${routes.length} screens load without errors`, async () => {
      for (const r of routes) { await s.page.goto(BASE + r); await s.page.waitForLoadState("networkidle").catch(() => {}); await s.page.waitForTimeout(250); }
      expect(errs.length === 0, [...new Set(errs)].slice(0, 4).join(" | "));
    }, s.page);
    await s.ctx.close();
  }
}

// J12 mobile -------------------------------------------------------------------------------------------------------------------------------
{
  const J = "12. Mobile layout (390px)";
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } }); const p = await ctx.newPage();
  await p.goto(`${BASE}/login`); await p.fill("input[type=email]", "marcus@cirra.demo"); await p.fill("input[type=password]", PW);
  await p.click("button[type=submit]"); await p.waitForURL((u) => !u.pathname.startsWith("/login"));
  for (const r of ["/leads", `/leads/${leadId}`, `/deals/${dealId}`, `/orders/${orderId}`, `/forms/cf_demo_webform_cirra`]) {
    await step(J, `${r.replace(/[0-9a-f-]{36}/, ":id")} has no horizontal page scroll`, async () => {
      await p.goto(BASE + r); await p.waitForTimeout(1200);
      const over = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(over <= 1, `page is ${over}px wider than the viewport`);
    }, p);
  }
  await ctx.close();
}

await browser.close();
writeFileSync(`${OUT}/e2e/results.json`, JSON.stringify({ run: RUN, at: new Date().toISOString(), results }, null, 2));
const c = (s) => results.filter((r) => r.status === s).length;
console.log(`\nTOTAL ${results.length}: ${c("pass")} passed, ${c("fail")} failed, ${c("blocked")} blocked`);
