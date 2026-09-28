// Browser functional sweep of every screen for every role, plus UI journeys through the newest modules.
// npm i playwright && node e2e/screen-sweep.mjs <outdir> [unsubscribe-token]   (E2E_CHANNEL=msedge to use an installed Edge)
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "fs";

const OUT = process.argv[2] ?? "./out"; mkdirSync(`${OUT}/shots`, { recursive: true });
const UNSUB = process.argv[3] ?? "";
const BASE = "http://localhost:3000", API = "http://localhost:8000/api/v1", PW = "cirra123";
const RUN = Date.now().toString(36).slice(-5);
const ROLES = {
  admin: "admin@cirra.demo", manager: "marcus@cirra.demo", ae: "priya@cirra.demo", sdr: "sam@cirra.demo", auditor: "viewer@cirra.demo",
  support: "sofia@cirra.demo", marketing: "nina@cirra.demo", partner: "partner@northstar-partners.com",
};
const results = [];
const browser = await chromium.launch(process.env.E2E_CHANNEL ? { channel: process.env.E2E_CHANNEL } : {});

async function step(area, name, fn, page) {
  const t0 = Date.now();
  try {
    const detail = await fn();
    results.push({ area, name, status: "pass", ms: Date.now() - t0, detail: detail ?? "" });
    console.log("PASS", area, "›", name, detail ?? "");
  } catch (e) {
    const shot = page ? `shots/fail-${results.length + 1}.png` : null;
    if (page) await page.screenshot({ path: `${OUT}/${shot}`, fullPage: true }).catch(() => {});
    results.push({ area, name, status: "fail", ms: Date.now() - t0, detail: String(e.message ?? e).split("\n")[0].slice(0, 400), shot });
    console.log("FAIL", area, "›", name, "->", String(e.message ?? e).split("\n")[0].slice(0, 300));
  }
}
const expect = (c, m) => { if (!c) throw new Error(m); };

/** A signed-in page that records console errors, API 5xx and API 403s. */
async function session(email, viewport = { width: 1440, height: 950 }) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const log = { console: [], api5xx: [], api403: [], pageErrors: [] };
  page.on("console", (m) => { if (m.type() === "error" && !/favicon|Download the React DevTools|net::ERR_ABORTED/.test(m.text())) log.console.push(m.text().slice(0, 200)); });
  page.on("pageerror", (e) => log.pageErrors.push(String(e.message).slice(0, 200)));
  page.on("response", (r) => {
    if (!r.url().startsWith(API)) return;
    if (r.status() >= 500) log.api5xx.push(`${r.status()} ${r.request().method()} ${r.url().slice(API.length)}`);
    if (r.status() === 403) log.api403.push(`${r.request().method()} ${r.url().slice(API.length)}`);
  });
  await page.goto(`${BASE}/login`);
  await page.fill("input[type=email]", email); await page.fill("input[type=password]", PW);
  await page.click("button[type=submit]");
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
  await page.waitForLoadState("networkidle").catch(() => {});
  const token = await page.evaluate(() => localStorage.getItem("cirra.token"));
  return { ctx, page, log, token };
}
const reset = (log) => { log.console.length = 0; log.api5xx.length = 0; log.api403.length = 0; log.pageErrors.length = 0; };

async function visit(s, path, name) {
  reset(s.log);
  await s.page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded" });
  await s.page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});
  await s.page.waitForTimeout(400);
  const body = await s.page.locator("body").innerText();
  expect(!/Application error|Unhandled Runtime Error|Something went wrong|This page could not be found/i.test(body), `error screen on ${path}`);
  expect(!s.log.pageErrors.length, `page error: ${s.log.pageErrors[0]}`);
  expect(!s.log.api5xx.length, `API 5xx: ${s.log.api5xx.join(", ")}`);
  expect(!s.log.api403.length, `UI called endpoints the role can't use: ${[...new Set(s.log.api403)].join(", ")}`);
  expect(!s.log.console.length, `console error: ${s.log.console[0]}`);
  const noScroll = await s.page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  expect(noScroll, "horizontal page scroll");
  await s.page.screenshot({ path: `${OUT}/shots/${name}.png` }).catch(() => {});
  return body.length;
}

