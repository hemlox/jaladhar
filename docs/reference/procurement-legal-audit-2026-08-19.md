# Procurement / Litigation / Oversight audit for per-location underpass levels

**Date:** 2026-08-19. **Git SHA:** `6e52b131b61853a4ac0f9b34417415d3b3215f4c`.
**Companion to:** [`underpass-sources-audit-2026-08-18.md`](underpass-sources-audit-2026-08-18.md) (item AO).
**Scope:** procurement (BBMP/KPPP tenders), litigation (Karnataka HC / Lokayukta), oversight (CAG, media investigations) as carriers of ONE number per underpass: road surface level beneath the bridge, sump invert, or vertical clearance.
**Measured result:** 20 items probed across the three families. **0 of 50 register locations** gained a realized level from any of them. The block on item AO survives; what is new is that it now has a documented evidence trail and two concrete request paths.

## Per-item table

| # | Item | Exists | Vertical data | Verdict |
|---|---|---|---|---|
| 1 | KPPP e-procurement portal (kppp.karnataka.gov.in) | yes | **blocked** — search API returns `401 Full authentication is required` | [BLOCKED] |
| 2 | BBMP tender `EE(RI-Spl)/TEND/14/16-17` — Suranjandas Rd underpass IFT, 07.02.2017 (Wayback) | yes | **no** — cost/EMD/schedule only; design is turnkey ("Tenderer's own Design on the basis of Parameters fixed by the BBMP") | [FINDING] location confirmed; levels sit in the gated tender documents |
| 3 | BBMP total-station survey empanelment (Mahadevapura 2017, Wayback) | yes | **no** — building mapping, not underpasses | [FINDING] dead end |
| 4 | BBMP archived tender library (Wayback, ~200 tender PDFs) | yes | **no** — 1 underpass match total; 0 pumping/subway/sump matches | [FINDING] the archive exists and is searchable; no level-bearing doc found |
| 5 | bbmp.gov.in (live) | yes | unreachable from this network (read timeout, both http/https) | [BLOCKED] network-level |
| 6 | bbmpgov.in | yes | parked GoDaddy domain, not BBMP | dead end |
| 7 | eprocure.gov.in (national) | yes | no Karnataka underpass tenders | dead end |
| 8 | Lokayukta suo motu case, KR Circle underpass (techie drowning, May 2023) | yes | **no** — "neck-deep water" (qualitative); negligence finding against BBMP SWD; divisions blamed each other | [FINDING] oversight trail exists; inquiry report not public |
| 9 | Lokayukta names ACS UDD, GBA CC, BCCC Commissioner as respondents (06 May 2026) | yes | **no** — underpass re-flooded 29 Apr 2026 | [FINDING] |
| 10 | Upalokayukta 45-day deadline, Kodigehalli + Sahakaranagar underpasses (Feb 2022) | yes | **no** | [FINDING] |
| 11 | HC PIL — Suranjandas Rd underpass tree felling (Aug 2021, The Hindu) | yes | **no** — technical expert committee report referenced but not published | [FINDING] litigation trail for this location |
| 12 | HC/NGT PILs on Hebbal tunnel road project (2025-26) | yes | **no** — planned tunnel, not existing underpasses | [FINDING] |
| 13 | services.ecourts.gov.in / karnatakajudiciary.kar.nic.in | yes/no | case search needs CNR/case no./party name; judiciary site times out | [BLOCKED] request path = get CNR from registry |
| 14 | CAG Report 8 of 2025 — Disaster Management in Karnataka | yes | **no levels**, but §5.3.3.3/§5.3.3.4 document the KSNDMC WLS network (105 sensors, 5 in flood-vulnerable streets) and its failure | [FINDING] strongest oversight trail for the sensor story |
| 15 | CAG Karnataka Local Bodies Report No.4 of 2016 | yes | **no** — full text checked, no underpass/pump/sump/invert content | [FINDING] |
| 16 | Citizen Matters on CAG SWD audit (Dec 2021) | yes | **no** — documents that BBMP SWD keeps no work registers/drawings, so dewatering/pumping records are not auditable | [FINDING] explains the absence |
| 17 | BBMP 211 vulnerable SWD points (Jun 2020) | yes | **no** — per-SWD vulnerability buffers, not levels | [FINDING] |
| 18 | BTP 54 flood-prone roads / ≥12 flood-prone underpasses (Jul 2022) | yes | **no** — qualitative "several feet of water" | [FINDING] |
| 19 | Sankey Rd underpass injection well (Jan 2020, The Hindu) | yes | **no** — 230 ft recharge pit depth, not road level | [FINDING] |

**Not probed / stops:** (i) KPPP tender documents behind login (item 1); (ii) Lokayukta inquiry report (items 8-9); (iii) HC orders (items 11-12); (iv) BBMP SWD zone records (items 16-17).

## Measured quotes (verbatim, with URL)

