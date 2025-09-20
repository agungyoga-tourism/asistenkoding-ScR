import streamlit as st
import pandas as pd
import google.generativeai as genai
import json
import re

# --- Konfigurasi Halaman ---
st.set_page_config(page_title="Asisten Koding Otomatis", layout="wide")

# --- Inisialisasi Session State ---
# Diperlukan untuk menyimpan hasil pengodean dan database yang sedang berjalan
if 'coding_result' not in st.session_state:
    st.session_state.coding_result = None
if 'coded_data' not in st.session_state:
    st.session_state.coded_data = pd.DataFrame(columns=["axis_A", "equity_level", "equity_evidence", "original_text"])

# --- Fungsi Inti ---
def find_json_in_text(text):
    """Menemukan dan mengekstrak string JSON pertama yang valid dari blok teks."""
    # Pola regex untuk menemukan blok yang dimulai dengan { dan diakhiri dengan }
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return match.group(0)
    return None

def generate_coding_draft(article_text, codebook, api_key):
    """
    Mengirim artikel dan codebook ke AI untuk menghasilkan draf pengodean.
    """
    try:
        genai.configure(api_key=api_key)
    except Exception as e:
        st.error(f"Gagal mengonfigurasi API Google. Pastikan kunci API Anda valid. Error: {e}")
        return None

    model = genai.GenerativeModel('gemini-1.5-flash')
    
    # Prompt yang diperbaiki dengan instruksi yang lebih ketat
    prompt = f"""
    PERAN: Anda adalah Asisten Riset Akademik yang sangat teliti.
    TUJUAN: Baca [INPUT ARTIKEL ILMIAH] dan isi [FORMAT OUTPUT JSON] berdasarkan aturan ketat dari [CODEBOOK].

    [CODEBOOK]:
    ---
    {codebook}
    ---

    [FORMAT OUTPUT JSON (WAJIB DIIKUTI)]:
    {{
      "axis_A": "A1 State-led | A2 Co-managed | A3 Community-led | NA",
      "equity_level": "1 | 2 | 3 | NA",
      "equity_evidence": "Kutipan verbatim bukti pemerataan + halaman/bagian, atau NA"
    }}

    [INPUT ARTIKEL ILMIAH]:
    ---
    {article_text}
    ---

    PENTING: Respons Anda HARUS HANYA berisi objek JSON yang valid. Jangan sertakan teks penjelasan, markdown, atau kata pengantar apa pun.
    """
    
    try:
        response = model.generate_content(prompt)
        json_string = find_json_in_text(response.text)
        
        if json_string:
            return json.loads(json_string)
        else:
            st.error("AI tidak mengembalikan JSON yang valid. Tidak ada blok JSON yang ditemukan.")
            st.text_area("Output Mentah dari AI:", response.text)
            return None
            
    except json.JSONDecodeError as e:
        st.error(f"Gagal mem-parsing JSON dari respons AI. Error: {e}")
        st.text_area("Output Mentah dari AI (menyebabkan kesalahan parsing):", json_string)
        return None
    except Exception as e:
        st.error(f"Terjadi kesalahan tak terduga saat berinteraksi dengan API. Error: {e}")
        st.text_area("Output Mentah dari AI:", response.text)
        return None

# --- Antarmuka Pengguna (UI) ---

st.title("🤖 Asisten Koding Otomatis (AKO) v2")
st.write("Alat ini membantu mempercepat proses pengodean untuk tinjauan sistematis dengan menghasilkan draf pertama.")

# Kolom untuk Input
input_col, config_col = st.columns([2, 1])

with config_col:
    st.subheader("⚙️ Konfigurasi")
    api_key_input = st.text_input("Masukkan Kunci API Google Anda di sini:", type="password", help="Kunci API Anda tidak disimpan.")

    st.subheader("📖 Codebook")
    default_codebook = """
Codebook v1.1 — Aturan Ekstraksi
1. TUJUAN: Mengekstrak dan mengklasifikasi bukti tentang desa wisata secara sistematis.
2. AXIS A - GOVERNANCE:
   - A1 State-led: Pemerintah dominan.
   - A2 Co-managed: Ada pembagian kuasa formal.
   - A3 Community-led: Komunitas memegang hak putus utama.
3. EQUITY_LEVEL (1-3):
   - 3 (High): Ada bukti transparansi bagi hasil DAN representasi kelompok marjinal.
   - 2 (Medium): Ada program inklusi tapi kekuasaan terpusat.
   - 1 (Low): Ada bukti elite capture atau eksklusi.
4. ATURAN BUKTI: Setiap klasifikasi WAJIB didukung kutipan verbatim dari teks. Jika tidak ada, gunakan NA.
    """
    codebook_input = st.text_area("Edit atau tempelkan Codebook Anda:", value=default_codebook, height=350)

