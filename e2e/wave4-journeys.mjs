// Browser journeys for Wave 4 (email-to-case, presence routing, support inbox, nurture journeys with tracking):
// npm i playwright && node e2e/wave4-journeys.mjs <outdir>  (E2E_CHANNEL=msedge for Edge)
// Needs INBOUND_EMAIL_SECRET set for the API (E2E_INBOUND_SECRET, default "local-dev-inbound-secret").
import { chromium } from "playwright";
import { execSync } from "child_process";
import { mkdirSync, writeFileSync } from "fs";

const OUT = process.argv[2] ?? "./wave4-out"; mkdirSync(`${OUT}/shots`, { recursive: true });
const BASE = "http://localhost:3000", API = "http://localhost:8000/api/v1";
const SECRET = process.env.E2E_INBOUND_SECRET ?? "local-dev-inbound-secret";
const RUN = Date.now().toString(36).slice(-5);
const results = [];
process.on("unhandledRejection", (e) => console.log("(ignored late rejection)", String(e).slice(0, 160)));
const browser = await chromium.launch(process.env.E2E_CHANNEL ? { channel: process.env.E2E_CHANNEL } : {});
const expect = (c, m) => { if (!c) throw new Error(m); };
const sql = (q) => execSync(`docker exec cirra_db psql -U cirra_user -d cirra_crm -Atc "${q}"`, { env: { ...process.env, MSYS_NO_PATHCONV: "1" } }).toString().trim();

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
const api = async (token, method, path, body, headers = {}) => {
  const r = await fetch(API + path, { method, headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), "content-type": "application/json", ...headers },
    body: body ? JSON.stringify(body) : undefined });
  const text = await r.text(); let data; try { data = JSON.parse(text); } catch { data = text; }
  return { status: r.status, data };
};
const inbound = (m) => api(null, "POST", "/inbound/email", { message_id: `<${RUN}-${Math.random().toString(36).slice(2)}@customer.example>`, ...m },
  { "X-Cirra-Inbound-Secret": SECRET });
const toast = (page, text) => page.locator("[data-sonner-toast]", { hasText: text }).first().waitFor({ timeout: 15000 });
const shot = (page, name) => page.screenshot({ path: `${OUT}/shots/${name}.png` });

const QUEUE = `Priority support ${RUN}`, ADDRESS = `priority-${RUN}@support.cirra.example`;
const admin = await login("admin@cirra.demo");
const a = admin.page;
let queueId = "";

// ---- admin: a presence-routed queue with a support address --------------------------------------------
await step("setup", "admin creates a queue with a support address and presence routing", async () => {
  await a.goto(`${BASE}/admin?tab=service`); await a.waitForLoadState("networkidle");
  await a.fill("#queue-name", QUEUE);
  await a.getByRole("button", { name: "Add queue" }).click();
  const card = a.locator("div.rounded-md.border", { hasText: QUEUE }).first(); await card.waitFor();
  await card.getByLabel(`Add agent to ${QUEUE}`).selectOption({ label: "Sofia Lindqvist" });
  await card.getByText("Sofia Lindqvist").waitFor();
  await card.getByLabel(`Routing for ${QUEUE}`).selectOption("presence");
  await card.getByLabel("Support address").fill(ADDRESS);
  await card.getByRole("button", { name: "Save" }).click();
  await a.waitForTimeout(800);
  const q = (await api(admin.token, "GET", "/service/queues")).data.find((x) => x.name === QUEUE);
  expect(q && q.routing === "presence" && q.email_address === ADDRESS, JSON.stringify(q));
  queueId = q.id;
  await shot(a, "queue-settings");
  return `${QUEUE} ← ${ADDRESS}`;
}, a);