- Suranjandas Rd IFT (2017): *"Construction of Grade seperators in junction of Suranjandas road- Old Madras road" on Turnkey, Lump Sum, Fixed Price, No Variation contract basis based on Tenderer's own Design on the basis of Parameters fixed by the BBMP*. Amount Rs 4485.00 lakh, EMD Rs 45.00 lakh, 9 months. — `https://web.archive.org/web/20170922193918id_/http://bbmp.gov.in/documents/10180/4672507/Tender+Notification+no+14+Suranjan+das+road+Underpass.pdf/cfc47d03-40b6-4d13-b5de-26659fa1ac1a` (copy: `data/raw/underpass_search/results/suranjan_das_tender.pdf`)
- KPPP search API: *`{"type":"...problem-with-message","title":"Unauthorized","status":401,"detail":"Full authentication is required to access this resource",...}`* — `https://kppp.karnataka.gov.in/works-tender-service/v1/api/tender-service/search-eproc-tenders?page=0&size=5`
- Lokayukta notice (2023): *"prima facie, there has been negligence on the part of the officers entrusted with the responsibility of maintenance of stormwater drains. ... every citizen who lives within the municipal area or a local body has a right to have roads maintained in a reasonable condition. This is a part of the fundamental right guaranteed under Article 21 of the Constitution of India."* — DH, `https://www.deccanherald.com/amp/story/india/karnataka/bengaluru/kr-circle-underpass-flood-lokayukta-initiates-suo-motu-case-against-bbmp-1222306` (Wayback snapshot)
- BTP survey (2022): *"A recent survey by the Bengaluru Traffic Police (BTP) has revealed that the city has 54 flood-prone roads... The list includes at least a dozen underpasses."* Flood-prone grade separators: *"Okalipuram, Panathur, Ramamurthynagar, Vatal Nagaraj Road in Rajajinagar, JP Nagar and Carmelaram ... submerge in several feet of water."* — DH, `https://web.archive.org/web/20220717114923/https://www.deccanherald.com/amp/city/top-bengaluru-stories/despite-bbmp-s-efforts-city-s-underpasses-drown-in-downpour-1127358.html`
- CAG 2025: *"As of December 2023, despite an expenditure of ₹1.61 crore with respect to the project pertaining to 'Preparation of urban flood model for Bengaluru' the flood forecast alert model is yet to be prepared."* and *"49 out of 100 WLS installed on storm water drains were non-functional and no information/data was available in respect of five WLS reportedly installed at flood vulnerable streets."* — `https://cag.gov.in/webroot/uploads/download_audit_report/2025/REPORT-8-OF-2025---ENGLISH-0694389fd547e10.69426873.pdf` (copy: `data/raw/underpass_search/results/cag_disaster_mgmt_full.pdf`, text: `results/cag_disaster_mgmt_full.txt`)

## Highest-value next step

**The KSNDMC 105-sensor water-level network.** The CAG report (item 14) proves it exists (100 WLS on drains + 5 in flood-vulnerable streets, installed Jan 2022) and that its data is neither public nor archived. A formal KSNDMC data request — draft already at `docs/archive/ksndmc_data_request_DRAFT.md` — is the only route on this audit's books to per-location water-level time series at the flood-prone street points. Second: the **KR Circle Lokayukta inquiry report** (items 8-9) is the most likely litigation document to carry pump/sump/level details, since engineers from both BBMP divisions submitted reports to the Lokayukta; it is not public and needs an RTI to the Karnataka Lokayukta.

## Dead ends, with reasons

- **KPPP automated tender search** — the Angular SPA's REST endpoint (`*tender-service/search-eproc-tenders`) returns 401 without a login session. The portal is real and well-architected (API URLs extracted from `eproc-gts-ui/main.js`), but every search path needs credentials.
- **bbmp.gov.in live site** — TCP connects, HTTP response never arrives (read timeout). Wayback holds ~200 tender PDFs; only one mentions an underpass and it carries no levels.
- **bbmpgov.in** — a parked GoDaddy website-builder page, not the BBMP.
- **DuckDuckGo / Mojeek / Bing** — timeouts/403 from this IP; Google News RSS worked and became the primary discovery channel.
- **Tender documents (NIT/BOQ) carrying sump inverts** — for BBMP underpass pumping works these live only behind the KPPP login; no copies survive in the Wayback index (0 URL hits for underpass/subway/pump on `eproc.karnataka.gov.in` and `kppp.karnataka.gov.in`).

## Failed search terms

Google News RSS (hl=en-IN&gl=IN&ceid=IN:en) returned no usable results for:
- `BBMP underpass pumping station tender`
- `BBMP underpass pumping station dewatering tender`
- `Baiyappanahalli underpass pumping`
- `Bengaluru 2022 floods expert committee report underpass levels`
- `Bengaluru flood committee report PDF 2022`
- `Bengaluru floods expert committee 2022 report BBMP recommendations`
- `Bengaluru flood expert committee report submitted government 2023`
- `underpass pumping station Bengaluru BBMP dewatering monsoon 2024`
- `BBMP underpass pumping stations list maintenance`
- `BBMP water level sensor flood vulnerable streets underpass Bengaluru`

The 2022 floods expert-committee report is referenced in coverage but its full text is not indexed in the RSS feed or the Wayback index.

## Rules compliance

- All recorded values are verbatim quotes with URLs (Rule 3). No synthetic or inferred numbers.
- Items that need credentials/requests are labelled [BLOCKED] with the request path in the CSV's `request_path_if_blocked` column (Rule 5).
- Findings vs readings are labelled in the table; nothing downstream is premised on a reading.
- Manifest at `data/raw/underpass_search/procurement_legal_manifest.json` (git SHA, status, URLs probed with HTTP status).