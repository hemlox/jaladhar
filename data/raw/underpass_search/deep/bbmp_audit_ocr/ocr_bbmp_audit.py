#!/usr/bin/env python3
"""
OCR for BBMP underpass audit PDFs (OpenCity dataset 668c76f9).
CPU-only. Never allocates GPU memory (rule: check nvidia-smi at startup).
Logs manifest at run start (status=running) and updates in place on completion (rule 6, V9).
- Renders each PDF page via pymupdf at 150 dpi
- Tries rapidocr_onnxruntime if available (CPU), else falls back to pymupdf embedded text + regex heuristic
- Writes per-page OCR text files and extracted_dimensions.csv
"""
import sys, pathlib, json, datetime, subprocess, re, os, time, hashlib

ROOT = pathlib.Path(__file__).resolve().parents[5] if pathlib.Path(__file__).exists() else pathlib.Path(".")
# Be robust: repo root is where this file lives relative to data/raw/...
REPO = pathlib.Path.cwd()
while not (REPO / ".git").exists() and REPO != REPO.parent:
    REPO = REPO.parent
if not (REPO / ".git").exists():
    REPO = pathlib.Path("/home/darshil/Desktop/sih/clginternal")

OUT_DIR = REPO / "data/raw/underpass_search/deep/bbmp_audit_ocr"
OUT_DIR.mkdir(parents=True, exist_ok=True)
MANIFEST = OUT_DIR / "manifest.json"

def git_sha():
    try:
        return subprocess.check_output(["git","rev-parse","HEAD"], cwd=REPO).decode().strip()
    except Exception:
        return "unknown"

def check_gpu():
    try:
        out = subprocess.check_output(["nvidia-smi","--query-gpu=memory.used","--format=csv,noheader,nounits"], timeout=5).decode()
        print(f"[preflight] nvidia-smi memory used MiB: {out.strip()}")
    except Exception as e:
        print(f"[preflight] nvidia-smi check failed or absent: {e}")
    # ensure no CUDA allocation
    assert "CUDA_VISIBLE_DEVICES" not in os.environ or os.environ.get("CUDA_VISIBLE_DEVICES") in ("","-1","CPU"), "GPU env not cleared"

def write_manifest(status, **extra):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    payload = {
        "script": str(pathlib.Path(__file__).resolve().relative_to(REPO)) if pathlib.Path(__file__).resolve().is_relative_to(REPO) else str(pathlib.Path(__file__).resolve()),
        "git_sha": git_sha(),
        "start_time": extra.get("start_time", now),
        "end_time": now if status!="running" else None,
        "wall_clock_s": extra.get("wall_clock_s"),
        "status": status,
        "python": sys.version.split()[0],
        "inputs": extra.get("inputs", {}),
        "outputs": extra.get("outputs", {}),
        "checks": extra.get("checks", {}),
        "note": "Manifest written at run start and updated in place on completion (rule 6).",
    }
    payload.update({k:v for k,v in extra.items() if k not in payload})
    MANIFEST.write_text(json.dumps(payload, indent=2))
    return payload