// ---- a customer emails in; the case waits until Sofia goes available -----------------------------------
const contact = (await api(admin.token, "GET", "/contacts")).data.find((c) => c.email && c.status === "active");
const sofia = await login("sofia@cirra.demo");
const s = sofia.page;
await api(sofia.token, "PUT", "/cases/presence/me", { status: "offline" });
let caseId = "";
await step("email-to-case", "a customer email opens a case in the addressed queue, waiting for an available agent", async () => {
  const r = await inbound({ from_email: contact.email, from_name: contact.name, to: [ADDRESS], subject: `Invoices don't load ${RUN}`,
    text: "Hi team,\nthe invoices page shows a spinner forever.\n\nThanks" });
  expect(r.status === 200 && r.data.status === "case_created", JSON.stringify(r.data));
  caseId = r.data.case_id;
  const c = (await api(sofia.token, "GET", `/cases/${caseId}`)).data;
  expect(c.channel === "email" && c.queue_id === queueId && c.owner_id === null, JSON.stringify({ ch: c.channel, q: c.queue_id, o: c.owner_id }));
  return `${c.case_number} via email, unassigned (Sofia offline)`;
}, s);

await step("routing", "Sofia goes Available on the Service page and the waiting case is pushed to her", async () => {
  const load = (await api(sofia.token, "GET", "/cases/routing")).data.agents.find((x) => x.name === "Sofia Lindqvist")?.open_cases ?? 0;
  await s.goto(`${BASE}/cases`); await s.waitForLoadState("networkidle");
  await s.getByLabel("My capacity").fill(String(load + 3)); await s.getByLabel("My capacity").blur();
  await s.waitForTimeout(600);
  await s.getByLabel("My status").selectOption("available");
  await toast(s, "You're available");
  await s.waitForTimeout(500);
  const c = (await api(sofia.token, "GET", `/cases/${caseId}`)).data;
  expect(c.owner === "Sofia Lindqvist", `owner ${c.owner}`);
  await shot(s, "presence-available");
  return `${c.case_number} → ${c.owner}`;
}, s);

await step("email-to-case", "the case page shows the email conversation and a customer's emailed reply", async () => {
  await s.goto(`${BASE}/cases/${caseId}`); await s.waitForLoadState("networkidle");
  await s.getByText("via email").waitFor();
  await s.fill("#case-reply", "Thanks, we're looking into it. Could you try another browser meanwhile?");
  await s.getByRole("button", { name: "Send reply" }).click();
  await toast(s, "Reply sent");
  const num = (await api(sofia.token, "GET", `/cases/${caseId}`)).data.case_number;
  const back = await inbound({ from_email: contact.email, to: [ADDRESS], subject: `Re: [${num}] Invoices don't load`, text: "Same in Firefox, sorry!" });
  expect(back.data.status === "appended", JSON.stringify(back.data));
  await s.reload(); await s.waitForLoadState("networkidle");
  await s.getByText("customer reply").waitFor();
  await s.getByText("Same in Firefox, sorry!").waitFor();
  const status = (await api(sofia.token, "GET", `/cases/${caseId}`)).data.status;
  expect(status === "open", `status ${status}`);
  await shot(s, "case-email-thread");
  expect(sofia.errors.length === 0, sofia.errors.join(";"));
  return `reply threaded onto ${num}; case back to open`;
}, s);

await step("routing", "the routing console shows presence, load and waiting work", async () => {
  await s.goto(`${BASE}/cases/routing`); await s.waitForLoadState("networkidle");
  await s.getByText(QUEUE).first().waitFor();
  const row = s.locator("tbody tr", { hasText: "Sofia Lindqvist" });
  await row.getByText("Available").waitFor();
  await shot(s, "routing-console");
  return (await row.innerText()).replace(/\s+/g, " ").slice(0, 120);
}, s);

await step("inbox", "unmatched mail waits in the Email inbox and is filed onto an account", async () => {
  const r = await inbound({ from_email: `buyer.${RUN}@newprospect-${RUN}.example`, from_name: "New Buyer", to: [ADDRESS], subject: `Pricing question ${RUN}`,
    text: "Do you offer volume pricing?" });
  expect(r.data.status === "unmatched", JSON.stringify(r.data));
  await s.goto(`${BASE}/cases/inbox`); await s.waitForLoadState("networkidle");
  const row = s.locator("tbody tr", { hasText: `Pricing question ${RUN}` }); await row.waitFor();
  await shot(s, "support-inbox");
  await row.getByRole("button", { name: "File as case" }).click();
  const dlg = s.getByRole("dialog");
  await dlg.getByLabel("Account", { exact: true }).selectOption({ index: 1 });
  await dlg.getByRole("button", { name: "File as case" }).click();
  await toast(s, "Filed as CS-");
  return await s.locator("[data-sonner-toast]").first().innerText();
}, s);
await api(sofia.token, "PUT", "/cases/presence/me", { status: "offline" });

