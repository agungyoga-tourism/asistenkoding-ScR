# coding_app_revised.py
# ------------------------------------------------------------
# Streamlit app: Asisten Koding Otomatis — Codebook v1.1 compliant
# Perubahan kunci:
# - TIDAK ada input Google API Key di UI.
# - API key diambil otomatis dari st.secrets/env: GEMINI_API_KEY atau GOOGLE_API_KEY.
# - Codebook dimuat otomatis dari file codebook.txt (atau secrets/env CODEBOOK_PATH).
# - JSON-mode Gemini (response_schema) agar keluaran valid & patuh Codebook.
# - Dukungan multi-row (split-case), validasi enumerasi, ekspor CSV/JSON.
# ------------------------------------------------------------

import os
import json
from typing import List, Optional, Dict, Any
import pandas as pd
import streamlit as st
import google.generativeai as genai

# =========================
# Konfigurasi Halaman
# =========================
st.set_page_config(page_title="Asisten Koding Otomatis — Codebook v1.1", layout="wide")

# =========================
# Memuat Codebook dari file
# =========================
@st.cache_data(show_spinner=False)
def load_codebook_text() -> str:
    """
    Memuat Codebook dari:
    1) st.secrets['CODEBOOK_PATH'] atau env CODEBOOK_PATH (jika tersedia), jika tidak
    2) file 'codebook.txt' pada working directory.
    """
    cb_path = st.secrets.get("CODEBOOK_PATH", os.environ.get("CODEBOOK_PATH", "")).strip() if "CODEBOOK_PATH" in st.secrets or os.environ.get("CODEBOOK_PATH") else ""
    candidates: List[str] = []
    if cb_path:
        candidates.append(cb_path)
    candidates.append("codebook.txt")  # fallback default

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
    st.error(
        "File **codebook.txt** tidak ditemukan atau kosong. "
        "Simpan Codebook v1.1 lengkap sebagai `codebook.txt` di direktori aplikasi, "
        "atau setel `st.secrets['CODEBOOK_PATH']` / env `CODEBOOK_PATH`."
    )
    st.stop()

# =========================
# API Key (tanpa UI)
# =========================
def get_api_key() -> str:
    """
    Mengambil API key dari:
    - st.secrets['GEMINI_API_KEY'] atau st.secrets['GOOGLE_API_KEY']
    - env GEMINI_API_KEY atau GOOGLE_API_KEY
    """
    key = st.secrets.get("GEMINI_API_KEY", None)
    if not key:
        key = st.secrets.get("GOOGLE_API_KEY", None)
    if not key:
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        st.error(
            "Google API Key tidak ditemukan. Setel di `st.secrets['GEMINI_API_KEY']` "
            "atau `st.secrets['GOOGLE_API_KEY']` (alternatif: env `GEMINI_API_KEY`/`GOOGLE_API_KEY`)."
        )
        st.stop()
    return key

