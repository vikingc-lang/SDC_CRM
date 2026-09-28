# Cirra vs Salesforce: fitment and maturity scorecard

*Updated 2026-09-28, after Waves 1–5. An expert estimate, not a formal benchmark; scored the same way as the
original assessment of 2026-09-27 so the numbers compare like-for-like.*

## Method

- **Fitment:** how much of Salesforce's functional scope Cirra covers, where Salesforce is 100% (Sales, Service and
  Marketing Cloud, CPQ and Data Cloud together).
- **Maturity (1–5):** 1 prototype · 2 core flows work and are tested · 3 robust and configurable · 4 enterprise scale
  and ecosystem · 5 market-leading. Salesforce sits at about 4.5–5 on most modules.
- **Overall:** P0 modules count double. **Composite** = 60% fitment + 40% maturity (as a percentage of 5).

## Summary

| | Original | After Wave 1 | **After Wave 5** |
|---|---|---|---|
| Fitment (P0 ×2) | 57% | 60% | **65%** (P0 67%, P1 55%) |
| Maturity | 2.6 | 2.7 | **3.0** (P0 3.1, P1 2.6) |
| Composite | 55 | 58 | **62 / 100** |

## By module

| # | Module | Pri | Fitment | Maturity | What moved it | Biggest remaining gap |
|---|---|---|---|---|---|---|
| 1 | CRM platform foundation | P0 | 55% → **68%** | 2.5 → **3.0** | Custom objects, validation rules, config export/import, CI, separate scheduler | Page layouts and record types, true sandboxes, metadata API, mobile app |
| 2 | Accounts | P0 | 75% → **80%** | 3.0 → **3.5** | List views and inline editing, sharing rules, related custom records | Account teams, person accounts |
| 3 | Contacts & relationships | P0 | 70% → **74%** | 3.0 → 3.0 | List views, bulk actions, custom-field security | One contact across several accounts, data enrichment |
| 4 | Leads | P0 | 75% → **80%** | 3.0 → **3.5** | List views and inline editing, validation rules, email opens and clicks feed scoring, nurture journeys | Visual assignment-rule and web-to-lead builders |
| 5 | Opportunities / pipeline | P0 | 75% → **77%** | 3.0 → 3.0 | Validation rules, field security, previous-period comparison | Opportunity teams and splits, product schedules, list view on the pipeline page |
| 6 | Activities & engagement | P0 | 60% → **63%** | 2.5 → 2.5 | Email tracking, emailed case replies with threading | Outlook/Gmail add-ins, calendar sync, sales cadences |
| 7 | Products & catalog | P0 | 65% → 65% | 3.0 → 3.0 | — | Guided product configuration, multi-dimensional pricing |
| 8 | CPQ / quotes | P1 | 55% → 55% | 2.5 → 2.5 | — | Guided selling, amendments, co-termed renewals |
| 9 | Orders & contracts | P1 | 55% → 55% | 2.5 → 2.5 | — | Amendments, order orchestration |
| 10 | Forecasting & revenue | P1 | 60% → **62%** | 3.0 → 3.0 | Period comparisons, scheduled report delivery | Forecasts by product or territory, pipeline inspection |
| 11 | Territory / quota / incentive | P1 | 45% → **47%** | 2.0 → 2.0 | Criteria-based sharing rules support territory visibility | Territory models and hierarchies |
| 12 | Marketing & campaigns | P1 | 30% → **48%** | 2.0 → **2.5** | Nurture journeys with open/click branching, signed tracking, engagement scoring, email metrics | A/B tests, landing pages, segment builder, bounce handling, SMS |
| 13 | Customer service | P1 | 50% → **65%** | 2.5 → **3.0** | Email-to-case with threading, presence and capacity routing, routing console, unmatched-mail inbox | Live chat, customer self-service portal, entitlements, macros |
| 14 | Customer success | P1 | 55% → 55% | 2.5 → 2.5 | — | Health scorecards and playbooks |
| 15 | Partner / channel | P1 | 55% → 55% | 2.5 → 2.5 | — | Partner marketing funds, tier automation |
| 16 | Customer data / 360 | P0 | 40% → **47%** | 2.0 → **2.5** | Upsert by external ID, custom objects on the account, email engagement data | Identity resolution, streaming data intake, audiences |
| 17 | Workflow automation | P0 | 45% → **58%** | 2.5 → **3.0** | Webhook, Slack and Teams actions, validation rules, scheduled deliveries, journeys | Visual flow builder; workflows don't run on custom objects |
| 18 | Analytics & reporting | P0 | 55% → **75%** | 2.5 → **3.5** | Drill-down, pivot, dashboard filters, period comparison, subscriptions, reports on custom objects, field security in reports | CRM Analytics / Tableau-class analysis |
| 19 | AI / agentic | P0 | 50% → 50% | 2.5 → 2.5 | Quick-log now respects scope (hardening only) | Autonomous agents, AI actions, an AI trust layer |
| 20 | Integration, API & events | P0 | 50% → **68%** | 2.5 → **3.5** | Upsert and bulk APIs, inbound email webhook, custom-object API, rate limits, request tracing | Streaming and change-data-capture APIs, an integration hub |
| 21 | Admin, security & governance | P0 | 60% → **72%** | 3.0 → **3.5** | Field security, sharing rules, config transfer, rate limits, security headers, health checks, CI | Field security on standard fields, encryption and event monitoring, sandboxes |

## Reading it

- **Biggest gains:** analytics, integration, customer service and marketing, plus the platform and admin layer
  (custom objects, field security, sharing rules).
- **Maturity crossed 3.0 overall:** robust and configurable, not just working core flows. That rests on real test
  coverage (176 backend tests, about 320 browser checks, CI on every push) and operational basics (rate limits,
  health checks, a separate scheduler).
- **Unchanged modules:** products, CPQ, orders, customer success and partners weren't in any wave's scope.
- **Largest remaining gaps:** AI agents, CPQ depth, a visual flow builder, a customer portal with live chat, and
  marketing A/B tests and landing pages. That's also where Salesforce's lead is widest.
- **Where Cirra beats Salesforce:** private-cloud deployment with no outbound data, local AI, an ERP-native path from
  quote to order, and e-signature included.
