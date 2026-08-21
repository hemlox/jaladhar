# KSNDMC historical rainfall data request — DRAFT

**Status:** name and email filled in from git config. **[COLLEGE/DEPARTMENT] and
[PHONE] are still placeholders** — I don't have that information and won't guess it.
Fill those two, then send from your own address. Resolves `OPEN-ITEMS.md` item 1b.

**To:** `Director@ksndmc.org`
**Cc:** `office@ksndmc.org`, `dmc.kar@gmail.com`
**Subject:** Request for historical telemetric rain gauge data — Bengaluru, Aug–Sept 2022 (student research project)

---

Respected Sir/Madam,

I am Darshil Trivedi, a student at [COLLEGE/DEPARTMENT], writing on behalf of a six-member
team developing an urban flood forecasting system for Bengaluru as part of the Smart
India Hackathon.

Our system predicts street-level flood depths from rainfall using a physics-based
hydraulic model of the city's terrain. To demonstrate that it works, we need to
reproduce a flood that actually happened and compare our predictions against what was
observed. The September 2022 Bengaluru floods are the natural test case.

**What we are requesting**

Historical rainfall records from KSNDMC's telemetric rain gauge network for:

- **Districts:** Bengaluru Urban, Bengaluru Rural, Bengaluru South
- **Period:** 28 August 2022 to 10 September 2022 (inclusive)
- **Resolution:** 15-minute readings, matching the interval published on the KSNDMC
  portal. Hourly would also be usable; daily totals unfortunately would not, as our
  model operates on a 1–6 hour forecast horizon.
- **Fields:** station name/ID, date, time, and rainfall — the same fields shown on the
  public "Current Rainfall Information" page.
- **Format:** CSV or Excel is ideal; any machine-readable format is fine.

**Why we are writing rather than downloading**

We have built our live-mode system against KSNDMC's public rainfall pages and it works
well. However, those pages serve current-day readings only, and we could find no public
route to historical records.

Without gauge data, our only alternative for the September 2022 event is NASA's GPM
IMERG satellite product at roughly 11 km resolution. The entire BBMP area falls within
just nine IMERG cells, so all 198 wards effectively share nine rainfall values. Your
gauge network provides several hundred measurement points across the same area. The
difference in what we can demonstrate is substantial.

**Our commitments**

- The data will be used solely for this non-commercial student research project.
- KSNDMC will be credited as the data source in our submission, documentation, and any
  presentation or publication.
- We will not redistribute the raw data.
- We would be glad to share our results with KSNDMC, including our reconstruction of the
  September 2022 event and our assessment of how well it matches what was observed. If
  the work is useful to you, we would welcome the opportunity to present it.

If a formal application, undertaking, or fee is required, please let us know the
procedure and we will follow it. If a shorter period or coarser resolution is easier to
provide, we would gratefully accept whatever can be shared.

Thank you for your time and for maintaining the telemetric network, which has been
genuinely valuable to our work.

Respectfully,

Darshil Trivedi
[COLLEGE / DEPARTMENT]
drshiltrivedi@gmail.com · [PHONE]

---

## Notes before sending

- **Still need:** college/department name and a phone number. Everything else is filled in. Do not send with brackets in it.
- **Send from an institutional address** if you have one — a college domain gets a
  materially better response rate from government offices than a personal Gmail.
- **A faculty member cc'd or as co-signatory helps.** Government data requests from an
  identifiable institution are treated more seriously than from an individual student.
- **If there is no reply in ~7 days,** a polite follow-up, or a phone call to the office
  number listed on `ksndmc.org`, is reasonable. Phone often works better than email for
  Indian government offices.
- **The RTI route is a fallback,** not a first move. A Right to Information application
  is legally guaranteed to get a response and costs ₹10, but it is slower (30 days) and
  more adversarial in tone than a cooperative request. Only escalate if the friendly
  approach goes unanswered.
- **Do not pause other work waiting on this.** Reply latency is days to weeks. The
  running 15-minute capture (`src/jaladhar/forcing/ksndmc_logger.sh`) may deliver a
  fresh flood event at full gauge resolution before any reply arrives, which would make
  this request moot.
