# coding_app_revised.py — Strict QC (Gemini 2.5 + Fallback)
# ------------------------------------------------------------
# Asisten Koding Otomatis — patuh Codebook v1.1 dengan QC ketat
# - Codebook otomatis dari codebook.txt (atau secrets/env CODEBOOK_PATH)
# - API key otomatis dari secrets/env (GEMINI_API_KEY/GOOGLE_API_KEY)
# - Model 2.5 (Pro/Flash/Flash-Lite) + fallback cerdas & deteksi kuota 429 limit:0
# - JSON-mode (response_schema) -> keluaran JSON valid
# - Screening & Scope fields; QC otomatis (auto-NA bila evidence kosong)
# - Multi-row (split-case), verifikasi per baris, ekspor CSV/JSON
# ------------------------------------------------------------

import os
import json
from typing import List, Optional, Dict, Any, Tuple
import pandas as pd
import streamlit as st
import google.generativeai as genai

st.set_page_config(page_title="Asisten Koding Otomatis — Codebook v1.1 (Strict QC)", layout="wide")

# =========================
# Load Codebook
# =========================
@st.cache_data(show_spinner=False)
def load_codebook_text() -> str:
    cb_path = ""
    if "CODEBOOK_PATH" in st.secrets:
        cb_path = str(st.secrets["CODEBOOK_PATH"]).strip()
    elif os.environ.get("CODEBOOK_PATH"):
        cb_path = os.environ.get("CODEBOOK_PATH").strip()

    candidates: List[str] = []
    if cb_path:
        candidates.append(cb_path)
    candidates.append("codebook_llm.txt")

    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except FileNotFoundError:
            continue
        except Exception as e:
            st.warning(f"Gagal membaca Codebook dari '{path}': {e}")
    return ""

CODEBOOK_TEXT = load_codebook_text()
if not CODEBOOK_TEXT:
    st.error("File **codebook.txt** tidak ditemukan/kosong. Letakkan Codebook v1.1 lengkap di direktori app atau set CODEBOOK_PATH.")
    st.stop()

# =========================
# API key (no UI)
# =========================
def get_api_key() -> str:
    key = st.secrets.get("GEMINI_API_KEY") or st.secrets.get("GOOGLE_API_KEY") \
          or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        st.error("Google API Key tidak ditemukan di secrets/env (GEMINI_API_KEY/GOOGLE_API_KEY).")
        st.stop()
    return key

if st.secrets.get("GEMINI_API_KEY") or st.secrets.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
    st.caption("✅ Google API Key terdeteksi dari secrets/env (tidak ditampilkan).")
else:
    st.error("Google API Key tidak terdeteksi.")
    st.stop()

# =========================
# Schema & Columns
# =========================
SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {

                    # --- Group 1: Screening & Scope ---
                    "rrn": {"type": "string"},
                    "inclusion_I1": {"type": "string", "enum": ["Yes","No","NA"]},
                    "inclusion_I2": {"type": "string", "enum": ["Yes","No","NA"]},
                    "inclusion_I3": {"type": "string", "enum": ["Yes","No","NA"]},
                    "exclusion_E1": {"type": "string", "enum": ["Yes","No"]},
                    "exclusion_E2": {"type": "string", "enum": ["Yes","No"]},
                    "scope_decision": {"type": "string", "enum": ["Include","Exclude"]},
                    "scope_justification": {"type": "string"},
                    "literature_type": {"type": "string"},
                    "geographic_focus": {"type": "string"},
                    "unit_of_analysis": {"type": "string", "enum": ["Village/community","Cluster of villages","Municipality/Region","Household/enterprise","Programme/policy","Other"]},

                    # --- Group 3: Definitions & Typologies ---
                    "explicit_definition": {"type": "string", "enum": ["Yes","Partial","No"]},
                    "verbatim_definition": {"type": "string"},
                    "typology_proposed": {"type": "string", "enum": ["Yes","Partial","No"]},
                    "typology_details": {"type": "string"},

                    # --- Group 4: Axes + anchors ---
                    "axis_A": {"type": "string", "enum": ["A1 State-led","A2 Co-managed","A3 Community-led","NA"]},
                    "axis_B": {"type": "string", "enum": ["B1 Heritage-led","B2 Nature-led","B3 Mixed-portfolio","B4 Commodified/amenities-led","NA"]},
                    "axis_C": {"type": "string", "enum": ["C1 Claimed/aspirational","C2 Process-based/criteria","C3 Measured/verified","NA"]},
                    "axis_A_anchor": {"type": "string"},
                    "axis_B_anchor": {"type": "string"},
                    "axis_C_anchor": {"type": "string"},

                    # --- Purpose & Findings ---
                    "purpose_tokens": {"type": "string"},  # pipe-separated
                    "key_findings": {"type": "string"},

                    # --- Group 5: Outcomes + evidence ---
                    "participation_level": {"type": "string", "enum": ["1","2","3","NA"]},
                    "participation_evidence": {"type": "string"},
                    "equity_level": {"type": "string", "enum": ["1","2","3","NA"]},
                    "equity_evidence": {"type": "string"},
                    "env_level": {"type": "string", "enum": ["1","2","3","NA"]},
                    "env_evidence": {"type": "string"},

                    # --- Optional tags ---
                    "equity_tags": {"type": "string"},
                    "engagement_tags": {"type": "string"},

                    # --- QC ---
                    "evidence_quality": {"type": "string", "enum": ["Low","Moderate","High"]},
                    "inferred": {"type": "string", "enum": ["Yes","No"]},
                    "notes": {"type": "string"},
                    "split_case": {"type": "string", "enum": ["Yes","No"]}
                },
                "required": [
                    "inclusion_I1","inclusion_I2","inclusion_I3",
                    "exclusion_E1","exclusion_E2","scope_decision",
                    "unit_of_analysis",

                    "explicit_definition","typology_proposed",
                    "axis_A","axis_B","axis_C",

                    "participation_level","equity_level","env_level",
                    "equity_evidence","env_evidence",

                    "evidence_quality","inferred","split_case"
                ]
            }
        }
    },
    "required": ["rows"]
}

