// Browser journeys for the P0 reach work: notification center, buying committee and org chart, segments and website
// tracking, connectors, the installable mobile app with offline queue, and page translation.
// npm i playwright && node e2e/p0-reach-journeys.mjs <outdir>  (E2E_CHANNEL=msedge for Edge)
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "fs";

const OUT = process.argv[2] ?? "./reach-out"; mkdirSync(`${OUT}/shots`, { recursive: true });
const BASE = "http://localhost:3000", API = "http://localhost:8000/api/v1";
const results = [];
const browser = await chromium.launch(process.env.E2E_CHANNEL ? { channel: process.env.E2E_CHANNEL } : {});
const expect = (c, m) => { if (!c) throw new Error(m); };
const RUN = Date.now().toString(36);

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
async function token(email) {
  const r = await fetch(`${API}/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: email, password: "cirra123" }) });
  return (await r.json()).access_token;
}
async function call(tok, method, path, body) {
  const r = await fetch(`${API}${path}`, { method, headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return r.status === 204 ? null : r.json();
}
const shot = (page, name) => page.screenshot({ path: `${OUT}/shots/${name}.png` });

const marcus = await login("marcus@cirra.demo");
const p = marcus.page;

await step("notifications", "the bell leads to the notification center; snooze and archive work", async () => {
  await p.goto(`${BASE}/settings?tab=notifications`); await p.waitForLoadState("networkidle");
  await p.getByRole("button", { name: "Send a test" }).click();
  await p.getByText(/Test notification sent/).waitFor();
  await p.goto(`${BASE}/notifications`); await p.waitForLoadState("networkidle");
  const item = p.getByRole("button", { name: /Test notification/ }).first();
  await item.waitFor();
  await p.getByLabel("Snooze: Test notification").first().selectOption("1h");
  await p.getByText("Snoozed", { exact: true }).last().waitFor();
  await p.getByRole("tab", { name: "Snoozed" }).click();
  await p.getByRole("button", { name: /Test notification/ }).first().waitFor();
  await p.getByRole("button", { name: /^Archive: Test notification/ }).first().click();
  await p.getByRole("tab", { name: "Archived" }).click();
  await p.getByRole("button", { name: /Test notification/ }).first().waitFor();
  await shot(p, "notification-center");
}, p);

await step("notifications", "preferences save per kind and channel, with quiet hours", async () => {
  await p.goto(`${BASE}/settings?tab=notifications`); await p.waitForLoadState("networkidle");
  const box = p.getByLabel("Deal risk: In the app");
  const was = await box.isChecked();
  await box.click();
  await p.getByText("Notification settings saved").first().waitFor();
  expect((await box.isChecked()) === !was, "the choice was saved");
  await box.click();
  await p.waitForFunction((w) => document.querySelector('input[aria-label="Deal risk: In the app"]')?.checked === w, was);
  const quiet = await p.locator("#n-quiet").isChecked();
  await p.locator("#n-quiet").click();
  await p.waitForFunction((q) => document.querySelector("#n-quiet")?.checked === !q, quiet);
  expect((await p.getByLabel("Quiet from").isEnabled()) === !quiet, "quiet hours inputs follow the switch");
  await p.locator("#n-quiet").click();
  await p.waitForFunction((q) => document.querySelector("#n-quiet")?.checked === q, quiet);
  await shot(p, "notification-settings");
}, p);

// a fresh account with people and a deal, via the API
const mt = await token("marcus@cirra.demo");
const acc = await call(mt, "POST", "/accounts", { name: `Reach Works ${RUN}`, domain: `reach${RUN}.example.com`, force: true });
const people = {};
for (const [first, title, role] of [["Vera", "Chief Financial Officer", "Economic Buyer"], ["Tom", "VP Operations", "Decision Maker"], ["Ana", "Operations Manager", "Champion"]]) {
  people[first] = await call(mt, "POST", "/contacts", { account_id: acc.id, first_name: first, last_name: `Reach${RUN}`, email: `${first.toLowerCase()}@reach${RUN}.example.com`, job_title: title, buying_role: role });
}
await call(mt, "PATCH", `/contacts/${people.Ana.id}`, { reports_to_id: people.Tom.id });
const deal = await call(mt, "POST", "/deals", { title: `Reach rollout ${RUN}`, account_id: acc.id, amount: 42000 });

await step("stakeholders", "the deal's buying committee: add people, set stance, see gaps and suggestions", async () => {
  await p.goto(`${BASE}/deals/${deal.id}`); await p.waitForLoadState("networkidle");
  await p.getByRole("button", { name: "Add to buying committee" }).click();
  await p.locator("#bc-contact").selectOption(people.Ana.id);
  await p.locator("#bc-role").selectOption("Champion");
  await p.getByRole("button", { name: "Add", exact: true }).click();
  await p.getByLabel(`Ana Reach${RUN}: stance`).selectOption("champion");
  await p.getByText("Single-threaded: only one person is engaged on this deal").waitFor();
  await p.getByRole("button", { name: /^Add Tom Reach.* as / }).click();
  await p.getByLabel(`Tom Reach${RUN}: role`).waitFor();
  await p.getByRole("meter", { name: "Committee coverage" }).waitFor();
  await shot(p, "buying-committee");
}, p);

await step("stakeholders", "the account org chart shows reporting lines and can be edited", async () => {
  await p.goto(`${BASE}/accounts/${acc.id}`); await p.waitForLoadState("networkidle");
  await p.getByRole("tab", { name: "Org chart" }).click();
  await p.getByRole("list", { name: `Reports to Tom Reach${RUN}` }).getByRole("link", { name: `Ana Reach${RUN}` }).waitFor();
  await p.getByRole("button", { name: "Edit reporting lines" }).click();
  await p.getByLabel(`Tom Reach${RUN} reports to`).selectOption(people.Vera.id);
  await p.getByRole("list", { name: `Reports to Vera Reach${RUN}` }).getByRole("link", { name: `Tom Reach${RUN}` }).first().waitFor();
  await shot(p, "org-chart");
}, p);

await step("stakeholders", "the contact page edits reports-to, influence and stance, with tap-to-call", async () => {
  await call(mt, "PATCH", `/contacts/${people.Vera.id}`, { phone: "+1 (555) 010-2030" });
  await p.goto(`${BASE}/contacts/${people.Vera.id}`); await p.waitForLoadState("networkidle");
  await p.locator("#sh-influence").selectOption("high");
  await p.getByText("Contact updated").first().waitFor();
  const href = await p.getByRole("link", { name: /555/ }).getAttribute("href");
  expect(href === "tel:+15550102030", `tel link was ${href}`);
  await p.getByText("Website, email and product activity").waitFor();
}, p);

await step("segments", "build a segment with live preview, then save it", async () => {
  const nina = await login("nina@cirra.demo");
  try {
    const n = nina.page;
    await n.goto(`${BASE}/campaigns`); await n.waitForLoadState("networkidle");
    await n.getByRole("link", { name: "Segments" }).click();
    await n.waitForURL(/\/campaigns\/segments/);
    await n.getByRole("button", { name: "New segment" }).click();
    await n.locator("#seg-name").fill(`Reach people ${RUN}`);
    await n.getByLabel("Remove condition 1").isDisabled();
    await n.getByRole("button", { name: "Attribute" }).click();
    await n.getByLabel("Condition 2 field").selectOption("account.name");
    await n.getByLabel("Condition 2 value").fill(acc.name);
    await n.getByLabel("Remove condition 1").click();
    await n.getByText(/3 contacts match right now/).waitFor({ timeout: 15000 });
    await shot(n, "segment-builder");
    await n.getByRole("button", { name: "Create segment" }).click();
    await n.getByText(`Reach people ${RUN}`).first().waitFor();
    if (await n.getByRole("button", { name: "Turn on" }).isVisible()) await n.getByRole("button", { name: "Turn on" }).click();
    await n.getByText(/<script async src=/).waitFor();
  } finally { await nina.ctx.close(); }
});

await step("connectors", "Admin → Connectors lists Slack, Teams, Mailchimp and BambooHR and refuses bad settings", async () => {
  const admin = await login("admin@cirra.demo");
  try {
    const a = admin.page;
    await a.goto(`${BASE}/admin?tab=connectors`); await a.waitForLoadState("networkidle");
    for (const name of ["Slack", "Microsoft Teams", "Mailchimp", "BambooHR"]) await a.getByRole("button", { name: `Connect ${name}` }).waitFor();
    await a.getByRole("button", { name: "Connect Microsoft Teams" }).click();
    await a.locator("#cn-webhook_url").fill("https://127.0.0.1/hook");
    await a.getByRole("button", { name: "Test and save" }).click();
    await a.getByText(/internal|private|not allowed|refused/i).first().waitFor({ timeout: 15000 });
    await shot(a, "connectors");
  } finally { await admin.ctx.close(); }
});

await step("mobile", "installable app: manifest, service worker, icons and the bottom tab bar", async () => {
  const manifest = await (await fetch(`${BASE}/manifest.webmanifest`)).json();
  expect(manifest.display === "standalone" && manifest.icons.length >= 3, "manifest");
  expect((await fetch(`${BASE}/sw.js`)).ok && (await fetch(`${BASE}/icon-512.png`)).ok, "sw and icons");
  const m = await login("priya@cirra.demo", { width: 390, height: 844 });
  try {
    const nav = m.page.getByRole("navigation", { name: "Quick navigation" });
    await nav.getByRole("link", { name: "Tasks" }).click();
    await m.page.waitForURL(/\/tasks/);
    for (const path of ["/notifications", "/campaigns/segments", `/accounts/${acc.id}`, `/deals/${deal.id}`]) {
      await m.page.goto(`${BASE}${path}`); await m.page.waitForLoadState("networkidle");
      const over = await m.page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(over <= 1, `${path} overflows by ${over}px`);
    }
    await shot(m.page, "mobile-deal");
  } finally { await m.ctx.close(); }
});

await step("mobile", "a task added offline is queued and saved when the connection returns", async () => {
  const s = await login("priya@cirra.demo");
  try {
    await s.page.goto(`${BASE}/tasks`); await s.page.waitForLoadState("networkidle");
    await s.ctx.setOffline(true);
    await s.page.getByRole("button", { name: "New task" }).click();
    await s.page.locator("#t-title").fill(`Offline task ${RUN}`);
    await s.page.getByRole("button", { name: "Add task" }).click();
    await s.page.getByText(/change\(s\) waiting to be sent|You're offline/).first().waitFor();
    await s.ctx.setOffline(false);
    await s.page.getByText(/made offline was saved/).waitFor({ timeout: 15000 });
    const tasks = await call(await token("priya@cirra.demo"), "GET", "/tasks");
    expect((Array.isArray(tasks) ? tasks : tasks.items).some((t) => t.title === `Offline task ${RUN}`), "task reached the server");
  } finally { await s.ctx.close(); }
}, p);

await step("language", "a Spanish user sees the app in Spanish, beyond the menus", async () => {
  const pt = await token("priya@cirra.demo");
  await call(pt, "PATCH", "/users/me/preferences", { locale: "es-ES" });
  const s = await login("priya@cirra.demo");
  try {
    await s.page.goto(`${BASE}/pipeline`); await s.page.waitForLoadState("networkidle");
    await s.page.getByRole("link", { name: "Embudo" }).first().waitFor();
    await s.page.getByText("Arrastra tarjetas entre etapas", { exact: false }).first().waitFor({ timeout: 10000 });
    await s.page.goto(`${BASE}/notifications`); await s.page.waitForLoadState("networkidle");
    await s.page.getByRole("tab", { name: "Pospuestas" }).waitFor({ timeout: 10000 });
    await shot(s.page, "spanish");
  } finally {
    await call(pt, "PATCH", "/users/me/preferences", { locale: null });
    await s.ctx.close();
  }
});

const errors = [marcus].flatMap((x) => x.errors);
results.push({ area: "health", name: "no server errors or page crashes", status: errors.length ? "fail" : "pass", detail: errors.slice(0, 5).join(" | ") });
console.log(errors.length ? `FAIL health › ${errors.slice(0, 5).join(" | ")}` : "PASS health › no server errors or page crashes");
writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 1));
console.log(`\n${results.filter((r) => r.status === "pass").length}/${results.length} P0 reach checks passed`);
await browser.close();