# Validasi ketersediaan key di awal agar jelas
_API_KEY_PRESENT = True if (st.secrets.get("GEMINI_API_KEY") or st.secrets.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")) else False
if _API_KEY_PRESENT:
    st.caption("✅ Google API Key terdeteksi dari secrets/env (tidak ditampilkan).")
else:
    st.error(
        "Google API Key tidak terdeteksi. Tambahkan ke `st.secrets` atau environment "
        "sebelum menjalankan pengodean."
    )
    st.stop()

# =========================
# Konstanta & Skema JSON
# =========================
SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # Group 3: Definitions & Typologies
                    "explicit_definition": {"type": "string", "enum": ["Yes", "Partial", "No"]},
                    "verbatim_definition": {"type": "string"},
                    "typology_proposed": {"type": "string", "enum": ["Yes", "Partial", "No"]},
                    "typology_details": {"type": "string"},

                    # Group 4: Axes (A/B/C) + anchors
                    "axis_A": {"type": "string", "enum": ["A1 State-led", "A2 Co-managed", "A3 Community-led", "NA"]},
                    "axis_B": {"type": "string", "enum": ["B1 Heritage-led", "B2 Nature-led", "B3 Mixed-portfolio", "B4 Commodified/amenities-led", "NA"]},
                    "axis_C": {"type": "string", "enum": ["C1 Claimed/aspirational", "C2 Process-based/criteria", "C3 Measured/verified", "NA"]},
                    "axis_A_anchor": {"type": "string"},
                    "axis_B_anchor": {"type": "string"},
                    "axis_C_anchor": {"type": "string"},

                    # Group 3/10 extras
                    "purpose_tokens": {"type": "string"},  # Multi dengan pipe: "DEV|LIV|SUS"
                    "key_findings": {"type": "string"},

                    # Group 5: Outcomes + evidence
                    "participation_level": {"type": "string", "enum": ["1", "2", "3", "NA"]},
                    "participation_evidence": {"type": "string"},
                    "equity_level": {"type": "string", "enum": ["1", "2", "3", "NA"]},
                    "equity_evidence": {"type": "string"},
                    "env_level": {"type": "string", "enum": ["1", "2", "3", "NA"]},
                    "env_evidence": {"type": "string"},

                    # Group 6: Optional tags
                    "equity_tags": {"type": "string"},      # contoh: "EQ-GEN|EQ-BEN"
                    "engagement_tags": {"type": "string"},  # contoh: "ENG-MED|ENG-MET"

                    # Group 7: QC
                    "evidence_quality": {"type": "string", "enum": ["Low", "Moderate", "High"]},
                    "inferred": {"type": "string", "enum": ["Yes", "No"]},
                    "notes": {"type": "string"},
                    "split_case": {"type": "string", "enum": ["Yes", "No"]}
                },
                "required": [
                    "explicit_definition", "typology_proposed",
                    "axis_A", "axis_B", "axis_C",
                    "participation_level", "equity_level", "env_level",
                    "equity_evidence", "env_evidence",
                    "evidence_quality", "inferred", "split_case"
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
    "explicit_definition", "verbatim_definition", "typology_proposed", "typology_details",
    "axis_A", "axis_B", "axis_C", "axis_A_anchor", "axis_B_anchor", "axis_C_anchor",
    "purpose_tokens", "key_findings",
    "participation_level", "participation_evidence",
    "equity_level", "equity_evidence",
    "env_level", "env_evidence",
    "equity_tags", "engagement_tags",
    "evidence_quality", "inferred", "notes", "split_case",
    "original_text"
]

ENUMS: Dict[str, List[str]] = {
    "explicit_definition": ["Yes", "Partial", "No"],
    "typology_proposed": ["Yes", "Partial", "No"],
    "axis_A": ["A1 State-led", "A2 Co-managed", "A3 Community-led", "NA"],
    "axis_B": ["B1 Heritage-led", "B2 Nature-led", "B3 Mixed-portfolio", "B4 Commodified/amenities-led", "NA"],
    "axis_C": ["C1 Claimed/aspirational", "C2 Process-based/criteria", "C3 Measured/verified", "NA"],
    "participation_level": ["1", "2", "3", "NA"],
    "equity_level": ["1", "2", "3", "NA"],
    "env_level": ["1", "2", "3", "NA"],
    "evidence_quality": ["Low", "Moderate", "High"],
    "inferred": ["Yes", "No"],
    "split_case": ["Yes", "No"]
}