DEFAULT_GENERATION_CONFIG: Dict[str, Any] = {
    "temperature": 0.2,
    "top_p": 0.9,
    "response_mime_type": "application/json",
    "response_schema": SCHEMA
}

COLUMNS: List[str] = [
    # Screening & Scope
    "rrn","inclusion_I1","inclusion_I2","inclusion_I3","exclusion_E1","exclusion_E2",
    "scope_decision","scope_justification","literature_type","geographic_focus","unit_of_analysis",
    # Core extraction
    "explicit_definition","verbatim_definition","typology_proposed","typology_details",
    "axis_A","axis_B","axis_C","axis_A_anchor","axis_B_anchor","axis_C_anchor",
    "purpose_tokens","key_findings",
    # Outcomes
    "participation_level","participation_evidence",
    "equity_level","equity_evidence",
    "env_level","env_evidence",
    # Tags & QC
    "equity_tags","engagement_tags",
    "evidence_quality","inferred","notes","split_case",
    # Original text
    "original_text"
]

ENUMS: Dict[str, List[str]] = {
    "inclusion_I1": ["Yes","No","NA"],
    "inclusion_I2": ["Yes","No","NA"],
    "inclusion_I3": ["Yes","No","NA"],
    "exclusion_E1": ["Yes","No"],
    "exclusion_E2": ["Yes","No"],
    "scope_decision": ["Include","Exclude"],
    "unit_of_analysis": ["Village/community","Cluster of villages","Municipality/Region","Household/enterprise","Programme/policy","Other"],

    "explicit_definition": ["Yes","Partial","No"],
    "typology_proposed": ["Yes","Partial","No"],
    "axis_A": ["A1 State-led","A2 Co-managed","A3 Community-led","NA"],
    "axis_B": ["B1 Heritage-led","B2 Nature-led","B3 Mixed-portfolio","B4 Commodified/amenities-led","NA"],
    "axis_C": ["C1 Claimed/aspirational","C2 Process-based/criteria","C3 Measured/verified","NA"],
    "participation_level": ["1","2","3","NA"],
    "equity_level": ["1","2","3","NA"],
    "env_level": ["1","2","3","NA"],
    "evidence_quality": ["Low","Moderate","High"],
    "inferred": ["Yes","No"],
    "split_case": ["Yes","No"]
}

# =========================
# Utils & QC rules
# =========================
def normalise_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for col in COLUMNS:
        val = row.get(col, "")
        if col in ENUMS and val not in ENUMS[col]:
            # Fallback aman
            if col in ["axis_A","axis_B","axis_C","participation_level","equity_level","env_level"]:
                val = "NA"
            elif col in ["explicit_definition","typology_proposed"]:
                val = "No"
            elif col in ["inclusion_I1","inclusion_I2","inclusion_I3"]:
                val = "NA"
            elif col in ["exclusion_E1","exclusion_E2"]:
                val = "No"
            elif col == "evidence_quality":
                val = "Moderate"
            elif col in ["inferred","split_case"]:
                val = "No"
            elif col == "scope_decision":
                val = "Include"
            elif col == "unit_of_analysis":
                val = "Village/community"
        out[col] = val
    return out

