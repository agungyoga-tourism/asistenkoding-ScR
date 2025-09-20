# coding_app_revised.py
# ------------------------------------------------------------
# Streamlit app: Asisten Koding Otomatis — Codebook v1.1 compliant
# Fitur utama:
# - Memaksa output JSON valid via Gemini JSON-mode (response_schema)
# - Skema mengikuti Minimal Reporting Checklist Codebook v1.1
# - Menangani multi-row (split-case) -> antrian verifikasi
# - Validasi enumerasi dan ekspor CSV/JSON
# ------------------------------------------------------------

import os
import json
import pandas as pd
import streamlit as st
import google.generativeai as genai

# =========================
# Konfigurasi Halaman
# =========================
st.set_page_config(page_title="Asisten Koding Otomatis — Codebook v1.1", layout="wide")

# =========================
# Konstanta & Skema JSON
# =========================

# Skema JSON ketat sesuai Minimal Reporting Checklist Codebook v1.1
SCHEMA = {
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

# Konfigurasi generasi default (dapat diubah di runtime dengan memilih model)
DEFAULT_GENERATION_CONFIG = {
    "temperature": 0.2,
    "top_p": 0.9,
    "response_mime_type": "application/json",
    "response_schema": SCHEMA
}

# Kolom DataFrame sesi (sinkron dengan skema + original_text)
COLUMNS = [
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

# Daftar enumerasi untuk validasi ringan di sisi app
ENUMS = {
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
def normalise_row(row: dict) -> dict:
    """
    Menjamin setiap row memiliki semua kolom (COLUMNS), nilai enum valid,
    dan string kosong untuk field opsional yang hilang.
    """
    out = {}
    for col in COLUMNS:
        val = row.get(col, "")
        # Validasi enum bila ada
        if col in ENUMS:
            if val not in ENUMS[col]:
                # fallback aman: NA untuk level/axes; "No" untuk inferred/split_case jika kosong
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


def configure_genai(api_key: str):
    try:
        genai.configure(api_key=api_key)
        return True, ""
    except Exception as e:
        return False, str(e)


def generate_coding_draft(article_text: str, codebook: str, api_key: str, model_name: str) -> list | None:
    """
    Mengirim artikel + codebook ke Gemini dan memaksa keluaran JSON valid
    sesuai SCHEMA. Mengembalikan list[dict] rows (atau None jika gagal).
    """
    ok, err = configure_genai(api_key)
    if not ok:
        st.error(f"Gagal mengonfigurasi API Google. Pastikan kunci API valid. Error: {err}")
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
{codebook}
---

OUTPUT RULES:
- Return an object with key "rows": a list of row objects (ALWAYS a list, even if one row).
- Each row is one document. If the document contains distinct split-cases with separate definitions/typologies/outcomes, create multiple rows and set split_case='Yes' for those rows.

ARTICLE:
---
{article_text}
---
    """.strip()

    try:
        resp = model.generate_content(prompt)
        raw_json = resp.text  # dijamin JSON string (response_mime_type="application/json")
        data = json.loads(raw_json)

        if not isinstance(data, dict) or "rows" not in data or not isinstance(data["rows"], list):
            st.error("Struktur JSON tidak sesuai (wajib object dengan key 'rows' berupa list).")
            st.text_area("Output mentah dari AI:", raw_json, height=200)
            return None

        return data["rows"]

    except json.JSONDecodeError as e:
        st.error(f"Gagal parsing JSON dari respons AI. Error: {e}")
        try:
            st.text_area("Output mentah dari AI (menyebabkan kesalahan parsing):", resp.text, height=200)
        except Exception:
            pass
        return None
    except Exception as e:
        st.error(f"Kesalahan saat berinteraksi dengan API: {e}")
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

st.write(
    "Aplikasi ini membantu ekstraksi dan klasifikasi bukti tentang **tourism villages** "
    "secara **ketat** sesuai *Codebook v1.1*. Gunakan tombol di bawah untuk menjalankan "
    "pengodean otomatis, lalu verifikasi setiap baris hasil."
)

input_col, config_col = st.columns([2, 1])

with config_col:
    st.subheader("⚙️ Konfigurasi")

    # API Key: prioritas dari input; jika kosong coba secrets/env (opsional)
    api_key_input = st.text_input(
        "Masukkan Google API Key:",
        type="password",
        placeholder="AIzaSy... (disarankan gunakan st.secrets)",
        help="Kunci ini tidak disimpan. Anda juga dapat menyetel st.secrets['GEMINI_API_KEY']."
    )
    if not api_key_input:
        api_key_input = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))

    model_choice = st.radio(
        "Pilih Model Gemini:",
        options=["gemini-1.5-pro", "gemini-1.5-flash"],
        index=0,
        help="Pilih Pro untuk akurasi; Flash untuk kecepatan."
    )

    st.subheader("📖 Codebook")
    default_codebook = (
        "Paste Codebook v1.1 — Tourism Village Scoping Review di sini.\n"
        "Gunakan versi lengkap agar pengodean 100% patuh.\n\n"
        "Contoh ringkas (placeholder):\n"
        "- Axis A/B/C (definisi & boundary)\n"
        "- Outcomes (participation/equity/env) + evidence hierarchy\n"
        "- Minimal reporting checklist\n"
        "- Validation lists\n"
    )
    codebook_input = st.text_area(
        "Codebook (tempel versi lengkap):",
        value=default_codebook,
        height=350
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
        elif not api_key_input:
            st.warning("Harap masukkan Google API Key (atau set di st.secrets/env).")
        elif not codebook_input or codebook_input.strip().lower().startswith("paste codebook"):
            st.warning("Harap tempel Codebook v1.1 lengkap untuk hasil yang patuh.")
        else:
            with st.spinner("AI sedang membaca dan mengodekan artikel..."):
                rows = generate_coding_draft(
                    article_text=article_input,
                    codebook=codebook_input,
                    api_key=api_key_input,
                    model_name=model_choice
                )
                if rows:
                    # Tambahkan original_text ke setiap row; normalisasi
                    q = []
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

        # Enumerations untuk widget
        def_opts = ENUMS["explicit_definition"]
        axis_a_opts = ENUMS["axis_A"]
        axis_b_opts = ENUMS["axis_B"]
        axis_c_opts = ENUMS["axis_C"]
        lvl_opts = ENUMS["participation_level"]
        eq_quality_opts = ENUMS["evidence_quality"]
        yn_opts = ENUMS["inferred"]
        split_opts = ENUMS["split_case"]

        # --- Definitions & Typology
        explicit_definition = st.selectbox(
            "Explicit definition",
            options=def_opts,
            index=def_opts.index(result.get("explicit_definition", "No"))
        )
        verbatim_definition = st.text_area(
            "Verbatim definition (quote + page/section)",
            value=result.get("verbatim_definition", ""),
            height=100
        )
        typology_proposed = st.selectbox(
            "Typology proposed",
            options=def_opts,
            index=def_opts.index(result.get("typology_proposed", "No"))
        )
        typology_details = st.text_area(
            "Typology details (classes + decision rules)",
            value=result.get("typology_details", ""),
            height=100
        )

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

        # Tombol submit
        submitted = st.form_submit_button("Setuju & Simpan ke Sesi", use_container_width=True)

        if submitted:
            row = {
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

            # Normalisasi & simpan
            row = normalise_row(row)
            row_df = pd.DataFrame([{col: row.get(col, "") for col in COLUMNS}])
            st.session_state.coded_data = pd.concat([st.session_state.coded_data, row_df], ignore_index=True)

            st.success("Baris tersimpan ke sesi.")
            # Ambil row berikutnya jika ada
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
    "<hr/>"
    "<small>Tips: Untuk kepatuhan penuh, tempel Codebook v1.1 lengkap. "
    "Tandai baris dengan <em>inferred='Yes'</em> atau <em>evidence_quality='Low'</em> untuk prioritas QC.</small>",
    unsafe_allow_html=True
)