# =========================
# Utilitas
# =========================
def normalise_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Pastikan setiap row punya semua kolom & nilai enum valid."""
    out: Dict[str, Any] = {}
    for col in COLUMNS:
        val = row.get(col, "")
        if col in ENUMS and val not in ENUMS[col]:
            if col in ["axis_A", "axis_B", "axis_C"]:
                val = "NA"
            elif col in ["participation_level", "equity_level", "env_level"]:
                val = "NA"
            elif col in ["explicit_definition", "typology_proposed"]:
                val = "No"
            elif col == "evidence_quality":
                val = "Moderate"
            elif col in ["inferred", "split_case"]:
                val = "No"
        out[col] = val
    return out


def configure_genai() -> bool:
    try:
        genai.configure(api_key=get_api_key())
        return True
    except Exception as e:
        st.error(f"Gagal mengonfigurasi API Google. Error: {e}")
        return False


def generate_coding_draft(
    article_text: str,
    codebook_text: str,
    model_name: str
) -> Optional[List[Dict[str, Any]]]:
    """
    Mengirim artikel + codebook ke Gemini dan memaksa keluaran JSON valid
    sesuai SCHEMA. Mengembalikan list[dict] rows (atau None jika gagal).
    """
    if not configure_genai():
        return None

    try:
        model = genai.GenerativeModel(
            model_name=model_name,
            generation_config=DEFAULT_GENERATION_CONFIG
        )
    except Exception as e:
        st.error(f"Gagal menginisialisasi model {model_name}. Error: {e}")
        return None

    prompt = f"""
SYSTEM:
You are an exacting academic coding assistant. Follow the CODEBOOK and output ONLY JSON per the provided schema.

TASK:
Read [ARTICLE] and fill the JSON fields strictly. Use ONLY evidence from the text.
Rules:
- Verbatim fields must copy exactly and include page/section anchors if present (e.g., “...” p. 12; Fig. 2).
- Use pipe '|' for multi-value tokens (e.g., purpose_tokens, tags).
- If evidence is insufficient after two careful passes, choose 'NA' and explain briefly in 'notes'.
- If you infer from strong contextual cues, set inferred='Yes' and justify in the relevant *_evidence.

CODEBOOK:
---
{codebook_text}
---

OUTPUT RULES:
- Return an object with key "rows": a list of row objects (ALWAYS a list, even if one row).
- Each row is one document. If the document contains distinct split-cases with separate definitions/typologies/outcomes, create multiple rows and set split_case='Yes' for those rows.