def apply_qc_rules(row: Dict[str, Any]) -> Dict[str, Any]:
    notes: List[str] = []

    # 1) Outcome evidence required: downgrade to NA if evidence empty/NA
    for var in ["participation","equity","env"]:
        lvl = (row.get(f"{var}_level") or "NA").strip()
        ev = (row.get(f"{var}_evidence") or "").strip()
        if lvl in ["1","2","3"] and (ev == "" or ev.upper() == "NA"):
            row[f"{var}_level"] = "NA"
            notes.append(f"Auto-NA {var} (no evidence).")

    # 2) Typology: set Partial if criteria unclear
    if row.get("typology_proposed") == "Yes":
        td = (row.get("typology_details") or "").lower()
        if ("not explicit" in td) or ("tidak eksplisit" in td) or (len(td) < 40):
            row["typology_proposed"] = "Partial"
            notes.append("Typology set to Partial (criteria unclear).")

    # 3) Scope: if Exclude -> axes & outcomes NA
    if row.get("scope_decision") == "Exclude":
        for k in ["axis_A","axis_B","axis_C","participation_level","equity_level","env_level"]:
            row[k] = "NA"
        row["evidence_quality"] = row.get("evidence_quality") or "Low"
        notes.append("Excluded (scope): axes & outcomes set to NA.")

    # 4) Axis anchors sanity: if axis != NA but no anchor, flag
    for ax in ["A","B","C"]:
        if row.get(f"axis_{ax}") and row.get(f"axis_{ax}") != "NA":
            if not (row.get(f"axis_{ax}_anchor") or "").strip():
                notes.append(f"Axis {ax} lacks anchor.")

    # Append notes
    if notes:
        row["notes"] = (row.get("notes","") + (" " if row.get("notes") else "") + "; ".join(notes)).strip()

    return normalise_row(row)

def is_free_tier_quota_zero_error(err: Exception) -> bool:
    msg = str(err).lower()
    return ("429" in msg) and ("quota" in msg) and ("limit: 0" in msg)

def quota_help_box(model_tried: str):
    with st.expander("❓ Bantuan: Mengatasi error kuota (429 limit: 0)", expanded=True):
        st.markdown(
            f"- Project Anda tidak punya kuota **free-tier** untuk **{model_tried}**.\n"
            f"- **Coba model lain**: *gemini-2.5-flash* atau *gemini-2.5-flash-lite*.\n"
            f"- Atau **aktifkan billing/kuota** di Google AI Studio/Vertex AI untuk model yang ingin dipakai.\n"
            f"- Pastikan API key berasal dari project yang benar (punya kuota)."
        )

def configure_genai() -> bool:
    try:
        genai.configure(api_key=get_api_key())
        return True
    except Exception as e:
        st.error(f"Gagal mengonfigurasi API Google: {e}")
        return False

def _fallback_order_for(model_name: str) -> List[str]:
    # Fallback sesuai dua profil yang disepakati
    if model_name == "gemini-2.5-pro":
        return ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
    if model_name == "gemini-2.5-flash":
        return ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"]
    # default (flash-lite atau lainnya)
    return ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"]

