# Region pivot to Assam — evaluated and REJECTED, 2026-08-22

**Decision record. Do not re-litigate without new external evidence.** Darshil asked whether the
project should move from Bengaluru to Assam, with a pre-registered kill rule: *"does something
like this exist for assam like it does in mumbai — if yes stop immediately"*, and a second
condition: *only proceed if there is enough data that Phase 3 does not block again.*

**Both conditions failed. The pivot is not taken. No workspace cleanup, archiving, or Assam
scaffolding was performed.**

## (a) The kill rule fired — an operational street-level equivalent already exists for Guwahati

**FINDING (primary source, TERI press release for the NDMA-partnered launch):** the Flood Early
Warning System (FEWS) for Guwahati, launched August 2020 by TERI with NDMA, IMD, ASDMA, Guwahati
Municipal Corporation, NESAC and TERI SAS, is a *"fully automated web-based tool"* with
*"inbuilt urban drainage to predict flood at **street-level accuracy**"*, visualising flood
levels and hotspots over Google Maps, 72-hour lead time, driven by IMD WRF precipitation at 3 km
hourly resolution into a hydrodynamic model.

Substitute Guwahati for Bengaluru in this project's fixed problem statement and that *is* FEWS.

Supporting systems, any one of which independently fires the rule:

- **FLEWS** (NESAC + ASDMA + IMD + CWC + NEEPCO + WRD): operational since the 2009-10 Lakhimpur
  pilot, now covering **all flood-prone districts of Assam** with revenue-circle-level alerts,
  WRF + HEC-HMS, 24-36 h lead, ~75% year-on-year alert success.
- **Lodestar** (deploying now, Guwahati): multi-hazard warning using real-time sensors, AI,
  hydrological and hydraulic modelling, CCTV feeds and citizen reports, 6 h ahead. This closes
  the "FEWS was only a 2020 pilot" objection — the niche is actively being re-occupied.
- **Google Flood Hub** with a CWC agreement, covering Brahmaputra and Barak inundation.
- Published hybrid CNN-LSTM flood models trained on Guwahati events 2015-2024 — the neural
  surrogate angle is occupied too.

**The pivot would invert the project's novelty position, not improve it.** Mumbai has iFLOWS;
Guwahati has FEWS, FLEWS and Lodestar; **Bengaluru has no operational street-level system** —
which is the reason it was a defensible target in the first place.

## (b) Independent corroboration — the data situation is worse on the exact axis that blocked us

Presented as corroboration only; (a) is load-bearing on its own.

1. **The primary forcing variable is officially classified.** FINDING (Nature *Scientific Data*,
   GUARDIAN dataset paper, open access): *"the classified rivers encompass major transboundary
   river basins such as the Ganga, Brahmaputra, and Indus."* India-WRIS carries daily water-surface
   elevation and discharge for **unclassified** rivers only, and *"the data is only available up
   until year 2020 with a significant number of data gaps."* Access to classified-basin data
   requires a justified request to a CWC Chief Engineer.
   This is strictly worse than the KSNDMC problem: rain gauges had IMERG as a satellite
   substitute, which is how the current forcing block was resolved. **There is no satellite
   substitute for river discharge.** It is a bureaucratic Rule-5 block on the dominant variable.
2. **The pluvial-only steelman fails on a physical coupling.** The strongest argument for Assam is
   that Guwahati's urban flooding is local-rain driven (Meghalaya hills, Bharalu basin) and so
   escapes the classified-discharge problem. It does not: the Bharalumukh **sluice gates must
   close when the Brahmaputra rises above 45 m**, after which the city depends on mechanical
   pumping. Urban Guwahati is boundary-conditioned on the classified variable. (Engineering
   judgment from sourced facts, not a measurement.)
3. **The terrain blocker returns, load-bearing rather than partial.** Low-resolution global DEMs
   do not resolve embankments — the reported errors are largest exactly at embankment portions —
   and embankment breaches are the primary Assam flood mechanism. In Bengaluru the DEM-resolution
   limit cost us underpasses, a subset of locations; in Assam it would compromise the main
   mechanism. Open LiDAR for the Brahmaputra floodplain is not available.
4. **Depth ground truth is weaker.** The curation method that produced this project's GT — news and
   social imagery with visual depth cues — depends on dense English-language metro coverage.
   Assam flooding is largely rural and agricultural (the 2026 event submerged ~17,198 ha of crops
   across eight districts), with correspondingly thinner street-level depth imagery.
5. **The one genuine advantage is an axis already solved here.** Sentinel-1 performs well over
   open floodplain water, so extent validation would be easier — but Goal A has already validated
   both instruments for Bengaluru at 0.97/0.79 and 0.98/0.82 recall.

## The solver does not transfer (engineering judgment — most decision-relevant fact)

The Phase 2 solver is a 2D overland-flow (ACC) scheme with rainfall forcing over an urban DEM: a
**pluvial** tool. Assam is a **fluvial** problem requiring 1D channel routing coupled to 2D
floodplain spreading, upstream discharge boundary conditions, and embankment-breach logic. That
is a Phase 2 rewrite, not a DEM swap. Reuse would be limited to the environment, parts of the
terrain pipeline, and parts of the validation stack — not the physics core the project is built
around. The 2026 event illustrates the regime difference: it was triggered by a **cloudburst in
Mon district, Nagaland**, surging the Dikhow and Dhansiri tributaries — out-of-state upstream
forcing, unreachable by city rainfall telemetry.

## What was deliberately NOT done

No cleanup, no archiving, no Assam CLAUDE.md or rule docs, no archive prompt — the pivot
condition failed, and independently **Goal D is in flight**, where this project's own standing
rule is that nothing touches the tree while an expensive run is running.

Deferred, for after Goal D's verdict: worktree hygiene (11 sibling worktrees currently exist).
The `.venv` is retained regardless.

## Open question for Darshil (does not change the verdict)

`CLAUDE.md` marks the problem statement *"fixed, do not reword"* and it names Bengaluru. If that
statement is competition-issued for the SIH internal round, a region switch may be disqualifying
irrespective of the science. Worth confirming, but the verdict above stands either way.

## Sources

- TERI / NDMA Guwahati FEWS launch: https://www.teriin.org/press-release/teri-and-ndma-launch-flood-early-warning-system-fews-predict-floods-guwahati
- FLEWS (ASDMA): https://www.asdma.gov.in/project_flood_warning.html and https://www.asdma.gov.in/pdf/publication/FLEWS.pdf
- NESAC disaster management support: https://nesac.gov.in/scientific-programmes/disaster-management-support/
- Lodestar Guwahati deployment: https://assamtribune.com/guwahati/guwahati-set-to-deploy-multi-hazard-early-warning-system-for-floods-1615852
- CWC classified-basin policy and WRIS coverage (GUARDIAN, Nature Scientific Data): https://pmc.ncbi.nlm.nih.gov/articles/PMC11489425/
- CWC hydrological data policy: https://cwc.gov.in/en/hydrological-data
- 2026 Assam floods: https://en.wikipedia.org/wiki/2026_Assam_floods
- Guwahati urban flood mechanism / Bharalu basin: https://gmc.assam.gov.in/frontimpotentdata/flood-free-guwahati