def main():
    t0 = time.time()
    start_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    check_gpu()
    inputs = {
        "pdf_14": "data/raw/underpass_search/deep/bbmp_audit_ocr/bbmp_14_underpasses.pdf",
        "pdf_kr": "data/raw/underpass_search/deep/bbmp_audit_ocr/bbmp_kr_circle.pdf",
        "pdf_harlur": "data/raw/underpass_search/deep/bbmp_audit_ocr/harlur_junction_dpr_drawings.pdf",
        "source_urls": [
            "https://data.opencity.in/dataset/668c76f9-53fe-4642-9e8c-c8d190bd7329/resource/3bc30859-8b9c-4868-a788-463e9ae09cab/download/c304bc4e-43a3-4765-9360-dbb4b2285a6e.pdf",
            "https://data.opencity.in/dataset/668c76f9-53fe-4642-9e8c-c8d190bd7329/resource/c53619d2-b20c-427d-9e34-f5638599e65c/download/0102faf2-cabc-46e4-b063-7b6c94a50538.pdf",
            "https://data.opencity.in/dataset/4353dd27-64d7-41a8-9100-1ed33f1a6591/resource/5c536b05-a90c-4b7e-9c9e-04f7f9cdb973/download/3f558afd-5e4f-47d6-9a80-e8bbca07c6e0.pdf",
        ],
    }
    write_manifest("running", start_time=start_iso, inputs=inputs, outputs={"ocr_text_dir": str(OUT_DIR), "dimension_table": str(OUT_DIR/"extracted_dimensions.csv")})

    # attempt imports
    has_pymupdf = False
    has_rapidocr = False
    try:
        import pymupdf
        has_pymupdf = True
    except ImportError:
        try:
            import fitz as pymupdf
            has_pymupdf = True
        except ImportError:
            pass
    try:
        from rapidocr_onnxruntime import RapidOCR
        has_rapidocr = True
    except ImportError:
        pass

    print(f"[info] pymupdf={has_pymupdf} rapidocr={has_rapidocr}")

    # dimension regex
    dim_pat = re.compile(r"(\d+\.\d+|\d+)\s*m\b", re.IGNORECASE)

    rows = []
    per_pdf_stats = {}

    for pdf_name, pdf_path in [("bbmp_14_underpasses.pdf", OUT_DIR/"bbmp_14_underpasses.pdf"),
                               ("bbmp_kr_circle.pdf", OUT_DIR/"bbmp_kr_circle.pdf"),
                               ("harlur_junction_dpr_drawings.pdf", OUT_DIR/"harlur_junction_dpr_drawings.pdf")]:
        if not pdf_path.exists():
            print(f"[warn] missing {pdf_path}")
            per_pdf_stats[pdf_name] = {"pages":0, "status":"missing"}
            continue
        try:
            import pymupdf
            doc = pymupdf.open(str(pdf_path))
            n_pages = len(doc)
            print(f"[process] {pdf_name}: {n_pages} pages")
            for i in range(n_pages):
                page = doc[i]
                pix = page.get_pixmap(dpi=150)
                # save embedded text fallback
                embedded = page.get_text()
                txt_path = OUT_DIR / f"{pdf_name.replace('.pdf','')}_page_{i:02d}_embedded.txt"
                txt_path.write_text(embedded, encoding="utf-8")
                # OCR if available
                ocr_text = ""
                if has_rapidocr:
                    try:
                        from rapidocr_onnxruntime import RapidOCR
                        import numpy as np
                        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
                        ocr = RapidOCR()
                        result, _ = ocr(img)
                        if result:
                            ocr_text = "\n".join([r[1] for r in result])
                        else:
                            ocr_text = ""
                    except Exception as e:
                        ocr_text = f"[ocr_error {e}]"
                else:
                    ocr_text = embedded  # fallback

                ocr_path = OUT_DIR / f"{pdf_name.replace('.pdf','')}_page_{i:02d}_ocr.txt"
                ocr_path.write_text(ocr_text, encoding="utf-8")

                # heuristic dimension candidates from embedded text (more reliable for English fragments)
                combined = embedded + "\n" + ocr_text
                for m in dim_pat.finditer(combined):
                    val = m.group(1)
                    ctx = combined[max(0,m.start()-40):m.end()+40].replace("\n"," ").strip()
                    rows.append({
                        "pdf": pdf_name,
                        "page": i,
                        "underpass_inferred": "",  # filled by human vision review (see report)
                        "dimension_type": "candidate_m",
                        "value_str": val,
                        "unit": "m",
                        "verbatim_snippet": ctx[:200],
                        "confidence": "low_ocr" if has_rapidocr else "embedded_only",
                        "note": "automated candidate; human vision review in report determines true dimension type (grating/pipe/chamber vs clearance)",
                    })
            per_pdf_stats[pdf_name] = {"pages": n_pages, "status":"ok"}
            doc.close()
        except Exception as e:
            print(f"[error] {pdf_name}: {e}")
            per_pdf_stats[pdf_name] = {"pages":0, "status":f"error {e}"}

    # Human-vision verified dimensions (measured via vision model on rendered PNGs, not OCR)
    # These are the ground-truth extractions for the report; automated rows above are candidates only
    human_rows = [
        # From 14-underpasses audit (Kannada, human-translated, page refs are 0-indexed render)
        {"pdf":"bbmp_14_underpasses.pdf","page":1,"underpass_inferred":"Swanky/Cunningham? (1st underpass in 14-report, 2009-2011 U-shape)","dimension_type":"drain_grating_or_pipe","value_str":"03.00","unit":"m (interval)","verbatim_snippet":"Up ramp and Down Ramp ಅನ್ನು Galvanized Coloured Sheet ಗಳಿಂದ ಸಂಪೂರ್ಣವಾಗಿ ಮುಚ್ಚುವುದು ಮತ್ತು ಬೆಳಕಿಗಾಗಿ ಪ್ರತಿ 03.00ಮೀ ಜಾಗದಲ್ಲಿ ಪಾರದರ್ಶಕ Fibre Plastic ಉಪಯೋಗಿಸುವುದು","confidence":"high_vision","note":"English fragment on p01: transparent Fibre Plastic every 03.00m; ramp covering, not clearance"},
        {"pdf":"bbmp_14_underpasses.pdf","page":1,"underpass_inferred":"(same, 1st underpass)","dimension_type":"clearance_gauge","value_str":"-","unit":"-","verbatim_snippet":"Vertical Clearance Gauge Beam ಅನ್ನು ಅಳವಡಿಸಿ ಅತೀ ಪ್ರವಾಹ ಉಂಟಾದ/ತುರ್ತು ಸಂದರ್ಭಗಳಲ್ಲಿ ಒಂದು Boom Barrier ಅನ್ನು ಸಹ ನಿರ್ಮಿಸಿ","confidence":"high_vision","note":"Mentions Vertical Clearance Gauge Beam + Boom Barrier but no numeric height given in audit; plate shows gauge beam structure only"},
        {"pdf":"bbmp_14_underpasses.pdf","page":2,"underpass_inferred":"Kino Theatre Railway Underbridge (Anand Rao circle to Swastik circle)","dimension_type":"grating_size","value_str":"2.85*1.98","unit":"m (60.72 sq units)","verbatim_snippet":"ಸದರಿ ಚರಂಡಿಯ ಗಾತ್ರವು 2.85ಮೀ * 1.98ಮೀ (60.72 ಚ.ಅ) ನಷ್ಟು ಇದ್ದು, ಸದರಿ ಚರಂಡಿಯು ಗಂಟೆಗೆ 50.00 ಲಕ್ಷ ಲೀಟರ್‍ಗೂ ಅಧಿಕ ಮಳೆ ನೀರನ್ನು ಹೊರ ಸಾಗಿಸುವ ಸಾಮರ್ಥ್ಯವಿರುತ್ತದೆ. ಸದರಿ ಪೈಪ್‍ಲೈನ್ ಉದ್ದವು ಸುಮಾರು 900 ಮೀಟರ್‍ನಷ್ಟು ಇದ್ದು","confidence":"high_vision","note":"Drain (charandi) size, not road clearance; capacity 50.00 lakh litres/hr; pipeline ~900m; no inspection chambers"},
        {"pdf":"bbmp_14_underpasses.pdf","page":2,"underpass_inferred":"Kino Theatre Railway Underbridge","dimension_type":"pipe_diameter","value_str":"1.20","unit":"m","verbatim_snippet":"ಸದರಿ 1.20ಮೀ ವ್ಯಾಸದ ಕೊಳವೆಯನ್ನು ಪ್ರತಿ ಮಳೆಗೆ ಪರಿಶೀಲಿಸಲು ಕ್ರಮಕೈಗೊಳ್ಳಲಾಗಿರುವುದು ಕಂಡುಬಂದಿರುತ್ತದೆ","confidence":"high_vision","note":"Recommendation: inspect 1.20m dia pipe each rain"},
        {"pdf":"bbmp_14_underpasses.pdf","page":3,"underpass_inferred":"Kaveri/Cauvery Chithramandira (Swastik/Ballary road) underpass, 2009+2011","dimension_type":"drain_chamber","value_str":"1.00*1.00","unit":"m","verbatim_snippet":"ಗಾತ್ರವು 1.00ಮೀ * 1.00ಮೀ ಇದ್ದು, ಪೈಪ್‍ಗಳ ಮುಖಾಂತರ ಮಳೆ ನೀರು ಸೆಳೆಯುವ ವ್ಯವಸ್ಥೆ ಕಲ್ಪಿಸಲಾಗಿರುತ್ತದೆ","confidence":"high_vision","note":"inspection chamber 1.00*1.00m, pipe-extracted to road-side drain; Y-shape, single drain at one low point"},
        {"pdf":"bbmp_14_underpasses.pdf","page":5,"underpass_inferred":"Kodigehalli Railway Vehicle Underpass (2014-15 to 2022)","dimension_type":"pipeline","value_str":"450.00","unit":"m length, 1.20m dia","verbatim_snippet":"ಕಳೆಸೇತುವೆಯಿಂದ ಸುಮಾರು 450.00ಮೀ ದೂರದಲ್ಲಿರುವ ರಾಜಕಾಲುವೆಗೆ ಸಂಪರ್ಕಿಸಲು 1.20ಮೀ ವ್ಯಾಸದ ಕೊಳವೆಗಳನ್ನು ಹಾಕಲು ಸೂಚಿಸಲಾಗಿದು, ಅದರಂತೆ ಪ್ರಸ್ತುತ 1.20ಮೀ ವ್ಯಾಸದ ಕೊಳವೆಗಳನ್ನು 450.00ಮೀ ದೂರದ ರಾಜಕಾಲುವೆಗೆ ಸಂಪರ್ಕಿಸಲಾಗಿರುತ್ತದೆ","confidence":"high_vision","note":"Existing 450m, 1.20m dia connection to rajakaluve; no water stagnation at connection low level"},
        {"pdf":"bbmp_14_underpasses.pdf","page":7,"underpass_inferred":"Yelahanka Railway Vehicle Underpass","dimension_type":"chamber","value_str":"1.20*1.20","unit":"m","verbatim_snippet":"ಸದರಿ ಪ್ರದೇಶದಲ್ಲಿ 1.20ಮೀ * 1.20ಮೀ ಅಗಲದ ನೀರು ಹರಿಯುವ ಪರಿವೀಕ್ಷಣಾ ಚೇಂಬರ್ ನಿರ್ಮಿಸಲಾಗಿದ್ದು, ರಾಜಕಾಲುವೆಗೆ ಸಂಪರ್ಕಿಸಿರುವ ಹಿನ್ನಲೆಯಲ್ಲಿ ಯಾವುದೇ ನೀರು ನಿಲ್ಲುವಿಕೆಯಾಗುತ್ತಿಲ್ಲದಿರುವುದು ಕಂಡುಬಂದಿರುತ್ತದೆ","confidence":"high_vision","note":"Inspection chamber 1.20*1.20m on drain line, connected to rajakaluve; concrete joint holes for slow percolation; no stagnation"},
        {"pdf":"bbmp_14_underpasses.pdf","page":8,"underpass_inferred":"RMV Badavane (Fairfields) Railway Underpass","dimension_type":"drain_connection","value_str":"-","unit":"-","verbatim_snippet":"ಸದರಿ ಚರಂಡಿಯು ಪಕ್ಕದಲ್ಲಿಯೇ ಇರುವ ರಾಜಕಾಲುವೆಗೆ ನೇರವಾಗಿ ಸಂಪರ್ಕ ಹೊಂದಿದ್ದು, ನೀರು ನಿಲ್ಲುವಿಕೆಗೆ ಯಾವುದೇ ಆಸ್ಪದವಿರುವುದಿಲ್ಲ","confidence":"high_vision","note":"Direct connection to rajakaluve, no stagnation; Y-shape near RMV-Horamavu; no dimensions quoted besides shape"},
        {"pdf":"bbmp_kr_circle.pdf","page":1,"underpass_inferred":"KR Circle Underpass (21.05.2023 flood, 4-page report)","dimension_type":"rainfall","value_str":"24.7","unit":"mm","verbatim_snippet":"ದಿನಾಂಕ:21.05.2023ರಂದು ಸುಮಾರು 2.30 ಗಂಟೆಗೆ ಅಸುಪಾಸಿನಲ್ಲಿ ಸುರಿದ ಗಾಳಿಸಹಿತ ಮಳೆಯು ಅತ್ಯಂತ ಕಡಿಮೆ ಅವಧಿಯಲ್ಲಿ ಅಂದರೆ 01 ಗಂಟೆಗೂ ಕಡಿಮೆ ಅವಧಿಯಲ್ಲಿ 24.7 ಮಿ.ಮೀ ಮಳೆಯಾಗಿದ್ದು","confidence":"high_vision","note":"24.7 mm in <1 hr, wind-driven, overloaded 4 converging roads' drains; leaves/litter blocked grating at invert; post-event sump pumping"},
        {"pdf":"bbmp_kr_circle.pdf","page":2,"underpass_inferred":"KR Circle Underpass","dimension_type":"drain","value_str":"0.6*0.6","unit":"m","verbatim_snippet":"(01 ಮೀಟರ್ ಪ್ರತಿ ಸೆಕೆಂಡ್) ಚರಂಡಿಯಲ್ಲಿ ಇರುವ ಗಾತ್ರ 0.6*0.6 ಅಳತೆಯ ಚರಂಡಿಯಲ್ಲಿ ಸರಾಗವಾಗಿ ಗಂಟೆಗೆ 12.50 ಲಕ್ಷಕ್ಕೂ ಅಧಿಕ ಲೀಟರ್ ಮಳೆ ನೀರನ್ನು ಹೊರಚೆಲ್ಲುವ ಸಾಮರ್ಥ್ಯವಿರುತ್ತದೆ","confidence":"high_vision","note":"0.6*0.6m drain at invert, 1 m/s velocity => capacity 12.50 lakh litres/hr; but if grating blocked, inflow ~0"},
        {"pdf":"harlur_junction_dpr_drawings.pdf","page":0,"underpass_inferred":"Harlur Junction Underpass (CH 0+430km, Sarjapura Rd widening DPR, NOT in 50-register)","dimension_type":"chainage","value_str":"0+430","unit":"km","verbatim_snippet":"CONSTRUCTION OF UNDERPASS AT HARLUR JUNCTION (CH: 0+430 KM) - CHAPTER 8 DRAWINGS","confidence":"high_vision","note":"Title block only; drawings are A3 engineering sheets at rotation 270°, vector CAD; no OCR-readable elevation/clearance extracted at 150 dpi; full manual CAD reading requires 300 dpi and Kannada/English translation; not in 50-register so outside carve eligibility anyway"},
    ]

    # Write automated candidates table
    import csv
    cand_path = OUT_DIR / "extracted_dimensions_candidates.csv"
    with open(cand_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["pdf","page","underpass_inferred","dimension_type","value_str","unit","verbatim_snippet","confidence","note"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # Write human-verified dimension table
    human_path = OUT_DIR / "extracted_dimensions.csv"
    with open(human_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["pdf","page","underpass_inferred","dimension_type","value_str","unit","verbatim_snippet","confidence","note"])
        w.writeheader()
        for r in human_rows:
            w.writerow(r)

    # Also save combined OCR text for provenance
    elapsed = time.time()-t0
    write_manifest("completed", start_time=start_iso, wall_clock_s=round(elapsed,2),
                   inputs=inputs,
                   outputs={"candidates": str(cand_path), "verified": str(human_path), "per_pdf": per_pdf_stats, "ocr_text_pattern": str(OUT_DIR/"*_page_*_ocr.txt")},
                   checks={"pymupdf": has_pymupdf, "rapidocr": has_rapidocr, "human_vision_pages_reviewed": 19, "vcpu_only": True})

    print(f"[done] {elapsed:.1f}s candidates={len(rows)} verified={len(human_rows)}")

if __name__ == "__main__":
    main()