def generate_coding_draft(
    article_text: str,
    codebook_text: str,
    model_name: str
) -> Optional[Tuple[List[Dict[str, Any]], str]]:
    """
    Panggil Gemini dengan JSON-mode sesuai SCHEMA.
    Fallback urutan:
      - Pro -> Flash -> Flash-Lite
      - Flash -> Flash-Lite -> Pro
      - Flash-Lite -> Flash -> Pro
    Jika 429 & limit=0: langsung coba model berikutnya (tanpa retry).
    Return: (rows, used_model) atau None.
    """
    if not configure_genai():
        return None

    prompt = f"""
SYSTEM:
You are an exacting academic coding assistant. Follow the CODEBOOK and output ONLY JSON per the provided schema.

TASK:
Read [ARTICLE] and fill the JSON fields strictly. Use ONLY evidence from the text.
Rules:
- SCREENING & SCOPE: decide I1–I3/E1–E2 and scope_decision using the scope safeguard. If the unit of analysis is not village/community and no dedicated village module exists, set scope_decision='Exclude' and justify.
- Verbatim fields must copy exactly and include page/section anchors if present (e.g., “...” p. 12; Fig. 2).
- Use pipe '|' for multi-value tokens (e.g., purpose_tokens, tags).
- If evidence is insufficient after two careful passes, choose 'NA' and explain briefly in 'notes'.
- If you infer from strong contextual cues, set inferred='Yes' and justify in the relevant *_evidence.

CODEBOOK FULL:
---
{codebook_text}
---

OUTPUT RULES:
- Return an object with key "rows": a list of row objects (ALWAYS a list).
- Each row = one document (or split-case if distinct per-village evidence; then set split_case='Yes').

ARTICLE:
---
{article_text}
---
    """.strip()

    last_err: Optional[Exception] = None
    for model_try in _fallback_order_for(model_name):
        try:
            model = genai.GenerativeModel(model_name=model_try, generation_config=DEFAULT_GENERATION_CONFIG)
            resp = model.generate_content(prompt)
            raw_json = resp.text  # JSON string (response_mime_type="application/json")
            data = json.loads(raw_json)

            if not isinstance(data, dict) or "rows" not in data or not isinstance(data["rows"], list):
                st.error(f"Struktur JSON tidak sesuai saat memakai **{model_try}**.")
                st.text_area("Output mentah dari AI:", raw_json, height=200)
                return None

            return data["rows"], model_try

        except Exception as e:
            last_err = e
            if is_free_tier_quota_zero_error(e):
                st.warning(f"429 limit:0 pada **{model_try}** → mencoba fallback berikutnya…")
                continue
            else:
                st.error(f"Kesalahan saat memakai **{model_try}**: {e}")
                return None

    st.error("Tidak bisa menghasilkan output karena kuota/akses semua model yang dicoba gagal.")
    if last_err:
        quota_help_box(_fallback_order_for(model_name)[0])
        st.text_area("Detail error terakhir:", str(last_err), height=160)
    return None

# =========================
# Session State
# =========================
if "coding_queue" not in st.session_state:
    st.session_state.coding_queue = []
if "coding_result" not in st.session_state:
    st.session_state.coding_result = None
if "coded_data" not in st.session_state:
    st.session_state.coded_data = pd.DataFrame(columns=COLUMNS)

# =========================
# UI
# =========================
st.title("🤖 Asisten Koding Otomatis (AKO) — Codebook v1.1 (Strict QC)")
st.info("Codebook dimuat dari **codebook.txt**. API Key via secrets/env. QC ketat aktif (auto-NA bila evidence kosong).", icon="📖")

left, right = st.columns([2,1])

with right:
    st.subheader("⚙️ Konfigurasi")
    MODEL_OPTIONS = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"]
    model_choice = st.radio(
        "Pilih Model Gemini:",
        options=MODEL_OPTIONS,
        index=0,  # default: 2.5 Flash (efisien & kuota biasanya lebih longgar)
        help="2.5 Pro = akurasi/penalaran tinggi; 2.5 Flash = efisien; Flash-Lite = paling hemat biaya."
    )

with left:
    st.subheader("📄 Teks Artikel")
    article_input = st.text_area(
        "Tempel teks lengkap artikel (satu dokumen per run):",
        height=500,
        placeholder="Tempel full text, sertakan penanda halaman/fig jika ada."
    )
    if st.button("Mulai Pengodean Otomatis", type="primary", use_container_width=True):
        if not article_input:
            st.warning("Masukkan teks artikel terlebih dahulu.")
        else:
            with st.spinner("AI sedang membaca & mengodekan..."):
                result = generate_coding_draft(article_text=article_input, codebook_text=CODEBOOK_TEXT, model_name=model_choice)
                if result:
                    rows, used_model = result
                    q: List[Dict[str, Any]] = []
                    for r in rows:
                        r["original_text"] = article_input
                        r = normalise_row(r)
                        r = apply_qc_rules(r)  # QC ketat
                        q.append(r)
                    st.session_state.coding_queue = q
                    st.session_state.coding_result = st.session_state.coding_queue.pop(0)
                    st.success(f"AI menghasilkan {len(rows)} baris. Verifikasi baris pertama di bawah.")
                    st.caption(f"Model aktif: {used_model}")
                else:
                    st.error("Tidak ada baris yang dihasilkan / JSON tidak valid.")

st.markdown("---")