// ---- 1. every screen for every role ------------------------------------------------------------------------
const ADMIN_TABS = ["Users", "Sign-in security", "Roles & permissions", "Audit trail", "Privacy & consent", "Duplicates", "Custom fields", "Lead management",
  "Workflows", "Service", "Territories & incentives", "Approval chain", "Stages", "Stage gates", "Import / export", "API & webhooks", "Jobs"];
const DETAIL_FROM = { "/accounts": "/accounts/", "/contacts": "/contacts/", "/leads": "/leads/", "/pipeline": "/deals/", "/quotes": "/quotes/",
  "/orders": "/orders/", "/cases": "/cases/", "/campaigns": "/campaigns/", "/reports": "/reports/dashboards/" };

for (const [role, email] of Object.entries(ROLES)) {
  let s;
  await step(`screens:${role}`, "sign in", async () => { s = await session(email); return new URL(s.page.url()).pathname; });
  if (!s) continue;
  if (role === "partner") {
    await step(`screens:${role}`, "lands on the partner portal only", async () => {
      expect(new URL(s.page.url()).pathname.startsWith("/portal"), `landed on ${s.page.url()}`);
      await visit(s, "/portal", `${role}-portal`);
      await s.page.goto(`${BASE}/pipeline`);
      await s.page.waitForURL((u) => u.pathname.startsWith("/portal"), { timeout: 8000 }).catch(() => {});
      expect(new URL(s.page.url()).pathname.startsWith("/portal"), "partner reached an internal screen");
    }, s.page);
    await s.ctx.close(); continue;
  }
  const nav = await s.page.$$eval("aside a[href^='/'], nav a[href^='/']", (as) => [...new Set(as.map((a) => a.getAttribute("href")))].filter((h) => h && !h.startsWith("//")));
  for (const path of nav) {
    await step(`screens:${role}`, `open ${path}`, async () => `${await visit(s, path, `${role}${path.replace(/\//g, "_") || "_home"}`)} chars`, s.page);
    const prefix = DETAIL_FROM[path];
    if (prefix) {
      const href = await s.page.$$eval(`a[href^='${prefix}']`, (as) => as.map((a) => a.getAttribute("href")).find((h) => h && !h.endsWith("/new") && h.split("/").length > 2)).catch(() => null);
      if (href) await step(`screens:${role}`, `open a detail page ${href.replace(/[0-9a-f-]{36}/, ":id")}`, async () => { await visit(s, href, `${role}${prefix.replace(/\//g, "_")}detail`); }, s.page);
    }
    if (path === "/reports") {
      for (const tab of ["Saved reports", "Forecast call", "Pipeline forecast", "Win / loss"]) {
        await step(`screens:${role}`, `reports tab ${tab}`, async () => {
          reset(s.log);
          const t = s.page.getByRole("tab", { name: tab }).or(s.page.getByRole("button", { name: tab }));
          if (!(await t.count())) return "not shown";
          await t.first().click(); await s.page.waitForLoadState("networkidle").catch(() => {}); await s.page.waitForTimeout(300);
          expect(!s.log.api5xx.length && !s.log.pageErrors.length, `${s.log.api5xx} ${s.log.pageErrors}`);
          expect(!s.log.api403.length, `403s: ${[...new Set(s.log.api403)]}`);
        }, s.page);
      }
    }
    if (path === "/admin") {
      for (const tab of ADMIN_TABS) {
        await step(`screens:${role}`, `admin tab ${tab}`, async () => {
          reset(s.log);
          const t = s.page.getByRole("tab", { name: tab, exact: true }).or(s.page.getByRole("button", { name: tab, exact: true }));
          if (!(await t.count())) return "not shown for this role";
          await t.first().click(); await s.page.waitForLoadState("networkidle").catch(() => {}); await s.page.waitForTimeout(400);
          const body = await s.page.locator("main").innerText().catch(() => "");
          expect(!/Application error|Something went wrong/i.test(body), "error screen");
          expect(!s.log.api5xx.length && !s.log.pageErrors.length, `${s.log.api5xx} ${s.log.pageErrors}`);
          expect(!s.log.api403.length, `403s: ${[...new Set(s.log.api403)]}`);
          await s.page.screenshot({ path: `${OUT}/shots/${role}-admin-${tab.replace(/[^a-z]+/gi, "-")}.png` }).catch(() => {});
        }, s.page);
      }
    }
  }
  await s.ctx.close();
}

// ---- 2. journeys through the newest modules ------------------------------------------------------------------
const toast = (page, text) => page.locator("[data-sonner-toast]", { hasText: text }).first().waitFor({ timeout: 15000 });

{
  const s = await session(ROLES.admin);
  const p = s.page;
  await step("journey:admin", "create a territory and preview realignment", async () => {
    await p.goto(`${BASE}/admin?tab=territories`); await p.waitForLoadState("networkidle");
    await p.getByRole("button", { name: "New territory" }).click();
    await p.fill("#tr-name", `Nordics ${RUN}`); await p.fill("#tr-priority", "20"); await p.fill("#tr-countries", "Norway, Sweden, Denmark, Finland");
    await p.getByRole("button", { name: "Save", exact: true }).click();
    await toast(p, "Territory saved");
    await p.getByRole("button", { name: "Preview realignment" }).click();
    await p.getByText(/Realignment preview:/).waitFor({ timeout: 15000 });
    const head = await p.getByText(/Realignment preview:/).innerText();
    await p.screenshot({ path: `${OUT}/shots/journey-territory-preview.png` });
    return head;
  }, p);
  await step("journey:admin", "commission plan editor previews the payout", async () => {
    await p.getByRole("button", { name: "New plan" }).click();
    await p.fill("#pl-name", `FT plan ${RUN}`);
    await p.getByText(/this plan pays/).waitFor();
    await p.waitForTimeout(800);
    const txt = await p.getByText(/this plan pays/).innerText();
    await p.getByRole("button", { name: "Save plan" }).click(); await toast(p, "Plan saved");
    return txt.replace(/\s+/g, " ");
  }, p);
  await step("journey:admin", "issue a read-only API key (shown once) and use it", async () => {
    await p.goto(`${BASE}/admin?tab=developer`); await p.waitForLoadState("networkidle");
    await p.getByRole("button", { name: "New key" }).click();
    await p.fill("#ak-name", `FT key ${RUN}`);
    await p.selectOption("#ak-user", { index: 1 });
    await p.getByRole("button", { name: "Create key" }).click();
    const code = p.getByRole("dialog").locator("code", { hasText: "ck_" }).first(); await code.waitFor();
    const key = (await code.innerText()).trim();
    await p.screenshot({ path: `${OUT}/shots/journey-api-key.png` });
    await p.getByRole("button", { name: "Done" }).click();
    const r = await fetch(`${API}/users/me`, { headers: { "X-API-Key": key } });
    const w = await fetch(`${API}/tasks`, { method: "POST", headers: { "X-API-Key": key, "content-type": "application/json" }, body: JSON.stringify({ title: "x" }) });
    expect(r.status === 200 && w.status === 403, `read ${r.status}, write ${w.status}`);
    return "GET 200, POST 403";
  }, p);
  await step("journey:admin", "create a webhook, get its secret once, send a test", async () => {
    await p.getByRole("button", { name: "New webhook" }).click();
    await p.fill("#wh-name", `FT hook ${RUN}`); await p.fill("#wh-url", "https://hooks.cirra-test.invalid/unreachable");  // internal hosts are refused (SSRF guard)
    await p.getByRole("button", { name: "Save", exact: true }).click();
    await p.getByRole("dialog").locator("code", { hasText: "whsec_" }).first().waitFor();
    await p.getByRole("button", { name: "Done" }).click();
    const row = p.locator("tr", { hasText: `FT hook ${RUN}` });
    await row.getByRole("button", { name: "Test" }).click();
    await p.locator("[data-sonner-toast]", { hasText: /Test (delivered|failed)/ }).first().waitFor({ timeout: 20000 });
    await row.getByRole("button", { name: `FT hook ${RUN}`, exact: true }).click();
    await p.getByText("Recent deliveries").waitFor();
    await p.screenshot({ path: `${OUT}/shots/journey-webhook.png` });
    return "test ping logged in deliveries";
  }, p);
  await step("journey:admin", "workflow on/off switch works (regression for the 500)", async () => {
    reset(s.log);
    await p.goto(`${BASE}/admin?tab=workflows`); await p.waitForLoadState("networkidle");
    const box = p.locator("input[id^='wf-on-']").first();
    if (!(await box.count())) return "no workflows to toggle";
    const before = await box.isChecked();
    await box.click(); await p.waitForTimeout(1200);
    expect(!s.log.api5xx.length, s.log.api5xx.join());
    expect((await box.isChecked()) !== before, "switch didn't change");
    await box.click(); await p.waitForTimeout(800);
    return "toggled and restored";
  }, p);
  await s.ctx.close();
}

{
  const s = await session(ROLES.manager);
  const p = s.page;
  await step("journey:manager", "set a quota from the team leaderboard", async () => {
    await p.goto(`${BASE}/performance`); await p.waitForLoadState("networkidle");
    await p.getByRole("tab", { name: "Team & quotas" }).or(p.getByRole("button", { name: "Team & quotas" })).first().click();
    const input = p.getByLabel("Quota for Sam Okoye");
    await input.waitFor();
    const value = String(150000 + (Date.now() % 97) * 1000);
    await input.fill(value); await input.blur();
    await toast(p, "Quota saved");
    await p.screenshot({ path: `${OUT}/shots/journey-quota.png` });
    return "saved";
  }, p);
  await s.ctx.close();
}

let unsubToken = UNSUB;
{
  const s = await session(ROLES.marketing);
  const p = s.page;
  await step("journey:marketing", "create a campaign, build a list, write and send the email", async () => {
    await p.goto(`${BASE}/campaigns`); await p.waitForLoadState("networkidle");
    await p.getByRole("button", { name: "New campaign" }).click();
    await p.fill("#cp-name", `UI Roundtable ${RUN}`); await p.selectOption("#cp-type", "event"); await p.fill("#cp-cost", "1500");
    await p.getByRole("button", { name: "Create campaign" }).click();
    await p.waitForURL(/\/campaigns\/[0-9a-f-]{36}/, { timeout: 15000 });
    await p.getByRole("tab", { name: /Members/ }).or(p.getByRole("button", { name: /Members/ })).first().click();
    await p.getByRole("button", { name: "Add from a filter" }).click();
    await p.selectOption("#bl-source", "contacts");
    await p.getByRole("button", { name: "Add condition" }).click();
    await p.getByRole("button", { name: "Add members" }).click();
    await toast(p, "Added");
    await p.getByRole("tab", { name: "Email" }).or(p.getByRole("button", { name: "Email", exact: true })).first().click();
    await p.fill("#em-subject", "Hi {{first_name}}"); await p.fill("#em-body", "You're invited, {{first_name}} from {{company}}.");
    await p.getByRole("button", { name: "Save email" }).click(); await toast(p, "Email saved");
    await p.getByText(/will receive it/).waitFor();
    await p.screenshot({ path: `${OUT}/shots/journey-campaign-email.png` });
    p.once("dialog", (d) => d.accept());
    const send = p.getByRole("button", { name: /^Send to \d+/ });
    if (await send.isDisabled()) return "nobody eligible (all blocked by consent)";
    await send.click();
    await toast(p, "Sending in the background");
    await p.getByText(/^Last send: \d+ sent/).waitFor({ timeout: 30000 });  // the page picks up the job's result
    return "sent by the background job";
  }, p);
  await step("journey:marketing", "campaign overview shows funnel and attribution", async () => {
    await p.getByRole("tab", { name: "Overview" }).or(p.getByRole("button", { name: "Overview" })).first().click();
    await p.getByText("Member funnel").waitFor();
    return await p.getByText(/Sourced pipeline/).first().innerText();
  }, p);
  await s.ctx.close();
}

await step("journey:public", "unsubscribe page confirms and opts the person out", async () => {
  expect(unsubToken, "no token passed");
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/unsubscribe/${unsubToken}`); await p.waitForLoadState("networkidle");
  await p.getByRole("button", { name: "Unsubscribe" }).click();
  await p.getByText("You’re unsubscribed").waitFor();
  await p.screenshot({ path: `${OUT}/shots/journey-unsubscribe.png` });
  await ctx.close();
  return "confirmed";
});

// ---- 3. phone layout of the new screens ---------------------------------------------------------------------
{
  const s = await session(ROLES.manager, { width: 390, height: 844 });
  for (const path of ["/performance", "/campaigns", "/admin?tab=territories", "/admin?tab=developer", "/cases", "/reports"]) {
    await step("mobile:manager", `no horizontal scroll on ${path}`, async () => { await visit(s, path, `mobile${path.replace(/[/?=]/g, "_")}`); }, s.page);
  }
  await s.ctx.close();
}

await browser.close();
writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 1));
const pass = results.filter((r) => r.status === "pass").length;
console.log(`\n${pass}/${results.length} browser checks passed`);