ARTICLE:
---
{article_text}
---
    """.strip()

    resp = None
    try:
        resp = model.generate_content(prompt)
        raw_json = resp.text  # JSON string (response_mime_type="application/json")
        data = json.loads(raw_json)

        if not isinstance(data, dict) or "rows" not in data or not isinstance(data["rows"], list):
            st.error("Struktur JSON tidak sesuai (wajib object dengan key 'rows' berupa list).")
            st.text_area("Output mentah dari AI:", raw_json, height=200)
            return None

        return data["rows"]

    except json.JSONDecodeError as e:
        st.error(f"Gagal parsing JSON dari respons AI. Error: {e}")
        if resp is not None:
            try:
                st.text_area("Output mentah dari AI (menyebabkan kesalahan parsing):", resp.text, height=200)
            except Exception:
                pass
        return None
    except Exception as e:
        st.error(f"Kesalahan saat berinteraksi dengan API: {e}")
        if resp is not None:
            try:
                st.text_area("Output mentah dari AI:", resp.text, height=200)
            except Exception:
                pass
        return None

# =========================
# Inisialisasi Session State
# =========================
if "coding_queue" not in st.session_state:
    st.session_state.coding_queue = []  # list of dict (rows)
if "coding_result" not in st.session_state:
    st.session_state.coding_result = None  # current row dict untuk verifikasi
if "coded_data" not in st.session_state:
    st.session_state.coded_data = pd.DataFrame(columns=COLUMNS)

# =========================
# Antarmuka Pengguna
# =========================
st.title("🤖 Asisten Koding Otomatis (AKO) — Codebook v1.1")
st.info("Codebook dimuat otomatis dari **codebook.txt**. API Key diambil dari secrets/env.", icon="📖")

input_col, config_col = st.columns([2, 1])

with config_col:
    st.subheader("⚙️ Konfigurasi")
    model_choice = st.radio(
        "Pilih Model Gemini:",
        options=["gemini-1.5-pro", "gemini-1.5-flash"],
        index=0,
        help="Pilih Pro untuk akurasi; Flash untuk kecepatan."
    )

with input_col:
    st.subheader("📄 Teks Artikel")
    article_input = st.text_area(
        "Tempel teks lengkap artikel (satu dokumen per run):",
        height=500,
        placeholder="Paste full text here, including page/section anchors if possible..."
    )

    if st.button("Mulai Pengodean Otomatis", type="primary", use_container_width=True):
        if not article_input:
            st.warning("Harap masukkan teks artikel terlebih dahulu.")
        else:
            with st.spinner("AI sedang membaca dan mengodekan artikel..."):
                rows = generate_coding_draft(
                    article_text=article_input,
                    codebook_text=CODEBOOK_TEXT,
                    model_name=model_choice
                )
                if rows:
                    q: List[Dict[str, Any]] = []
                    for r in rows:
                        r["original_text"] = article_input
                        q.append(normalise_row(r))
                    st.session_state.coding_queue = q
                    st.session_state.coding_result = st.session_state.coding_queue.pop(0)
                    st.success(f"AI menghasilkan {len(rows)} baris. Silakan verifikasi baris pertama di bawah.")
                else:
                    st.error("Tidak ada baris yang dihasilkan atau JSON tidak valid.")

st.markdown("---")

# =========================
# Verifikasi & Edit (1 row per kali)
# =========================
if st.session_state.coding_result:
    st.header("✅ Verifikasi & Edit Hasil (Per Baris)")
    result = st.session_state.coding_result

    with st.form("verification_form"):
        st.subheader("Ringkasan Kunci")

        def_opts = ENUMS["explicit_definition"]
        axis_a_opts = ENUMS["axis_A"]
        axis_b_opts = ENUMS["axis_B"]
        axis_c_opts = ENUMS["axis_C"]
        lvl_opts = ENUMS["participation_level"]
        eq_quality_opts = ENUMS["evidence_quality"]
        yn_opts = ENUMS["inferred"]
        split_opts = ENUMS["split_case"]

        # --- Definitions & Typology
        explicit_definition = st.selectbox("Explicit definition", def_opts, index=def_opts.index(result.get("explicit_definition", "No")))
        verbatim_definition = st.text_area("Verbatim definition (quote + page/section)", value=result.get("verbatim_definition", ""), height=100)
        typology_proposed = st.selectbox("Typology proposed", def_opts, index=def_opts.index(result.get("typology_proposed", "No")))
        typology_details = st.text_area("Typology details (classes + decision rules)", value=result.get("typology_details", ""), height=100)

        # --- Axes + anchors
        axis_A = st.selectbox("Axis A — Governance & ownership", axis_a_opts, index=axis_a_opts.index(result.get("axis_A", "NA")))
        axis_A_anchor = st.text_input("Axis A anchor (page/figure/table ref.)", value=result.get("axis_A_anchor", ""))
        axis_B = st.selectbox("Axis B — Market orientation & product mix", axis_b_opts, index=axis_b_opts.index(result.get("axis_B", "NA")))
        axis_B_anchor = st.text_input("Axis B anchor (page/figure/table ref.)", value=result.get("axis_B_anchor", ""))
        axis_C = st.selectbox("Axis C — Sustainability performance", axis_c_opts, index=axis_c_opts.index(result.get("axis_C", "NA")))
        axis_C_anchor = st.text_input("Axis C anchor (page/figure/table ref.)", value=result.get("axis_C_anchor", ""))

        # --- Purpose & Findings
        purpose_tokens = st.text_input("Purpose tokens (pipe-separated, e.g., DEV|LIV|SUS)", value=result.get("purpose_tokens", ""))
        key_findings = st.text_area("Key arguments/findings (2–4 lines)", value=result.get("key_findings", ""), height=100)

        # --- Outcomes + Evidence
        participation_level = st.selectbox("Participation level", lvl_opts, index=lvl_opts.index(result.get("participation_level", "NA")))
        participation_evidence = st.text_area("Participation evidence (verbatim + anchor)", value=result.get("participation_evidence", ""), height=120)
        equity_level = st.selectbox("Equity level", lvl_opts, index=lvl_opts.index(result.get("equity_level", "NA")))
        equity_evidence = st.text_area("Equity evidence (verbatim + anchor) — WAJIB", value=result.get("equity_evidence", ""), height=120)
        env_level = st.selectbox("Environmental level", lvl_opts, index=lvl_opts.index(result.get("env_level", "NA")))
        env_evidence = st.text_area("Environmental evidence (verbatim + anchor) — WAJIB", value=result.get("env_evidence", ""), height=120)

        # --- Tags & QC
        equity_tags = st.text_input("Equity tags (e.g., EQ-GEN|EQ-BEN)", value=result.get("equity_tags", ""))
        engagement_tags = st.text_input("Engagement tags (e.g., ENG-MED|ENG-MET)", value=result.get("engagement_tags", ""))
        evidence_quality = st.selectbox("Evidence quality", eq_quality_opts, index=eq_quality_opts.index(result.get("evidence_quality", "Moderate")))
        inferred = st.selectbox("Inferred?", yn_opts, index=yn_opts.index(result.get("inferred", "No")))
        notes = st.text_area("Notes (≤2 lines; explain NA or inference)", value=result.get("notes", ""), height=80)
        split_case = st.selectbox("Split-case row?", split_opts, index=split_opts.index(result.get("split_case", "No")))

        submitted = st.form_submit_button("Setuju & Simpan ke Sesi", use_container_width=True)
        if submitted:
            row: Dict[str, Any] = {
                "explicit_definition": explicit_definition,
                "verbatim_definition": verbatim_definition,
                "typology_proposed": typology_proposed,
                "typology_details": typology_details,
                "axis_A": axis_A,
                "axis_B": axis_B,
                "axis_C": axis_C,
                "axis_A_anchor": axis_A_anchor,
                "axis_B_anchor": axis_B_anchor,
                "axis_C_anchor": axis_C_anchor,
                "purpose_tokens": purpose_tokens,
                "key_findings": key_findings,
                "participation_level": participation_level,
                "participation_evidence": participation_evidence,
                "equity_level": equity_level,
                "equity_evidence": equity_evidence,
                "env_level": env_level,
                "env_evidence": env_evidence,
                "equity_tags": equity_tags,
                "engagement_tags": engagement_tags,
                "evidence_quality": evidence_quality,
                "inferred": inferred,
                "notes": notes,
                "split_case": split_case,
                "original_text": result.get("original_text", "")
            }

            row = normalise_row(row)
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
# Database Sesi & Ekspor
# =========================
if not st.session_state.coded_data.empty:
    st.markdown("---")
    st.header("🗂️ Data Terkode Sesi Ini")
    st.dataframe(st.session_state.coded_data, use_container_width=True)

    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        csv_bytes = st.session_state.coded_data.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Unduh CSV",
            data=csv_bytes,
            file_name="coded_data.csv",
            mime="text/csv",
            use_container_width=True
        )
    with col2:
        json_bytes = st.session_state.coded_data.to_json(orient="records", force_ascii=False).encode("utf-8")
        st.download_button(
            label="⬇️ Unduh JSON (records)",
            data=json_bytes,
            file_name="coded_data.json",
            mime="application/json",
            use_container_width=True
        )
    with col3:
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
    "<hr/><small>Codebook dimuat dari <code>codebook.txt</code>. "
    "Setel lokasi alternatif via <code>st.secrets['CODEBOOK_PATH']</code> atau env <code>CODEBOOK_PATH</code>. "
    "API key diambil dari <code>st.secrets</code> / env (<code>GEMINI_API_KEY</code>/<code>GOOGLE_API_KEY</code>).</small>",
    unsafe_allow_html=True
)