# =========================
# Verifikasi & Edit
# =========================
if st.session_state.coding_result:
    st.header("✅ Verifikasi & Edit Hasil (Per Baris)")
    r = st.session_state.coding_result

    with st.form("verification_form"):
        st.subheader("Screening & Scope")
        inc_opts = ENUMS["inclusion_I1"]
        scope_opts = ENUMS["scope_decision"]
        ua_opts = ENUMS["unit_of_analysis"]

        rrn = st.text_input("RRN (opsional)", value=r.get("rrn",""))
        inclusion_I1 = st.selectbox("I1 Concept focus (village/CBT unit)", inc_opts, index=inc_opts.index(r.get("inclusion_I1","NA")))
        inclusion_I2 = st.selectbox("I2 Scholarly/credible", inc_opts, index=inc_opts.index(r.get("inclusion_I2","NA")))
        inclusion_I3 = st.selectbox("I3 Conceptual utility", inc_opts, index=inc_opts.index(r.get("inclusion_I3","NA")))
        exclusion_E1 = st.selectbox("E1 Scale too broad/narrow", ENUMS["exclusion_E1"], index=ENUMS["exclusion_E1"].index(r.get("exclusion_E1","No")))
        exclusion_E2 = st.selectbox("E2 Non-scholarly/insubstantial", ENUMS["exclusion_E2"], index=ENUMS["exclusion_E2"].index(r.get("exclusion_E2","No")))
        unit_of_analysis = st.selectbox("Unit of analysis", ua_opts, index=ua_opts.index(r.get("unit_of_analysis","Village/community")))
        scope_decision = st.selectbox("Scope decision", scope_opts, index=scope_opts.index(r.get("scope_decision","Include")))
        scope_justification = st.text_area("Scope justification (ringkas + anchor)", value=r.get("scope_justification",""), height=80)

        st.subheader("Definitions & Typology")
        def_opts = ENUMS["explicit_definition"]; typ_opts = ENUMS["typology_proposed"]
        explicit_definition = st.selectbox("Explicit definition", def_opts, index=def_opts.index(r.get("explicit_definition","No")))
        verbatim_definition = st.text_area("Verbatim definition (quote + page/section)", value=r.get("verbatim_definition",""), height=90)
        typology_proposed = st.selectbox("Typology proposed", typ_opts, index=typ_opts.index(r.get("typology_proposed","No")))
        typology_details = st.text_area("Typology details (classes + rules)", value=r.get("typology_details",""), height=90)

        st.subheader("Axes + Anchors")
        axis_A = st.selectbox("Axis A", ENUMS["axis_A"], index=ENUMS["axis_A"].index(r.get("axis_A","NA")))
        axis_A_anchor = st.text_input("Axis A anchor", value=r.get("axis_A_anchor",""))
        axis_B = st.selectbox("Axis B", ENUMS["axis_B"], index=ENUMS["axis_B"].index(r.get("axis_B","NA")))
        axis_B_anchor = st.text_input("Axis B anchor", value=r.get("axis_B_anchor",""))
        axis_C = st.selectbox("Axis C", ENUMS["axis_C"], index=ENUMS["axis_C"].index(r.get("axis_C","NA")))
        axis_C_anchor = st.text_input("Axis C anchor", value=r.get("axis_C_anchor",""))

        st.subheader("Purpose & Findings")
        purpose_tokens = st.text_input("Purpose tokens (DEV|LIV|SUS ...)", value=r.get("purpose_tokens",""))
        key_findings = st.text_area("Key arguments/findings (2–4 lines)", value=r.get("key_findings",""), height=90)

        st.subheader("Outcomes + Evidence")
        lvl_opts = ENUMS["participation_level"]; eq_q = ENUMS["evidence_quality"]; yn_opts = ENUMS["inferred"]
        participation_level = st.selectbox("Participation level", lvl_opts, index=lvl_opts.index(r.get("participation_level","NA")))
        participation_evidence = st.text_area("Participation evidence (verbatim + anchor)", value=r.get("participation_evidence",""), height=90)
        equity_level = st.selectbox("Equity level", lvl_opts, index=lvl_opts.index(r.get("equity_level","NA")))
        equity_evidence = st.text_area("Equity evidence (verbatim + anchor) — WAJIB bila level≠NA", value=r.get("equity_evidence",""), height=90)
        env_level = st.selectbox("Environmental level", lvl_opts, index=lvl_opts.index(r.get("env_level","NA")))
        env_evidence = st.text_area("Environmental evidence (verbatim + anchor) — WAJIB bila level≠NA", value=r.get("env_evidence",""), height=90)

        st.subheader("Tags & QC")
        equity_tags = st.text_input("Equity tags", value=r.get("equity_tags",""))
        engagement_tags = st.text_input("Engagement tags", value=r.get("engagement_tags",""))
        evidence_quality = st.selectbox("Evidence quality", eq_q, index=eq_q.index(r.get("evidence_quality","Moderate")))
        inferred = st.selectbox("Inferred?", yn_opts, index=yn_opts.index(r.get("inferred","No")))
        notes = st.text_area("Notes (≤2 lines)", value=r.get("notes",""), height=70)
        split_case = st.selectbox("Split-case row?", ENUMS["split_case"], index=ENUMS["split_case"].index(r.get("split_case","No")))

        submitted = st.form_submit_button("Setuju & Simpan ke Sesi", use_container_width=True)
        if submitted:
            row: Dict[str, Any] = {
                # Screening & Scope
                "rrn": rrn,
                "inclusion_I1": inclusion_I1, "inclusion_I2": inclusion_I2, "inclusion_I3": inclusion_I3,
                "exclusion_E1": exclusion_E1, "exclusion_E2": exclusion_E2,
                "scope_decision": scope_decision, "scope_justification": scope_justification,
                "literature_type": r.get("literature_type",""), "geographic_focus": r.get("geographic_focus",""),
                "unit_of_analysis": unit_of_analysis,
                # Core extraction
                "explicit_definition": explicit_definition, "verbatim_definition": verbatim_definition,
                "typology_proposed": typology_proposed, "typology_details": typology_details,
                "axis_A": axis_A, "axis_B": axis_B, "axis_C": axis_C,
                "axis_A_anchor": axis_A_anchor, "axis_B_anchor": axis_B_anchor, "axis_C_anchor": axis_C_anchor,
                "purpose_tokens": purpose_tokens, "key_findings": key_findings,
                # Outcomes
                "participation_level": participation_level, "participation_evidence": participation_evidence,
                "equity_level": equity_level, "equity_evidence": equity_evidence,
                "env_level": env_level, "env_evidence": env_evidence,
                # Tags & QC
                "equity_tags": equity_tags, "engagement_tags": engagement_tags,
                "evidence_quality": evidence_quality, "inferred": inferred, "notes": notes, "split_case": split_case,
                # Original
                "original_text": r.get("original_text","")
            }
            row = normalise_row(row)
            row = apply_qc_rules(row)

            row_df = pd.DataFrame([{col: row.get(col, "") for col in COLUMNS}])
            st.session_state.coded_data = pd.concat([st.session_state.coded_data, row_df], ignore_index=True)

            st.success("Baris tersimpan ke sesi.")
            if st.session_state.coding_queue:
                st.session_state.coding_result = st.session_state.coding_queue.pop(0)
                st.rerun()
            else:
                st.session_state.coding_result = None
                st.balloons()
                st.rerun()

