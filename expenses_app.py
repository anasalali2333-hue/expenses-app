# -*- coding: utf-8 -*-
import streamlit as st
from groq import Groq
import base64
import json
import os
import sqlite3
import pandas as pd
from datetime import datetime
from io import BytesIO
import plotly.express as px
from fpdf import FPDF
import fitz  # PyMuPDF
import arabic_reshaper
from bidi.algorithm import get_display

# ============ الإعدادات ============
GROQ_API_KEY = "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"
MODEL_NAME = "qwen/qwen3.8-27b"
DB_FILE = "expenses.db"
IMAGES_DIR = "receipts_images"

st.set_page_config(page_title="مصاريفي", page_icon="🧾", layout="centered")
os.makedirs(IMAGES_DIR, exist_ok=True)

# ============ إنشاء قاعدة البيانات تلقائياً للسحابة ============
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def setup_database():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS invoices (transaction_id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_date TEXT, description TEXT, invoice_number TEXT, total_amount REAL, category TEXT, image_path TEXT, created_at TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS invoice_items (item_id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id INTEGER, item_name TEXT, quantity REAL, unit_price REAL, line_total REAL, FOREIGN KEY (transaction_id) REFERENCES invoices (transaction_id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS budgets (category TEXT PRIMARY KEY, amount REAL)''')
    conn.commit()
    conn.close()

setup_database()

# ============ دوال قاعدة البيانات ============
def save_invoice(date, desc, inv_num, total, category, image_path, items_df):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO invoices (invoice_date, description, invoice_number, total_amount, category, image_path, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (date, desc, inv_num, total, category, image_path, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    transaction_id = cursor.lastrowid
    for _, row in items_df.iterrows():
        cursor.execute('''
            INSERT INTO invoice_items (transaction_id, item_name, quantity, unit_price, line_total)
            VALUES (?, ?, ?, ?, ?)
        ''', (transaction_id, row["المنتج"], row["الكمية"], row["سعر الوحدة"], row["الإجمالي"]))
    conn.commit()
    conn.close()
    return transaction_id

def delete_invoice(transaction_id, image_path):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM invoice_items WHERE transaction_id = ?", (transaction_id,))
    cursor.execute("DELETE FROM invoices WHERE transaction_id = ?", (transaction_id,))
    conn.commit()
    conn.close()
    if os.path.exists(image_path):
        try: os.remove(image_path)
        except: pass

# ============ معالجة الملفات ============
def convert_pdf_to_image(pdf_bytes):
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("jpeg")
        doc.close()
        return img_bytes
    except: return None

# ============ الذكاء الاصطناعي (الحل النصي للصور المائلة) ============
def extract_invoice_data(image_bytes):
    try:
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        client = Groq(api_key=GROQ_API_KEY)
        
        prompt = """أنت نظام استخراج بيانات فائق الدقة (OCR).
تخيل أنك تقرأ الفاتورة كتيار نصي مستمر من الأعلى إلى الأسفل، سطراً بسطر.
مهمتك: استخراج أسماء المنتجات والأسعار الخاصة بها.
⚠️ القاعدة الذهبية: عندما تقرأ اسم منتج (مثل Apfel 5kg Karton)، استمر في القراءة على نفس السطر أو السطر الذي يليه مباشرة حتى تجد أول مبلغ رقمي (مثل 4.99). اربط هذا المبلغ بالمنتج الذي قرأته للتو.
لا تضيع المبلغ للمنتج التالي. كل منتج يتبعه سعره الخاص.

أعد البيانات بصيغة JSON فقط:
{
  "store_name": "اسم المتجر", "date": "YYYY-MM-DD", "invoice_number": "رقم الفاتورة",
  "total": 14.60, "category": "طعام أو وقود أو منزل أو أخرى",
  "items": [{"name": "اسم المنتج", "qty": 1, "price": 0.0, "total": 0.0}]
}"""

        response = client.chat.completions.create(
            model=MODEL_NAME, 
            max_tokens=1000,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]}], 
            temperature=0.1,
        )
        
        content = response.choices[0].message.content
        if "```json" in content: content = content.split("```json")[1].split("```")[0]
        elif "```" in content: content = content.split("```")[1].split("```")[0]
        
        data = json.loads(content.strip())
        return data, None
    except Exception as e: return None, str(e)

# ============ المساعد الذكي ============
def ask_database(question):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT transaction_id, invoice_date, description, category, total_amount FROM invoices")
        invoices = [dict(row) for row in cursor.fetchall()]
        if not invoices:
            conn.close()
            return "لا توجد فواتير مسجلة بعد."
        
        cursor.execute("SELECT transaction_id, item_name, quantity, line_total FROM invoice_items")
        items_data = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        context = "بيانات الفواتير:\n"
        for inv in invoices:
            context += f"- المتجر: {inv['description']} | التاريخ: {inv['invoice_date']} | الإجمالي: {inv['total_amount']} €\n"
            invoice_items = [i for i in items_data if i['transaction_id'] == inv['transaction_id']]
            if invoice_items:
                context += "  (البنود: "
                for item in invoice_items:
                    context += f"{item['item_name']} (المجموع: {item['line_total']})، "
                context += ")\n"
        
        client = Groq(api_key=GROQ_API_KEY)
        prompt = f"بناءً على البيانات التالية، أجب على سؤال المستخدم بالعربية بدقة:\n{context}\n\nسؤال: {question}"
        response = client.chat.completions.create(model=MODEL_NAME, max_tokens=500, messages=[{"role": "user", "content": prompt}], temperature=0.2)
        return response.choices[0].message.content
    except Exception as e: return f"خطأ: {str(e)}"

# ============ إنشاء PDF (مع إصلاح الحروف) ============
def create_pdf(df):
    pdf = FPDF()
    pdf.add_page()
    def fix_ar(text):
        if not text: return ""
        return get_display(arabic_reshaper.reshape(str(text)))
    font_path = "Amiri-Regular.ttf"
    if os.path.exists(font_path):
        pdf.add_font('Amiri', '', font_path, uni=True)
        pdf.set_font('Amiri', '', 12)
    else: pdf.set_font('Arial', 'B', 12)
    if os.path.exists(font_path): pdf.set_font('Amiri', '', 16)
    else: pdf.set_font('Arial', 'B', 16)
    pdf.cell(0, 10, fix_ar('كشف المصاريف'), ln=True, align='C')
    pdf.ln(5)
    if os.path.exists(font_path): pdf.set_font('Amiri', '', 10)
    else: pdf.set_font('Arial', '', 10)
    pdf.set_fill_color(200, 220, 255)
    col_widths = [20, 30, 80, 30, 30]
    headers = ["رقم العملية", "التاريخ", "الوصف", "الفئة", "المبلغ"]
    for i, header in enumerate(headers):
        pdf.cell(col_widths[i], 10, fix_ar(header), border=1, align='C', fill=True)
    pdf.ln()
    if os.path.exists(font_path): pdf.set_font('Amiri', '', 9)
    else: pdf.set_font('Arial', '', 9)
    for index, row in df.iterrows():
        pdf.cell(col_widths[0], 8, str(row['transaction_id']), border=1, align='C')
        pdf.cell(col_widths[1], 8, str(row['invoice_date']), border=1, align='C')
        desc = str(row['description'])[:30] + "..." if len(str(row['description'])) > 30 else str(row['description'])
        pdf.cell(col_widths[2], 8, fix_ar(desc), border=1, align='R')
        pdf.cell(col_widths[3], 8, fix_ar(row['category']), border=1, align='C')
        pdf.cell(col_widths[4], 8, f"{row['total_amount']:.2f} €", border=1, align='C')
        pdf.ln()
    pdf.ln(5)
    if os.path.exists(font_path): pdf.set_font('Amiri', '', 12)
    else: pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, fix_ar(f"إجمالي المصاريف: {df['total_amount'].sum():.2f} €"), ln=True, align='R')
    pdf.output("temp_report.pdf")
    return "temp_report.pdf"

# ============ الواجهة ============
st.title("🧾 تطبيق مصاريفي")
st.caption("صوّر فاتورتك، ودعنا ندير أموالك")

page = st.radio("اختر:", ["📸 إضافة فاتورة", "📊 كشف الإجمالي", "🎯 الميزانية", "📈 الإحصائيات", "🤖 مساعد المصاريف", "🖼️ أرشيف الصور"], horizontal=True, label_visibility="collapsed")

if page == "📸 إضافة فاتورة":
    st.subheader("صوّر الفاتورة أو ارفعها")
    cam = st.camera_input("التقط صورة")
    upl = st.file_uploader("أو ارفع صورة / PDF", type=["jpg", "jpeg", "png", "pdf"])
    
    img_bytes = None
    if cam: img_bytes = cam.getvalue()
    elif upl:
        if upl.name.lower().endswith(".pdf"):
            with st.spinner("جاري تحويل الـ PDF..."):
                img_bytes = convert_pdf_to_image(upl.read())
        else: img_bytes = upl.getvalue()
    
    if img_bytes:
        st.image(img_bytes, caption="الفاتورة", width=300)
        if st.button("🧠 اقرأ الفاتورة", use_container_width=True):
            with st.spinner("جاري القراءة..."):
                data, error = extract_invoice_data(img_bytes)
                if error: st.error(f"خطأ: {error}")
                elif data:
                    st.session_state.temp_data = data
                    st.session_state.temp_img = img_bytes
                    st.success("✅ تم القراءة. يرجى تأكيد الحفظ.")

    if "temp_data" in st.session_state:
        data = st.session_state.temp_data
        col1, col2 = st.columns(2)
        with col1:
            store_name = st.text_input("اسم المتجر:", value=data.get("store_name", ""))
            inv_date = st.text_input("التاريخ:", value=data.get("date", ""))
        with col2:
            total = st.number_input("الإجمالي:", value=float(data.get("total", 0.0)), step=0.01)
            category = st.selectbox("الفئة:", ["طعام", "وقود", "منزل", "صحة", "ترفيه", "أخرى"], index=0)
            
        st.write("**البنود:**")
        df_items = pd.DataFrame([{"المنتج": i.get("name", ""), "الكمية": i.get("qty", 1), "سعر الوحدة": i.get("price", 0.0), "الإجمالي": i.get("total", 0.0)} for i in data.get("items", [])])
        edited_df = st.data_editor(df_items, num_rows="dynamic", use_container_width=True)
        
        if st.button("💾 حفظ الفاتورة", use_container_width=True):
            img_filename = f"receipt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            img_path = os.path.join(IMAGES_DIR, img_filename)
            with open(img_path, "wb") as f: f.write(st.session_state.temp_img)
            trans_id = save_invoice(inv_date, store_name, data.get("invoice_number", ""), total, category, img_path, edited_df)
            st.success(f"✅ تم الحفظ بنجاح! (رقم العملية: {trans_id})")
            st.balloons()
            del st.session_state.temp_data
            del st.session_state.temp_img
            st.rerun()

elif page == "📊 كشف الإجمالي":
    st.subheader("كشف الفواتير")
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT transaction_id, invoice_date, description, category, total_amount, image_path FROM invoices ORDER BY transaction_id DESC", conn)
    conn.close()
    if df.empty: st.info("لا توجد فواتير بعد.")
    else:
        df_display = df.drop(columns=["image_path"]).copy()
        df_display.index += 1
        st.dataframe(df_display.rename(columns={"transaction_id": "رقم العملية", "invoice_date": "التاريخ", "description": "الوصف", "category": "الفئة", "total_amount": "المبلغ"}))
        st.metric("إجمالي المصاريف", f"{df['total_amount'].sum():.2f} €")
        
        col1, col2 = st.columns(2)
        with col1:
            output_excel = BytesIO()
            with pd.ExcelWriter(output_excel, engine='openpyxl') as writer:
                df_display.to_excel(writer, index=False)
            st.download_button("⬇️ تحميل Excel", output_excel.getvalue(), "expenses.xlsx", use_container_width=True)
        with col2:
            if st.button("⬇️ إنشاء PDF", use_container_width=True):
                pdf_file = create_pdf(df_display)
                with open(pdf_file, "rb") as f:
                    st.download_button("📄 تحميل PDF", f, "expenses.pdf", mime="application/pdf", use_container_width=True)
        
        st.divider()
        st.subheader("🗑️ حذف فاتورة")
        invoice_to_delete = st.selectbox("اختر رقم العملية للحذف:", df["transaction_id"].tolist(), format_func=lambda x: f"عملية رقم {x} - {df[df['transaction_id']==x]['description'].values[0]}")
        if st.button("❌ حذف الفاتورة المحددة نهائياً", type="primary"):
            img_path = df[df["transaction_id"] == invoice_to_delete]["image_path"].values[0]
            delete_invoice(invoice_to_delete, img_path)
            st.success("✅ تم الحذف!")
            st.rerun()

elif page == "🎯 الميزانية":
    st.subheader("تحديد الميزانية الشهرية")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT category, amount FROM budgets")
    budgets_data = {row["category"]: row["amount"] for row in cursor.fetchall()}
    cursor.execute("SELECT category, SUM(total_amount) as spent FROM invoices GROUP BY category")
    spent_data = {row["category"]: row["spent"] for row in cursor.fetchall()}
    conn.close()
    categories = ["طعام", "وقود", "منزل", "صحة", "ترفيه", "أخرى"]
    
    with st.form("budget_form"):
        st.write("أدخل المبلغ المخصص لكل فئة:")
        new_budgets = {}
        for cat in categories:
            default_val = float(budgets_data.get(cat, 0.0))
            new_budgets[cat] = st.number_input(f"ميزانية {cat}:", min_value=0.0, value=default_val, step=10.0)
        
        if st.form_submit_button("💾 حفظ الميزانية"):
            conn = get_db_connection()
            cursor = conn.cursor()
            for cat, amt in new_budgets.items():
                cursor.execute('INSERT OR REPLACE INTO budgets (category, amount) VALUES (?, ?)', (cat, amt))
            conn.commit()
            conn.close()
            st.success("✅ تم حفظ الميزانية بنجاح!")
            st.rerun()

    st.divider()
    st.subheader("حالة الميزانية الحالية")
    for cat in categories:
        budget = budgets_data.get(cat, 0.0)
        spent = spent_data.get(cat, 0.0)
        if budget > 0:
            percentage = (spent / budget) * 100
            st.write(f"**{cat}:** صرفت {spent:.2f} € من أصل {budget:.2f} €")
            if percentage >= 100:
                st.error(f"⚠️ تجاوزت الميزانية! ({percentage:.1f}%)")
                st.progress(1.0)
            elif percentage >= 80:
                st.warning(f"تحذير: أنت على وشك تجاوز الميزانية ({percentage:.1f}%)")
                st.progress(percentage / 100)
            else:
                st.progress(percentage / 100)
        else:
            st.info(f"{cat}: لم يتم تحديد ميزانية بعد. (صرفت {spent:.2f} €)")

elif page == "📈 الإحصائيات":
    st.subheader("أين تذهب أموالك؟")
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT category, total_amount FROM invoices", conn)
    conn.close()
    if df.empty: st.info("لا توجد بيانات لعرض الإحصائيات.")
    else:
        df_grouped = df.groupby("category")["total_amount"].sum().reset_index()
        fig = px.pie(df_grouped, values='total_amount', names='category', title='توزيع المصاريف حسب الفئة', hole=0.4)
        st.plotly_chart(fig, use_container_width=True)
        fig_bar = px.bar(df_grouped, x='category', y='total_amount', title='إجمالي المصاريف لكل فئة', color='category')
        st.plotly_chart(fig_bar, use_container_width=True)

elif page == "🤖 مساعد المصاريف":
    st.subheader("اسأل عن مصاريفك")
    question = st.text_input("مثال: كم سعر التفاح؟")
    if st.button("اسأل", use_container_width=True):
        if question:
            with st.spinner("جاري التحليل..."):
                answer = ask_database(question)
                st.success(answer)

elif page == "🖼️ أرشيف الصور":
    st.subheader("أرشيف الفواتير")
    conn = get_db_connection()
    df_images = pd.read_sql_query("SELECT transaction_id, invoice_date, description, image_path FROM invoices ORDER BY transaction_id DESC", conn)
    conn.close()
    
    if df_images.empty: st.info("لا توجد صور محفوظة بعد.")
    else:
        for _, row in df_images.iterrows():
            if os.path.exists(row["image_path"]):
                st.write(f"**فاتورة رقم {row['transaction_id']} - {row['description']} ({row['invoice_date']})**")
                st.image(row["image_path"], width=200)
                st.divider()