with input_col:
    st.subheader("📄 Teks Artikel")
    article_input = st.text_area("Salin dan tempelkan teks lengkap artikel di sini:", height=500, placeholder="Tempelkan teks di sini...")

    if st.button("Mulai Pengodean Otomatis", type="primary", use_container_width=True):
        if not article_input:
            st.warning("Harap masukkan teks artikel terlebih dahulu.")
        elif not api_key_input:
            st.warning("Harap masukkan Kunci API Google Anda.")
        elif not codebook_input:
            st.warning("Codebook tidak boleh kosong.")
        else:
            with st.spinner("AI sedang membaca dan mengodekan artikel..."):
                coding_result = generate_coding_draft(article_input, codebook_input, api_key_input)
                if coding_result:
                    # Sertakan teks asli untuk referensi
                    coding_result['original_text'] = article_input
                    st.session_state.coding_result = coding_result

st.markdown("---")

# --- Bagian Verifikasi & Pengeditan ---
if st.session_state.coding_result:
    st.header("✅ Verifikasi & Edit Hasil")
    
    result = st.session_state.coding_result
    
    with st.form("verification_form"):
        st.subheader("Draf yang Dihasilkan AI")
        
        # Opsi yang memungkinkan untuk dipilih
        axis_a_options = ["A1 State-led", "A2 Co-managed", "A3 Community-led", "NA"]
        equity_level_options = ["1", "2", "3", "NA"]

        # Menemukan indeks default dari hasil AI
        try:
            axis_a_index = axis_a_options.index(result.get('axis_A', 'NA'))
        except ValueError:
            axis_a_index = len(axis_a_options) - 1 # Default ke NA jika tidak valid

        try:
            equity_level_index = equity_level_options.index(str(result.get('equity_level', 'NA')))
        except ValueError:
            equity_level_index = len(equity_level_options) - 1 # Default ke NA jika tidak valid
        
        # Widget yang dapat diedit
        edited_axis_a = st.selectbox("Axis A (Governance):", options=axis_a_options, index=axis_a_index)
        edited_equity_level = st.selectbox("Equity Level:", options=equity_level_options, index=equity_level_index)
        edited_equity_evidence = st.text_area("Equity Evidence (Kutipan):", value=result.get('equity_evidence', 'NA'), height=150)
        
        submitted = st.form_submit_button("Setuju & Simpan ke Sesi", use_container_width=True)
        if submitted:
            new_row = pd.DataFrame([{
                "axis_A": edited_axis_a,
                "equity_level": edited_equity_level,
                "equity_evidence": edited_equity_evidence,
                "original_text": result.get('original_text', '')
            }])
            
            st.session_state.coded_data = pd.concat([st.session_state.coded_data, new_row], ignore_index=True)
            
            st.success("Data berhasil diverifikasi dan disimpan ke sesi saat ini!")
            st.balloons()
            
            # Hapus hasil saat ini untuk mempersiapkan yang berikutnya
            st.session_state.coding_result = None
            st.rerun()

# --- Tampilkan Database Sesi & Opsi Unduh ---
if not st.session_state.coded_data.empty:
    st.markdown("---")
    st.header("🗂️ Data Terkode Sesi Ini")
    st.dataframe(st.session_state.coded_data)
    
    # Konversi DataFrame ke CSV untuk diunduh
    csv = st.session_state.coded_data.to_csv(index=False).encode('utf-8')
    
    st.download_button(
       label="Unduh Data sebagai CSV",
       data=csv,
       file_name='coded_data.csv',
       mime='text/csv',
       use_container_width=True
    )