# =========================
# Data & Export
# =========================
if not st.session_state.coded_data.empty:
    st.markdown("---")
    st.header("🗂️ Data Terkode Sesi Ini")
    st.dataframe(st.session_state.coded_data, use_container_width=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        csv_bytes = st.session_state.coded_data.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Unduh CSV", data=csv_bytes, file_name="coded_data.csv", mime="text/csv", use_container_width=True)
    with c2:
        json_bytes = st.session_state.coded_data.to_json(orient="records", force_ascii=False).encode("utf-8")
        st.download_button("⬇️ Unduh JSON (records)", data=json_bytes, file_name="coded_data.json", mime="application/json", use_container_width=True)
    with c3:
        if st.button("🧹 Bersihkan Data Sesi", use_container_width=True):
            st.session_state.coded_data = pd.DataFrame(columns=COLUMNS)
            st.session_state.coding_queue = []
            st.session_state.coding_result = None
            st.success("Sesi dibersihkan.")
            st.rerun()

# =========================
# Footer
# =========================
st.markdown(
    "<hr/><small>Codebook dari <code>codebook.txt</code>. Lokasi alternatif: "
    "<code>st.secrets['CODEBOOK_PATH']</code> atau env <code>CODEBOOK_PATH</code>. "
    "API key dari secrets/env (<code>GEMINI_API_KEY</code>/<code>GOOGLE_API_KEY</code>). "
    "Strict QC aktif. Model 2.5 (Pro/Flash/Flash-Lite) dengan fallback cerdas.</small>",
    unsafe_allow_html=True
)