// ---- marketing: a nurture journey with tracking --------------------------------------------------------
const nina = await login("nina@cirra.demo");
const n = nina.page;
const camp = (await api(nina.token, "POST", "/campaigns", { name: `Onboarding nurture ${RUN}` })).data;
const leadIds = [];
for (let i = 0; i < 3; i++) {
  const l = (await api(nina.token, "POST", "/leads", { first_name: `Ola${i}`, last_name: `Nurture${RUN}`, email: `ola${i}.${RUN}@nurture-e2e.example`,
    company_name: `Nurture Co ${RUN}`, consent: "granted", country: "United States" })).data;
  leadIds.push(l.id);
}
await api(nina.token, "POST", `/campaigns/${camp.id}/members`, { lead_ids: leadIds });
let journeyUrl = "";
await step("journeys", "marketer creates a journey from the campaign and edits its steps", async () => {
  await n.goto(`${BASE}/campaigns/${camp.id}`); await n.waitForLoadState("networkidle");
  await n.getByRole("tab", { name: "Nurture journeys" }).click();
  await n.getByRole("button", { name: "New journey" }).click();
  await n.waitForURL(/\/journeys\/[0-9a-f-]{36}/); journeyUrl = n.url();
  await n.getByLabel("Journey name").fill(`Welcome series ${RUN}`);
  await n.fill("#step-0-body", "Hi {{first_name}},\n\nHere's your getting-started guide: https://cirra.example/guide");
  await n.getByRole("button", { name: "Add email" }).click();
  await n.selectOption("#step-3-if", "clicked");
  await n.fill("#step-3-subject", "Want a walkthrough, {{first_name}}?");
  await n.fill("#step-3-body", "Book 20 minutes with us.");
  await shot(n, "journey-builder");
  await n.getByRole("button", { name: "Save changes" }).click();
  await toast(n, "Journey saved");
  return "welcome → wait 3 days → follow-up if not opened → walkthrough if clicked";
}, n);

await step("journeys", "activate enrolls the campaign's members and the first email goes out", async () => {
  await n.getByRole("button", { name: "Activate" }).click();
  await toast(n, "Journey live: 3 people enrolled");
  await n.getByRole("button", { name: "Process due steps now" }).click();
  await toast(n, "Processed 3 people");
  await n.reload(); await n.waitForLoadState("networkidle");
  await n.getByText(/3 sent · 0% opened/).waitFor();
  return "3 enrolled, step 1 sent to 3";
}, n);

await step("tracking", "an open is tracked and shows in the journey's results", async () => {
  const jid = journeyUrl.split("/").pop();
  const token = sql(`select token from email_sends where journey_id = '${jid}' order by sent_at limit 1`);
  const px = await fetch(`${API}/t/o/${token}.gif`);
  expect(px.headers.get("content-type") === "image/gif", "pixel");
  await n.reload(); await n.waitForLoadState("networkidle");
  await n.getByText(/3 sent · 33\.3% opened/).waitFor();
  await shot(n, "journey-results");
  expect(nina.errors.length === 0, nina.errors.join(";"));
  return "1 of 3 opened (33.3%)";
}, n);

await step("tracking", "the campaign overview shows email results", async () => {
  await n.goto(`${BASE}/campaigns/${camp.id}`); await n.waitForLoadState("networkidle");
  await n.getByText("Open rate").waitFor();
  await shot(n, "campaign-email-stats");
  await n.getByRole("tab", { name: "Nurture journeys" }).click();
  await n.getByRole("link", { name: `Welcome series ${RUN}` }).waitFor();
  return "stats tiles and journey list visible";
}, n);

// tidy: archive the demo journey and remove the test queue
const jid = journeyUrl.split("/").pop();
if (jid) await api(nina.token, "POST", `/journeys/${jid}/status?status=archived`);
if (queueId) await api(admin.token, "DELETE", `/service/queues/${queueId}`);  // its cases stay, without a queue

await browser.close();
writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 1));
console.log(`\n${results.filter((r) => r.status === "pass").length}/${results.length} wave-4 checks passed`);
