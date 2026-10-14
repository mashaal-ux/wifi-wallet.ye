#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
خادم منصة محفظة كروت شبكات الواي فاي (Yemen Wi-Fi Wallet)
مرقى ومحدث بالكامل للعمل بنظام قواعد البيانات العلائقية الاحترافية (PostgreSQL / SQLAlchemy)
- دعم DATABASE_URL التلقائي لـ Railway و Render
- نظام قفل المعاملات (Database Locking / with_for_update) لمنع بيع الكروت مرتين
- عزل بيانات ملاك الشبكات برمجياً بنسبة 100%
- رفع الكروت دفعة واحدة عبر 3 طرق: يدوي (Textarea)، ملف إكسل (Excel .xlsx)، ملف بي دي إف (PDF .pdf)
- منع تكرار الكروت أو المستخدمين برسائل توضيحية دقيقة
- اعتماد سندات الإيداع المباشر من داخل بوت التلجرام نفسه عبر أزرار تفاعلية (Inline Keyboard)
"""

import os
import sys
import io
import re
import json
import time
import random
import uuid
import threading
import urllib.request
import urllib.parse
from datetime import datetime

from flask import Flask, request, jsonify, make_response, send_from_directory, redirect
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# ضبط ترميز الطرفية على ويندوز
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# استيراد النماذج وقاعدة البيانات
from models import db, User, Network, CardPackage, Card, Order, Deposit, BankAccount, SessionToken, OtpRecord

# إعدادات التطبيق
app = Flask(__name__, static_folder='public')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'yemen-wifi-wallet-secret-key-2026')
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024 # 32 MB max upload

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, 'public')
DATA_DIR = os.path.join(BASE_DIR, 'data')
UPLOADS_DIR = os.path.join(PUBLIC_DIR, 'uploads', 'receipts')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

# 1. إعداد الاتصال بقاعدة البيانات (PostgreSQL / SQLite fallback)
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    # بيئة التطوير المحلية (Local fallback to SQLite)
    db_path = os.path.join(DATA_DIR, 'yemen_wifi.db')
    DATABASE_URL = f"sqlite:///{db_path}"
    print(f"[DB] Using local SQLite database at: {db_path}")
else:
    # معالجة بادئة الرابط برمجياً تلقائياً إذا كانت تبدأ بـ postgres:// واستبدالها بـ postgresql://
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    print(f"[DB] Connected to PostgreSQL via DATABASE_URL")

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}

db.init_app(app)

# إعدادات بوت التلجرام
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8939237549:AAES0BcMqQy61KVF-qAEQsAHJjNh_J8X_W8')
CHAT_ID = os.environ.get('CHAT_ID', '5958490522')
PORT = int(os.environ.get('PORT', 3000))

# ==================== دوال الجلسات والمصادقة المساعدة ====================

def get_current_user():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return None
    session_token = SessionToken.query.filter_by(session_id=session_id).first()
    if not session_token:
        return None
    user = User.query.get(session_token.user_id)
    return user

def create_user_session(response, user_id):
    session_id = str(uuid.uuid4())
    # حفظ الجلسة في قاعدة البيانات لضمان البقاء عبر عمليات إعادة تشغيل السيرفر
    token = SessionToken(session_id=session_id, user_id=user_id, created_at=datetime.utcnow())
    db.session.add(token)
    db.session.commit()
    response.set_cookie(
        'session_id',
        session_id,
        max_age=30 * 24 * 3600, # 30 يوماً
        httponly=True,
        samesite='Lax',
        path='/'
    )
    return session_id

def remove_user_session(response):
    session_id = request.cookies.get('session_id')
    if session_id:
        SessionToken.query.filter_by(session_id=session_id).delete()
        db.session.commit()
    response.set_cookie('session_id', '', expires=0, path='/')

# ==================== خدمة التلجرام الموسعة مع اعتماد السندات ====================

def send_telegram_message(text, reply_markup=None):
    """إرسال رسالة نصية إلى بوت تلجرام"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode())
    except Exception as e:
        print("[Telegram Message Error]:", e)
        return None

def send_telegram_deposit_card(deposit, user):
    """إرسال إشعار إيداع جديد إلى التلجرام مزوداً بأزرار اعتماد ورفض تفاعلية مباشرة"""
    caption = (
        f"💳 <b>طلب إيداع رصيد جديد (#{deposit.id})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>العميل:</b> {deposit.customer_name}\n"
        f"📱 <b>الهاتف:</b> {deposit.phone or 'غير مسجل'}\n"
        f"🏦 <b>البنك / المحفظة:</b> {deposit.bank_name}\n"
        f"💵 <b>المبلغ المطلوب:</b> {deposit.amount:,.2f} ر.ي\n"
        f"🎁 <b>البونص التلقائي (+10%):</b> {deposit.amount * 0.10:,.2f} ر.ي\n"
        f"💳 <b>إجمالي الرصيد المستحق:</b> {deposit.amount * 1.10:,.2f} ر.ي\n"
        f"⏱️ <b>الوقت:</b> {datetime.now().strftime('%Y-%m-%d %I:%M %p')}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👇 <i>يمكنك اعتماد أو رفض الطلب بلمسة واحدة من الأزرار أدناه:</i>"
    )

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ اعتماد الإيداع (+10% بونص)", "callback_data": f"approve_dep_{deposit.id}"},
                {"text": "❌ رفض الطلب", "callback_data": f"reject_dep_{deposit.id}"}
            ]
        ]
    }

    # إذا توفرت صورة السند نرسلها كصورة، وإلا رسالة نصية
    if deposit.receipt_img and os.path.exists(os.path.join(PUBLIC_DIR, deposit.receipt_img.lstrip('/'))):
        try:
            photo_path = os.path.join(PUBLIC_DIR, deposit.receipt_img.lstrip('/'))
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            
            boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
            body = bytearray()

            def add_field(name, value):
                body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode('utf-8'))

            add_field('chat_id', CHAT_ID)
            add_field('caption', caption)
            add_field('parse_mode', 'HTML')
            add_field('reply_markup', json.dumps(keyboard))

            with open(photo_path, 'rb') as f:
                photo_bytes = f.read()

            filename = os.path.basename(photo_path)
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{filename}"\r\nContent-Type: image/jpeg\r\n\r\n'.encode('utf-8'))
            body.extend(photo_bytes)
            body.extend(f'\r\n--{boundary}--\r\n'.encode('utf-8'))

            req = urllib.request.Request(url, data=bytes(body), headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
            with urllib.request.urlopen(req, timeout=15) as res:
                res_data = json.loads(res.read().decode())
                if res_data.get('ok'):
                    msg_id = res_data['result']['message_id']
                    deposit.telegram_message_id = str(msg_id)
                    db.session.commit()
                    return
        except Exception as e:
            print("[Telegram Photo Error, falling back to text]:", e)

    # إرسال كنص في حال عدم وجود صورة أو تعذر رفعها
    res = send_telegram_message(caption, reply_markup=keyboard)
    if res and res.get('ok'):
        deposit.telegram_message_id = str(res['result']['message_id'])
        db.session.commit()

def answer_telegram_callback(callback_query_id, text, show_alert=False):
    """الرد على ضغطة الزر التفاعلي في تيليجرام"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
        payload = {"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert}
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:
        print("[Telegram Answer Callback Error]:", e)

def edit_telegram_message_text(chat_id, message_id, text):
    """تعديل نص الرسالة في التلجرام بعد اعتمادها أو رفضها"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageCaption"
        payload = {"chat_id": chat_id, "message_id": message_id, "caption": text, "parse_mode": "HTML"}
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        try:
            urllib.request.urlopen(req, timeout=8)
            return
        except Exception:
            pass # قد تكون الرسالة أصلية نصاً وليست صورة

        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:
        print("[Telegram Edit Message Error]:", e)

def process_telegram_callback_action(callback_data, callback_id, message_id, chat_id):
    """تنفيذ عملية اعتماد الإيداع أو رفضه برمجياً عند الضغط على زر التلجرام"""
    with app.app_context():
        try:
            if callback_data.startswith("approve_dep_"):
                dep_id = int(callback_data.replace("approve_dep_", ""))
                dep = Deposit.query.get(dep_id)
                if not dep:
                    answer_telegram_callback(callback_id, "❌ لم يتم العثور على طلب الإيداع", show_alert=True)
                    return
                if dep.status != "pending":
                    answer_telegram_callback(callback_id, f"⚠️ هذا الطلب تمت معالجته مسبقاً ({dep.status})", show_alert=True)
                    return

                user = User.query.get(dep.user_id)
                if not user:
                    answer_telegram_callback(callback_id, "❌ لم يتم العثور على حساب العميل", show_alert=True)
                    return

                bonus = round(float(dep.amount) * 0.10, 2)
                total_added = round(float(dep.amount) + bonus, 2)
                user.balance = round(float(user.balance) + total_added, 2)

                dep.status = 'approved'
                dep.approved_amount = float(dep.amount)
                dep.bonus_amount = bonus
                dep.reviewed_at = datetime.utcnow()
                db.session.commit()

                answer_telegram_callback(callback_id, f"✅ تم اعتماد الإيداع بنجاح!\nالمبلغ: {dep.amount:,.2f} + بونص: {bonus:,.2f} ر.ي", show_alert=True)

                update_text = (
                    f"✅ <b>تم اعتماد طلب الإيداع بنجاح عبر تيليجرام</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>العميل:</b> {dep.customer_name}\n"
                    f"💰 <b>المبلغ المعتمد:</b> {dep.amount:,.2f} ر.ي\n"
                    f"🎁 <b>البونص (10%):</b> {bonus:,.2f} ر.ي\n"
                    f"💳 <b>الرصيد الكلي المضاف:</b> {total_added:,.2f} ر.ي\n"
                    f"💵 <b>رصيد العميل الحالي بالمحفظة:</b> {user.balance:,.2f} ر.ي\n"
                    f"⏱️ <b>وقت الاعتماد:</b> {datetime.now().strftime('%Y-%m-%d %I:%M %p')}\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"✔️ <i>الحالة: معتمد ومضاف للمحفظة فوراً</i>"
                )
                edit_telegram_message_text(chat_id, message_id, update_text)

            elif callback_data.startswith("reject_dep_"):
                dep_id = int(callback_data.replace("reject_dep_", ""))
                dep = Deposit.query.get(dep_id)
                if not dep or dep.status != "pending":
                    answer_telegram_callback(callback_id, "⚠️ هذا الطلب تمت معالجته مسبقاً", show_alert=True)
                    return

                dep.status = 'rejected'
                dep.reviewed_at = datetime.utcnow()
                db.session.commit()

                answer_telegram_callback(callback_id, "❌ تم رفض طلب الإيداع", show_alert=True)
                update_text = (
                    f"❌ <b>تم رفض طلب الإيداع (#{dep.id})</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>العميل:</b> {dep.customer_name}\n"
                    f"💵 <b>المبلغ:</b> {dep.amount:,.2f} ر.ي\n"
                    f"⏱️ <b>وقت الرفض:</b> {datetime.now().strftime('%Y-%m-%d %I:%M %p')}\n"
                    f"❌ <i>تم الرفض بواسطة الإدارة عبر تيليجرام</i>"
                )
                edit_telegram_message_text(chat_id, message_id, update_text)
        except Exception as e:
            print("[Callback Action Error]:", e)

def start_telegram_listener():
    """خيط خلفي (Background Thread) لاستقبال أحداث تيليجرام وضغطات الأزرار والعمل بدون الحاجة لـ Webhook خارجي"""
    def worker():
        offset = 0
        while True:
            try:
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=10"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=15) as res:
                    data = json.loads(res.read().decode())
                    if data.get('ok') and data.get('result'):
                        for update in data['result']:
                            offset = max(offset, update['update_id'] + 1)
                            if 'callback_query' in update:
                                cq = update['callback_query']
                                cq_data = cq.get('data', '')
                                cq_id = cq.get('id')
                                msg = cq.get('message', {})
                                msg_id = msg.get('message_id')
                                c_id = msg.get('chat', {}).get('id')
                                process_telegram_callback_action(cq_data, cq_id, msg_id, c_id)
            except Exception:
                time.sleep(3)
            time.sleep(1)

    t = threading.Thread(target=worker, daemon=True)
    t.start()

# ==================== استخراج الكروت من Excel و PDF ====================

def parse_cards_from_excel(file_bytes):
    """استخراج الكروت من ملف Excel (.xlsx / .xls) عبر openpyxl"""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheet = wb.active
    
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []

    # تحديد أعمدة الـ PIN والـ Serial
    header_idx = -1
    pin_col = 0
    serial_col = None

    for r_idx, row in enumerate(rows[:5]):
        row_str = [str(c or '').strip().lower() for c in row]
        for c_idx, val in enumerate(row_str):
            if any(k in val for k in ['pin', 'كود', 'رقم الكرت', 'الكرت', 'كود الكرت', 'رمز الكرت', 'code']):
                pin_col = c_idx
                header_idx = r_idx
            elif any(k in val for k in ['serial', 'تسلسلي', 'سيريال', 'رقم التسلسل', 'sn']):
                serial_col = c_idx

    start_row = header_idx + 1 if header_idx != -1 else 0
    extracted = []

    for row in rows[start_row:]:
        if not row or len(row) <= pin_col:
            continue
        pin_val = str(row[pin_col] or '').strip()
        if not pin_val or pin_val.lower() in ['none', 'null', 'pin', 'code']:
            continue
        
        serial_val = None
        if serial_col is not None and len(row) > serial_col and row[serial_col]:
            serial_val = str(row[serial_col]).strip()
            
        extracted.append({"pin": pin_val, "serial": serial_val})

    return extracted

def parse_cards_from_pdf(file_bytes):
    """استخراج الكروت من ملف PDF عبر pypdf باستخدام التعرف الذكي على أنماط الأكواد"""
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    full_text = ""
    for page in reader.pages:
        txt = page.extract_text()
        if txt:
            full_text += "\n" + txt

    extracted = []
    lines = full_text.splitlines()

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        
        # البحث عن أرقام بطاقات الواي فاي (أكواد أرقام وحروف بين 6 إلى 16 خانة)
        # أمثلة: 12345678 أو PIN: 98765432
        matches = re.findall(r'(?:PIN[:\s]*|كود[:\s]*)?([A-Za-z0-9]{6,16})', line_clean)
        for m in matches:
            if m.lower() not in ['serial', 'number', 'expire', 'duration', 'voucher', 'wifi']:
                extracted.append({"pin": m, "serial": None})

    return extracted

# ==================== التهيئة وتوليد البيانات الأولية (Seeding) ====================

def seed_initial_database():
    """تهيئة قاعدة البيانات وإنشاء الحساب الافتراضي والشبكات إذا كانت فارغة"""
    with app.app_context():
        db.create_all()

        # فحص إذا كانت قاعدة البيانات تحتوي على مستخدمين
        if User.query.count() == 0:
            print("[DB] Empty database detected. Seeding initial platform data...")

            # محاولة الهجرة التلقائية من database.json القديم إن وجد
            json_file = os.path.join(DATA_DIR, 'database.json')
            if not os.path.exists(json_file):
                json_file = os.path.join(BASE_DIR, 'database.json')

            migrated = False
            if os.path.exists(json_file):
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        old_data = json.load(f)
                    
                    # استيراد المستخدمين مع استبعاد أي تكرارات في اسم المستخدم أو البريد
                    seen_usernames = set()
                    seen_emails = set()
                    for u in old_data.get('users', []):
                        uname = (u.get('username') or '').strip()
                        if not uname:
                            continue
                        uname_lower = uname.lower()
                        if uname_lower in seen_usernames:
                            continue
                        seen_usernames.add(uname_lower)

                        email = (u.get('email') or '').strip().lower() or None
                        if email and email in seen_emails:
                            email = None
                        if email:
                            seen_emails.add(email)

                        role = u.get('role', 'client')
                        if role == 'customer':
                            role = 'client'

                        user = User(
                            id=u.get('id'),
                            full_name=u.get('full_name', 'مستخدم'),
                            username=uname,
                            email=email,
                            phone=u.get('phone'),
                            role=role,
                            balance=float(u.get('balance', 0)),
                            network_id=u.get('network_id'),
                            is_verified=u.get('is_verified', True)
                        )
                        user.set_password(u.get('password', 'Moshal380.'))
                        db.session.add(user)
                    db.session.commit()

                    # استيراد الشبكات
                    for n in old_data.get('networks', []):
                        net = Network(
                            id=n.get('id'),
                            name=n.get('name'),
                            code=n.get('code'),
                            logo_icon=n.get('logo_icon', 'fa-wifi'),
                            description=n.get('description'),
                            login_url=n.get('login_url'),
                            is_active=n.get('is_active', True)
                        )
                        db.session.add(net)
                    db.session.commit()

                    # استيراد الباقات
                    for p in old_data.get('packages', []):
                        pkg = CardPackage(
                            id=p.get('id'),
                            network_id=p.get('network_id'),
                            name=p.get('name'),
                            price=float(p.get('price', 100)),
                            duration=p.get('duration'),
                            data_limit=p.get('data_limit'),
                            description=p.get('description'),
                            is_active=p.get('is_active', True)
                        )
                        db.session.add(pkg)
                    db.session.commit()

                    # استيراد الكروت مع استبعاد أي PIN مكرر
                    seen_pins = set()
                    for c in old_data.get('cards', []):
                        pin = str(c.get('pin_code', '')).strip()
                        if not pin or pin in seen_pins:
                            continue
                        seen_pins.add(pin)
                        card = Card(
                            id=c.get('id'),
                            network_id=c.get('network_id'),
                            package_id=c.get('package_id'),
                            pin_code=pin,
                            serial_number=c.get('serial_number'),
                            status=c.get('status', 'available'),
                            buyer_id=c.get('buyer_id')
                        )
                        db.session.add(card)
                    db.session.commit()

                    # استيراد الحسابات البنكية
                    for b in old_data.get('bank_accounts', []):
                        bank = BankAccount(
                            id=b.get('id'),
                            bank_name=b.get('bank_name'),
                            account_number=b.get('account_number'),
                            account_name=b.get('account_name'),
                            logo_icon=b.get('logo_icon', 'fa-landmark'),
                            instructions=b.get('instructions')
                        )
                        db.session.add(bank)
                    db.session.commit()

                    print("[DB Migration] Successfully migrated all data from database.json to SQL Database!")
                    migrated = True
                except Exception as e:
                    db.session.rollback()
                    print("[DB Migration Error]:", e)

            if not migrated:
                # إنشاء المدير العام الافتراضي
                admin = User(
                    full_name="مشعل المريسي",
                    username="مشعل المريسي",
                    phone="777000000",
                    email="admin@yemenwifi.com",
                    role="admin",
                    balance=0.0,
                    is_verified=True
                )
                admin.set_password("Moshal380.")
                db.session.add(admin)
                db.session.commit()

                # إنشاء الشبكات الثلاث الافتراضية
                n1 = Network(name="زين نت (Zain Net)", code="zain_net", logo_icon="fa-wifi", description="شبكة زين نت عالية السرعة - تغطية واسعة ومستقرة", login_url="http://zain.wifi/login", is_active=True, owner_id=admin.id)
                n2 = Network(name="مريس نت (Murais Net)", code="murais_net", logo_icon="fa-tower-broadcast", description="شبكة مريس نت للألياف والمايكروويف - سرعات فائقة", login_url="http://murais.net/login", is_active=True, owner_id=admin.id)
                n3 = Network(name="الفهد نت (Al-Fahd Net)", code="fahd_net", logo_icon="fa-bolt", description="شبكة الفهد نت الذكية - باقات يومية وشهرية توفيرية", login_url="http://fahd.wifi/login", is_active=True, owner_id=admin.id)
                db.session.add_all([n1, n2, n3])
                db.session.commit()

                # إنشاء الباقات
                pkgs = [
                    CardPackage(network_id=n1.id, name="باقة 3 ساعات سريعة", price=100, duration="3 ساعات", data_limit="500 ميجابايت", description="صالحة 3 ساعات"),
                    CardPackage(network_id=n1.id, name="باقة 24 ساعة يومية", price=300, duration="24 ساعة", data_limit="2 جيجابايت", description="يوم كامل"),
                    CardPackage(network_id=n1.id, name="باقة 3 أيام اقتصادية", price=700, duration="3 أيام", data_limit="5 جيجابايت", description="توفير 72 ساعة"),
                    CardPackage(network_id=n1.id, name="باقة أسبوعية غير محدودة", price=1500, duration="7 أيام", data_limit="15 جيجابايت", description="سرعة فائقة"),
                    CardPackage(network_id=n1.id, name="باقة شهرية ذهبية", price=5000, duration="30 يوماً", data_limit="60 جيجابايت", description="الباقة الأكثر طلباً"),

                    CardPackage(network_id=n2.id, name="مريس - كرت ساعة واحدة", price=100, duration="1 ساعة", data_limit="1 جيجابايت", description="سرعة تيربو"),
                    CardPackage(network_id=n2.id, name="مريس - كرت يومي ممتاز", price=350, duration="24 ساعة", data_limit="3 جيجابايت", description="باقة 24 ساعة"),
                    CardPackage(network_id=n2.id, name="مريس - باقة أسبوعية برو", price=1600, duration="7 أيام", data_limit="18 جيجابايت", description="استقرار وثبات"),
                    CardPackage(network_id=n2.id, name="مريس - باقة شهرية VIP", price=5500, duration="30 يوماً", data_limit="70 جيجابايت", description="بنج منخفض للألعاب"),

                    CardPackage(network_id=n3.id, name="الفهد - كرت ساعتين", price=150, duration="2 ساعة", data_limit="1.5 جيجابايت", description="باقة سريعة"),
                    CardPackage(network_id=n3.id, name="الفهد - كرت 48 ساعة", price=600, duration="يومان", data_limit="4 جيجابايت", description="يومان اتصال سلس"),
                    CardPackage(network_id=n3.id, name="الفهد - باقة أسبوع بلس", price=1400, duration="7 أيام", data_limit="14 جيجابايت", description="عروض الفهد"),
                    CardPackage(network_id=n3.id, name="الفهد - باقة شهرية توفير", price=4800, duration="30 يوماً", data_limit="50 جيجابايت", description="أقوى عروض الفهد")
                ]
                db.session.add_all(pkgs)
                db.session.commit()

                # إنشاء الحسابات البنكية الافتراضية
                banks = [
                    BankAccount(bank_name="بنك الكريمي للتمويل الأصغر الإسلامي", account_number="123456789", account_name="محفظة كروت شبكات الواي فاي", logo_icon="fa-landmark", instructions="يرجى كتابة اسمك ورقم هاتفك في خانة الغرض من التحويل"),
                    BankAccount(bank_name="بنك القطيبي الإسلامي", account_number="987654321", account_name="محفظة شبكات اليمن", logo_icon="fa-building-columns", instructions="إيداع عبر تطبيق بنك القطيبي أو أي فرع"),
                    BankAccount(bank_name="صرافة النجم للحوالات", account_number="777000111", account_name="إدارة المحفظة الإلكترونية", logo_icon="fa-money-bill-transfer", instructions="حوالة بالاسم والرقم الموضح وإرسال سند الاستلام"),
                    BankAccount(bank_name="محفظة جيب / كاش / شامل موني", account_number="777223344", account_name="محفظة كروت النت", logo_icon="fa-mobile-screen", instructions="تحويل مباشر من محفظتك الإلكترونية")
                ]
                db.session.add_all(banks)
                db.session.commit()

                # توليد كروت ابتدائية
                init_cards = []
                for p in pkgs:
                    for i in range(1, 4):
                        init_cards.append(Card(
                            network_id=p.network_id,
                            package_id=p.id,
                            pin_code=str(random.randint(10000000, 99999999)),
                            serial_number=f"SN-{p.id}-{1000 + i}",
                            status='available'
                        ))
                db.session.add_all(init_cards)
                db.session.commit()
                print("[DB] Initial default data generated successfully!")

# ==================== 1. مسارات المصادقة والمستخدمين (Auth API) ====================

@app.route('/api/auth/me', methods=['GET'])
def api_auth_me():
    user = get_current_user()
    if user:
        return jsonify(success=True, user=user.to_dict())
    return jsonify(success=False, user=None)

@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    data = request.get_json(force=True, silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify(success=False, message="يرجى إدخال اسم المستخدم وكلمة المرور"), 400

    # فحص بالاسم أو الإيميل أو الهاتف
    user = User.query.filter(
        (User.username.ilike(username)) |
        (User.email.ilike(username)) |
        (User.phone == username)
    ).first()

    if not user or not user.check_password(password):
        return jsonify(success=False, message="اسم المستخدم أو كلمة المرور غير صحيحة"), 401

    res = make_response(jsonify(success=True, message=f"أهلاً بك، {user.full_name}!", user=user.to_dict()))
    create_user_session(res, user.id)
    return res

@app.route('/api/auth/logout', methods=['POST'])
def api_auth_logout():
    res = make_response(jsonify(success=True, message="تم تسجيل الخروج بنجاح"))
    remove_user_session(res)
    return res

@app.route('/api/auth/send-otp', methods=['POST'])
def api_auth_send_otp():
    data = request.get_json(force=True, silent=True) or {}
    email = data.get('email', '').strip().lower()
    username = data.get('username', '').strip()

    if not email:
        return jsonify(success=False, message="يرجى إدخال البريد الإلكتروني"), 400

    # فحص تكرار الإيميل
    existing = User.query.filter(User.email.ilike(email)).first()
    if existing:
        return jsonify(success=False, message="هذا البريد الإلكتروني مسجل مسبقاً في النظام"), 400

    otp_code = str(random.randint(100000, 999999))
    expires = time.time() + 600

    # حفظ في جدول الـ OTP
    OtpRecord.query.filter_by(email=email).delete()
    db.session.add(OtpRecord(email=email, code=otp_code, expires_at=expires))
    db.session.commit()

    send_telegram_message(f"🔐 <b>كود تحقق تسجيل جديد (OTP)</b>:\n👤 المستخدم: {username}\n📧 البريد: {email}\n🔑 الرمز: <code>{otp_code}</code>")

    return jsonify(success=True, message=f"تم إرسال كود التحقق إلى بريدك الإلكتروني ({email})", otp_hint=otp_code)

@app.route('/api/auth/verify-otp', methods=['POST'])
def api_auth_verify_otp():
    data = request.get_json(force=True, silent=True) or {}
    full_name = data.get('full_name', '').strip()
    username = data.get('username', '').strip()
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()
    otp_code = data.get('otp_code', '').strip()

    if not all([full_name, username, password, email, otp_code]):
        return jsonify(success=False, message="يرجى تعبئة كافة الحقول وكود التحقق"), 400

    otp = OtpRecord.query.filter_by(email=email).first()
    if not otp or otp.code != otp_code or time.time() > otp.expires_at:
        return jsonify(success=False, message="كود التحقق غير صحيح أو انتهت صلاحيته"), 400

    # فحص عدم تكرار اسم المستخدم والبريد
    if User.query.filter(User.username.ilike(username)).first():
        return jsonify(success=False, message="اسم المستخدم موجود مسبقاً، يرجى اختيار اسم آخر"), 400
    if User.query.filter(User.email.ilike(email)).first():
        return jsonify(success=False, message="هذا البريد الإلكتروني مسجل مسبقاً"), 400

    user = User(
        full_name=full_name,
        username=username,
        phone=phone,
        email=email,
        role='client',
        balance=0.0,
        is_verified=True
    )
    user.set_password(password)
    db.session.add(user)
    OtpRecord.query.filter_by(email=email).delete()
    db.session.commit()

    send_telegram_message(f"👤 <b>تسجيل عميل جديد بالمنصة</b>:\n▪️ الاسم: {full_name}\n▪️ اسم المستخدم: {username}\n▪️ الهاتف: {phone or 'غير مسجل'}\n▪️ البريد: {email}")

    res = make_response(jsonify(success=True, message="تم تفعيل الحساب وتسجيل الدخول بنجاح!", user=user.to_dict()))
    create_user_session(res, user.id)
    return res

# ==================== 2. المتجر وشراء الكروت بنظام القفل (Shop & Concurrency Locking) ====================

@app.route('/api/shop/networks', methods=['GET'])
def api_shop_networks():
    user = get_current_user()
    networks = Network.query.filter_by(is_active=True).all()
    res = [n.to_dict(include_packages=True, current_user=user) for n in networks]
    return jsonify(success=True, networks=res)

@app.route('/api/shop/purchase', methods=['POST'])
def api_shop_purchase():
    """
    نظام الشراء الآمن وقفل المعاملة (Database Concurrency Locking / with_for_update)
    يضمن 100% عدم بيع نفس الكرت لأكثر من عميل في نفس اللحظة
    """
    user = get_current_user()
    if not user:
        return jsonify(success=False, message="يرجى تسجيل الدخول أولاً لإتمام الشراء"), 401

    data = request.get_json(force=True, silent=True) or {}
    package_id = data.get('package_id')
    network_id = data.get('network_id')

    if not package_id:
        return jsonify(success=False, message="يرجى اختيار الباقة المراد شراؤها"), 400

    try:
        # 1. فحص الباقة وسعرها
        pkg = CardPackage.query.filter_by(id=package_id, is_active=True).first()
        if not pkg:
            return jsonify(success=False, message="الباقة المطلوبة غير متوفرة أو معطلة"), 404

        # 2. قفل سجل العميل للتحقق من الرصيد وخصمه دون تضارب
        locked_user = User.query.filter_by(id=user.id).with_for_update().first()
        if locked_user.balance < pkg.price:
            return jsonify(success=False, message=f"رصيدك الحالي ({locked_user.balance:,.2f} ر.ي) غير كافٍ. سعر الباقة: {pkg.price:,.2f} ر.ي"), 400

        # 3. قفل أول كرت متاح بهذه الباقة فوراً عبر with_for_update() لمنع البيع المزدوج
        locked_card = Card.query.filter_by(package_id=package_id, status='available').with_for_update().first()
        if not locked_card:
            return jsonify(success=False, message="عذراً، نفد مخزون كروت هذه الباقة حالياً. يرجى مراجعة إدارة الشبكة أو تجربة باقة أخرى"), 400

        # 4. خصم الرصيد وتحديث الكرت للمباع
        locked_user.balance = round(float(locked_user.balance) - float(pkg.price), 2)
        locked_card.status = 'sold'
        locked_card.buyer_id = locked_user.id
        locked_card.purchased_at = datetime.utcnow()

        # 5. تسجيل العملية في جدول الطلبات (Orders)
        order_num = f"ORD-{int(time.time())}-{random.randint(1000, 9999)}"
        order = Order(
            order_number=order_num,
            user_id=locked_user.id,
            card_id=locked_card.id,
            network_id=locked_card.network_id,
            package_id=pkg.id,
            amount=float(pkg.price),
            status='completed',
            created_at=datetime.utcnow()
        )
        db.session.add(order)
        db.session.commit()

        # تجهيز بيانات الاستجابة
        card_info = {
            "id": locked_card.id,
            "pin_code": locked_card.pin_code,
            "serial_number": locked_card.serial_number or "",
            "package_name": pkg.name,
            "network_name": locked_card.network.name if locked_card.network else "",
            "login_url": locked_card.network.login_url if locked_card.network else "",
            "price": pkg.price,
            "purchased_at": locked_card.purchased_at.isoformat()
        }
        new_balance = locked_user.balance

        # إشعار تلجرام بعملية الشراء
        send_telegram_message(
            f"🛒 <b>عملية شراء كرت جديدة</b>:\n"
            f"▪️ العميل: {user.full_name}\n"
            f"▪️ الشبكة: {card_info['network_name']}\n"
            f"▪️ الباقة: {pkg.name}\n"
            f"▪️ السعر: {pkg.price:,.2f} ر.ي\n"
            f"▪️ رصيد العميل المتبقي: {new_balance:,.2f} ر.ي"
        )

        return jsonify(
            success=True,
            message="تم شراء الكرت بنجاح!",
            card=card_info,
            new_balance=new_balance
        )

    except Exception as e:
        db.session.rollback()
        print("[Purchase Error]:", e)
        return jsonify(success=False, message="حدث خطأ أثناء معالجة عملية الشراء، يرجى المحاولة مجدداً"), 500

@app.route('/api/shop/my-cards', methods=['GET'])
def api_shop_my_cards():
    user = get_current_user()
    if not user:
        return jsonify(success=False, message="غير مصرح"), 401
    cards = Card.query.filter_by(buyer_id=user.id, status='sold').order_by(Card.purchased_at.desc()).all()
    return jsonify(success=True, cards=[c.to_dict() for c in cards])

# ==================== 3. المحفظة والإيداعات (Wallet & Deposits API) ====================

@app.route('/api/wallet/banks', methods=['GET'])
def api_wallet_banks():
    banks = BankAccount.query.filter_by(is_active=True).all()
    return jsonify(success=True, banks=[b.to_dict() for b in banks])

@app.route('/api/wallet/deposit', methods=['POST'])
def api_wallet_deposit():
    user = get_current_user()
    if not user:
        return jsonify(success=False, message="يرجى تسجيل الدخول أولاً لتقديم طلب الإيداع"), 401

    amount_val = request.form.get('amount') or (request.json and request.json.get('amount'))
    try:
        amount = float(amount_val)
        if amount <= 0:
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify(success=False, message="يرجى إدخال مبلغ صحيح للإيداع"), 400

    customer_name = request.form.get('customer_name') or user.full_name
    phone = request.form.get('phone') or user.phone
    bank_name = request.form.get('bank_name') or "حساب بنكي / محفظة"

    receipt_img_path = None
    if 'receipt_image' in request.files:
        file = request.files['receipt_image']
        if file and file.filename:
            ext = os.path.splitext(file.filename)[1].lower()
            if ext in ['.jpg', '.jpeg', '.png', '.webp']:
                fname = f"receipt_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
                file.save(os.path.join(UPLOADS_DIR, fname))
                receipt_img_path = f"/uploads/receipts/{fname}"

    deposit = Deposit(
        user_id=user.id,
        customer_name=customer_name,
        phone=phone,
        bank_name=bank_name,
        amount=amount,
        receipt_img=receipt_img_path,
        status='pending'
    )
    db.session.add(deposit)
    db.session.commit()

    # إرسال إشعار تلجرام التفاعلي بأزرار الاعتماد الفورية
    send_telegram_deposit_card(deposit, user)

    return jsonify(
        success=True,
        message="تم رفع طلب الإيداع بنجاح، وسيتم مراجعته وإضافة الرصيد مع 10% بونص فور الاعتماد!",
        deposit=deposit.to_dict()
    )

@app.route('/api/wallet/my-deposits', methods=['GET'])
def api_wallet_my_deposits():
    user = get_current_user()
    if not user:
        return jsonify(success=False, message="غير مصرح"), 401
    deps = Deposit.query.filter_by(user_id=user.id).order_by(Deposit.created_at.desc()).all()
    return jsonify(success=True, deposits=[d.to_dict() for d in deps])

@app.route('/api/wallet/transactions', methods=['GET'])
def api_wallet_transactions():
    user = get_current_user()
    if not user:
        return jsonify(success=False, message="غير مصرح"), 401

    txs = []
    # الإيداعات المعتمدة
    deposits = Deposit.query.filter_by(user_id=user.id, status='approved').all()
    for d in deposits:
        txs.append({
            "id": f"dep-{d.id}",
            "type": "deposit",
            "title": f"إيداع رصيد ({d.bank_name})",
            "amount": d.amount,
            "created_at": d.reviewed_at.isoformat() if d.reviewed_at else d.created_at.isoformat(),
            "status": "مكتمل"
        })
        if d.bonus_amount and d.bonus_amount > 0:
            txs.append({
                "id": f"bon-{d.id}",
                "type": "bonus",
                "title": "بونص إيداع إضافي مجاني (10%)",
                "amount": d.bonus_amount,
                "created_at": d.reviewed_at.isoformat() if d.reviewed_at else d.created_at.isoformat(),
                "status": "مكتمل"
            })

    # المشتريات
    orders = Order.query.filter_by(user_id=user.id).all()
    for o in orders:
        pkg = CardPackage.query.get(o.package_id)
        pkg_name = pkg.name if pkg else "باقة واي فاي"
        txs.append({
            "id": f"ord-{o.id}",
            "type": "purchase",
            "title": f"شراء كرت: {pkg_name}",
            "amount": -o.amount,
            "created_at": o.created_at.isoformat(),
            "status": "مكتمل"
        })

    txs.sort(key=lambda x: x['created_at'], reverse=True)
    return jsonify(success=True, transactions=txs)

# ==================== 4. لوحة تحكم مالك الشبكة المتقدمة (Network Owner & 3 Bulk Methods) ====================

@app.route('/api/network-owner/my-network', methods=['GET'])
def api_network_owner_my_network():
    """عزل بيانات مالك الشبكة برمجياً: يرى فقط شبكته وباقاته وإحصائياته"""
    user = get_current_user()
    if not user or user.role not in ['network_owner', 'admin']:
        return jsonify(success=False, message="صلاحية غير مصرح بها لمالك الشبكة"), 403

    # جلب شبكة المالك
    if user.role == 'admin':
        net = Network.query.first()
    else:
        net = Network.query.filter((Network.owner_id == user.id) | (Network.id == user.network_id)).first()

    if not net:
        return jsonify(success=False, message="لم يتم ربط حسابك بشبكة محددة بعد"), 404

    packages = CardPackage.query.filter_by(network_id=net.id).all()
    cards = Card.query.filter_by(network_id=net.id).all()

    total_cards = len(cards)
    available_cards = sum(1 for c in cards if c.status == 'available')
    sold_cards = sum(1 for c in cards if c.status == 'sold')
    total_sales = sum(float(c.package.price) for c in cards if c.status == 'sold' and c.package)

    return jsonify(
        success=True,
        network=net.to_dict(include_packages=False),
        packages=[p.to_dict(current_user=user) for p in packages],
        stats={
            "total_packages": len(packages),
            "total_cards": total_cards,
            "available_cards": available_cards,
            "sold_cards": sold_cards,
            "total_sales": total_sales
        }
    )

@app.route('/api/network-owner/packages', methods=['POST'])
def api_network_owner_packages():
    user = get_current_user()
    if not user or user.role not in ['network_owner', 'admin']:
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    if user.role == 'admin':
        net = Network.query.first()
    else:
        net = Network.query.filter((Network.owner_id == user.id) | (Network.id == user.network_id)).first()

    if not net:
        return jsonify(success=False, message="الشبكة غير موجودة"), 404

    data = request.get_json(force=True, silent=True) or {}
    name = data.get('name', '').strip()
    price = float(data.get('price', 0))
    duration = data.get('duration', '').strip()
    data_limit = data.get('data_limit', '').strip()
    description = data.get('description', '').strip()

    if not name or price <= 0:
        return jsonify(success=False, message="يرجى كتابة اسم الباقة وتحديد سعر صحيح"), 400

    pkg = CardPackage(
        network_id=net.id,
        name=name,
        price=price,
        duration=duration,
        data_limit=data_limit,
        description=description,
        is_active=True
    )
    db.session.add(pkg)
    db.session.commit()

    return jsonify(success=True, message="تمت إضافة الباقة بنجاح!", package=pkg.to_dict(current_user=user))

@app.route('/api/network-owner/cards', methods=['GET'])
def api_network_owner_cards():
    user = get_current_user()
    if not user or user.role not in ['network_owner', 'admin']:
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    if user.role == 'admin':
        net = Network.query.first()
    else:
        net = Network.query.filter((Network.owner_id == user.id) | (Network.id == user.network_id)).first()

    if not net:
        return jsonify(success=False, message="الشبكة غير موجودة"), 404

    cards = Card.query.filter_by(network_id=net.id).order_by(Card.created_at.desc()).limit(300).all()
    return jsonify(success=True, cards=[c.to_dict() for c in cards])

@app.route('/api/network-owner/cards/bulk', methods=['POST'])
def api_network_owner_cards_bulk():
    """
    إتاحة 3 طرق لرفع الكروت دفعة واحدة (Bulk Upload):
    1. يدوياً: عبر حقل نصي كبير (Textarea)
    2. ملف إكسل (Excel): قراءة عمود الأكواد من .xlsx / .xls
    3. ملف بي دي إف (PDF): استخراج الأكواد الذكي من .pdf
    مع تطبيق الفحص الصارم لعدم تكرار أي كرت مسبقاً
    """
    user = get_current_user()
    if not user or user.role not in ['network_owner', 'admin']:
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    # استخراج معرف الباقة
    package_id = request.form.get('package_id') or (request.json and request.json.get('package_id'))
    if not package_id:
        return jsonify(success=False, message="يرجى تحديد الباقة التابع لها الكروت"), 400

    pkg = CardPackage.query.get(int(package_id))
    if not pkg:
        return jsonify(success=False, message="الباقة المحددة غير موجودة"), 404

    # التحقق من صلاحية المالك على هذه الباقة والشبكة
    if user.role != 'admin' and user.network_id != pkg.network_id and pkg.network.owner_id != user.id:
        return jsonify(success=False, message="لا تملك صلاحية إضافة كروت لهذه الشبكة"), 403

    upload_method = request.form.get('method') or (request.json and request.json.get('method')) or 'paste'
    extracted_cards = []

    # 1. الرفع عبر ملف Excel
    if upload_method == 'excel' or 'excel_file' in request.files:
        file = request.files.get('excel_file') or request.files.get('file')
        if not file or not file.filename:
            return jsonify(success=False, message="يرجى اختيار ملف Excel بصيغة .xlsx"), 400
        try:
            extracted_cards = parse_cards_from_excel(file.read())
        except Exception as e:
            return jsonify(success=False, message=f"فشل قراءة ملف Excel: {str(e)}"), 400

    # 2. الرفع عبر ملف PDF
    elif upload_method == 'pdf' or 'pdf_file' in request.files:
        file = request.files.get('pdf_file') or request.files.get('file')
        if not file or not file.filename:
            return jsonify(success=False, message="يرجى اختيار ملف PDF"), 400
        try:
            extracted_cards = parse_cards_from_pdf(file.read())
        except Exception as e:
            return jsonify(success=False, message=f"فشل قراءة واستخراج الكروت من ملف PDF: {str(e)}"), 400

    # 3. الرفع اليدوي عبر Textarea
    elif upload_method == 'paste':
        raw_text = request.form.get('pins_text') or (request.json and request.json.get('pins_text')) or ''
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        for line in lines:
            parts = [p.strip() for p in re.split(r'[,|\t\s]+', line) if p.strip()]
            if parts:
                pin = parts[0]
                serial = parts[1] if len(parts) > 1 else None
                extracted_cards.append({"pin": pin, "serial": serial})

    # خيار التوليد التلقائي الإضافي (Auto-generate)
    elif upload_method == 'auto':
        count = int(request.form.get('count') or (request.json and request.json.get('count')) or 10)
        count = min(max(count, 1), 200)
        for i in range(count):
            extracted_cards.append({
                "pin": str(random.randint(10000000, 99999999)),
                "serial": f"SN-{pkg.id}-{int(time.time()) % 100000}{i}"
            })

    if not extracted_cards:
        return jsonify(success=False, message="لم يتم العثور على أي أكواد كروت صحيحة في الإدخال"), 400

    # فحص الكروت المكررة مسبقاً في قاعدة البيانات
    pins_list = [c['pin'] for c in extracted_cards]
    existing_cards = Card.query.filter(Card.pin_code.in_(pins_list)).all()
    existing_pins = set(c.pin_code for c in existing_cards)

    added_cards = []
    duplicate_pins = []
    seen_in_batch = set()

    for item in extracted_cards:
        p_code = item['pin']
        if p_code in existing_pins or p_code in seen_in_batch:
            duplicate_pins.append(p_code)
            continue

        seen_in_batch.add(p_code)
        card = Card(
            network_id=pkg.network_id,
            package_id=pkg.id,
            pin_code=p_code,
            serial_number=item.get('serial') or f"SN-{int(time.time()) % 100000}-{len(added_cards)+1}",
            status='available'
        )
        added_cards.append(card)

    # حفظ الكروت الجديدة غير المكررة
    if added_cards:
        db.session.add_all(added_cards)
        db.session.commit()

    # صياغة رسائل النتيجة والتوضيح
    if not added_cards and duplicate_pins:
        return jsonify(
            success=False,
            message="جميع الكروت المدخلة موجودة مسبقاً في النظام ولم تتم إضافة أي كرت مكرر!",
            added_count=0,
            duplicate_count=len(duplicate_pins),
            duplicates=duplicate_pins[:20]
        ), 400

    msg = f"تمت إضافة {len(added_cards)} كرت جديد بنجاح إلى باقة ({pkg.name})!"
    if duplicate_pins:
        msg += f" (تم تخطي {len(duplicate_pins)} كرت لأنها موجودة مسبقاً)"

    return jsonify(
        success=True,
        message=msg,
        added_count=len(added_cards),
        duplicate_count=len(duplicate_pins),
        duplicates=duplicate_pins[:20]
    )

@app.route('/api/network-owner/cards/<int:card_id>', methods=['DELETE'])
def api_network_owner_delete_card(card_id):
    user = get_current_user()
    if not user or user.role not in ['network_owner', 'admin']:
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    card = Card.query.get(card_id)
    if not card:
        return jsonify(success=False, message="الكرت غير موجود"), 404

    if user.role != 'admin' and card.network.owner_id != user.id and user.network_id != card.network_id:
        return jsonify(success=False, message="لا تملك صلاحية حذف هذا الكرت"), 403

    if card.status == 'sold':
        return jsonify(success=False, message="لا يمكن حذف كرت تم بيعه للعميل مسبقاً"), 400

    db.session.delete(card)
    db.session.commit()
    return jsonify(success=True, message="تم حذف الكرت من المخزون بنجاح")

@app.route('/api/network-owner/statement', methods=['GET'])
def api_network_owner_statement():
    user = get_current_user()
    if not user or user.role not in ['network_owner', 'admin']:
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    if user.role == 'admin':
        net = Network.query.first()
    else:
        net = Network.query.filter((Network.owner_id == user.id) | (Network.id == user.network_id)).first()

    if not net:
        return jsonify(success=False, message="الشبكة غير موجودة"), 404

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = Card.query.filter_by(network_id=net.id, status='sold')
    if date_from:
        query = query.filter(Card.purchased_at >= datetime.fromisoformat(date_from))
    if date_to:
        query = query.filter(Card.purchased_at <= datetime.fromisoformat(date_to + ' 23:59:59'))

    sold_cards = query.order_by(Card.purchased_at.desc()).all()
    items = []
    total_sales = 0

    for c in sold_cards:
        p_price = float(c.package.price) if c.package else 0
        total_sales += p_price
        buyer = User.query.get(c.buyer_id) if c.buyer_id else None
        items.append({
            "card_id": c.id,
            "package_name": c.package.name if c.package else '',
            "price": p_price,
            "pin_code": c.pin_code,
            "serial_number": c.serial_number or '',
            "buyer_name": buyer.full_name if buyer else 'عميل',
            "purchased_at": c.purchased_at.isoformat() if c.purchased_at else ''
        })

    return jsonify(
        success=True,
        network=net.to_dict(include_packages=False),
        total_sales=total_sales,
        sold_count=len(items),
        items=items
    )

# ==================== 5. لوحة تحكم المدير العام (Super Admin API) ====================

@app.route('/api/admin/stats', methods=['GET'])
def api_admin_stats():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    total_users = User.query.count()
    total_networks = Network.query.count()
    total_packages = CardPackage.query.count()
    total_cards = Card.query.count()
    sold_cards = Card.query.filter_by(status='sold').count()
    available_cards = Card.query.filter_by(status='available').count()

    total_sales = sum(float(c.package.price) for c in Card.query.filter_by(status='sold').all() if c.package)
    pending_deposits = Deposit.query.filter_by(status='pending').count()
    approved_deposits = sum(float(d.approved_amount or d.amount) for d in Deposit.query.filter_by(status='approved').all())

    return jsonify(
        success=True,
        stats={
            "total_users": total_users,
            "total_networks": total_networks,
            "total_packages": total_packages,
            "total_cards": total_cards,
            "sold_cards": sold_cards,
            "available_cards": available_cards,
            "total_sales": total_sales,
            "pending_deposits": pending_deposits,
            "approved_deposits": approved_deposits,
            "estimated_profit": round(total_sales * 0.15, 2)
        }
    )

@app.route('/api/admin/profit-analytics', methods=['GET'])
def api_admin_profit_analytics():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    networks = Network.query.all()
    net_reports = []
    grand_sales = 0
    grand_sold_cards = 0

    for net in networks:
        cards = Card.query.filter_by(network_id=net.id, status='sold').all()
        sales = sum(float(c.package.price) for c in cards if c.package)
        grand_sales += sales
        grand_sold_cards += len(cards)

        net_reports.append({
            "id": net.id,
            "name": net.name,
            "code": net.code,
            "logo_icon": net.logo_icon,
            "packages_count": len(net.packages),
            "sold_cards_count": len(cards),
            "available_cards_count": Card.query.filter_by(network_id=net.id, status='available').count(),
            "total_sales": sales,
            "system_commission": round(sales * 0.05, 2),
            "owner_net_profit": round(sales * 0.95, 2)
        })

    return jsonify(
        success=True,
        grand_total_sales=grand_sales,
        grand_sold_cards=grand_sold_cards,
        grand_system_profit=round(grand_sales * 0.05, 2),
        networks=net_reports
    )

@app.route('/api/admin/deposits', methods=['GET'])
def api_admin_deposits():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    status_filter = request.args.get('status', 'pending')
    query = Deposit.query
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)

    deposits = query.order_by(Deposit.created_at.desc()).all()
    return jsonify(success=True, deposits=[d.to_dict() for d in deposits])

@app.route('/api/admin/deposits/<int:dep_id>/approve', methods=['POST'])
def api_admin_deposit_approve(dep_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    dep = Deposit.query.get(dep_id)
    if not dep or dep.status != 'pending':
        return jsonify(success=False, message="طلب الإيداع غير موجود أو تمت معالجته مسبقاً"), 400

    data = request.get_json(force=True, silent=True) or {}
    actual_amount = float(data.get('approved_amount') or dep.amount)

    target_user = User.query.get(dep.user_id)
    if not target_user:
        return jsonify(success=False, message="حساب العميل غير موجود"), 404

    bonus = round(actual_amount * 0.10, 2)
    total_added = round(actual_amount + bonus, 2)

    target_user.balance = round(float(target_user.balance) + total_added, 2)
    dep.status = 'approved'
    dep.approved_amount = actual_amount
    dep.bonus_amount = bonus
    dep.reviewed_at = datetime.utcnow()
    db.session.commit()

    # تحديث إشعار التلجرام إن وجد
    if dep.telegram_message_id:
        update_text = (
            f"✅ <b>تم اعتماد طلب الإيداع (#{dep.id})</b>\n"
            f"👤 العميل: {dep.customer_name}\n"
            f"💰 المبلغ المعتمد: {actual_amount:,.2f} ر.ي\n"
            f"🎁 البونص (10%): {bonus:,.2f} ر.ي\n"
            f"💳 الرصيد المضاف: {total_added:,.2f} ر.ي\n"
            f"💵 رصيد العميل الحالي: {target_user.balance:,.2f} ر.ي"
        )
        edit_telegram_message_text(CHAT_ID, dep.telegram_message_id, update_text)

    return jsonify(
        success=True,
        message=f"تم اعتماد الإيداع بنجاح وإضافة {actual_amount:,.2f} + بونص {bonus:,.2f} ر.ي لحساب العميل!",
        deposit=dep.to_dict()
    )

@app.route('/api/admin/deposits/<int:dep_id>/reject', methods=['POST'])
def api_admin_deposit_reject(dep_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    dep = Deposit.query.get(dep_id)
    if not dep or dep.status != 'pending':
        return jsonify(success=False, message="طلب الإيداع غير موجود أو تمت معالجته مسبقاً"), 400

    dep.status = 'rejected'
    dep.reviewed_at = datetime.utcnow()
    db.session.commit()

    if dep.telegram_message_id:
        edit_telegram_message_text(CHAT_ID, dep.telegram_message_id, f"❌ تم رفض طلب الإيداع (#{dep.id}) للعميل {dep.customer_name}.")

    return jsonify(success=True, message="تم رفض طلب الإيداع", deposit=dep.to_dict())

@app.route('/api/admin/users', methods=['GET', 'POST'])
def api_admin_users():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    if request.method == 'GET':
        users = User.query.order_by(User.created_at.desc()).all()
        return jsonify(success=True, users=[u.to_dict() for u in users])

    # إضافة مستخدم جديد مع فحص التكرار الصارم
    data = request.get_json(force=True, silent=True) or {}
    full_name = data.get('full_name', '').strip()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    email = data.get('email', '').strip().lower() or None
    phone = data.get('phone', '').strip()
    role = data.get('role', 'client')
    network_id = data.get('network_id') or None

    if not full_name or not username or not password:
        return jsonify(success=False, message="يرجى تعبئة الاسم واسم المستخدم وكلمة المرور"), 400

    if User.query.filter(User.username.ilike(username)).first():
        return jsonify(success=False, message="اسم المستخدم موجود مسبقاً، يرجى اختيار اسم آخر"), 400

    if email and User.query.filter(User.email.ilike(email)).first():
        return jsonify(success=False, message="هذا البريد الإلكتروني مسجل مسبقاً"), 400

    new_u = User(
        full_name=full_name,
        username=username,
        email=email,
        phone=phone,
        role=role,
        network_id=network_id,
        balance=float(data.get('balance', 0)),
        is_verified=True
    )
    new_u.set_password(password)
    db.session.add(new_u)
    db.session.commit()

    return jsonify(success=True, message="تم إنشاء الحساب بنجاح!", user=new_u.to_dict())

@app.route('/api/admin/users/<int:u_id>', methods=['PUT'])
def api_admin_user_edit(u_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    target = User.query.get(u_id)
    if not target:
        return jsonify(success=False, message="المستخدم غير موجود"), 404

    data = request.get_json(force=True, silent=True) or {}
    new_username = data.get('username', '').strip()
    new_email = data.get('email', '').strip().lower() or None

    # فحص التكرار إذا تم تعديل اسم المستخدم أو الإيميل
    if new_username and new_username.lower() != target.username.lower():
        if User.query.filter(User.username.ilike(new_username)).first():
            return jsonify(success=False, message="اسم المستخدم الجديد مسجل مسبقاً"), 400
        target.username = new_username

    if new_email and new_email != (target.email or '').lower():
        if User.query.filter(User.email.ilike(new_email)).first():
            return jsonify(success=False, message="هذا البريد الإلكتروني مسجل مسبقاً"), 400
        target.email = new_email

    if 'full_name' in data and data['full_name'].strip():
        target.full_name = data['full_name'].strip()
    if 'phone' in data:
        target.phone = data['phone'].strip()
    if 'role' in data:
        target.role = data['role']
    if 'network_id' in data:
        target.network_id = data['network_id'] or None
    if 'balance' in data:
        target.balance = float(data['balance'])
    if 'password' in data and data['password'].strip():
        target.set_password(data['password'].strip())

    db.session.commit()
    return jsonify(success=True, message="تم تحديث بيانات المستخدم بنجاح!", user=target.to_dict())

@app.route('/api/admin/users/<int:u_id>/balance', methods=['POST'])
def api_admin_user_balance(u_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    target = User.query.get(u_id)
    if not target:
        return jsonify(success=False, message="المستخدم غير موجود"), 404

    data = request.get_json(force=True, silent=True) or {}
    action = data.get('action') # 'add', 'deduct'
    amount = float(data.get('amount', 0))

    if amount <= 0:
        return jsonify(success=False, message="يرجى كتابة مبلغ صحيح"), 400

    if action == 'add':
        target.balance = round(float(target.balance) + amount, 2)
    elif action == 'deduct':
        target.balance = round(max(0.0, float(target.balance) - amount), 2)
    else:
        return jsonify(success=False, message="نوع العملية غير صحيح"), 400

    db.session.commit()
    return jsonify(success=True, message=f"تم تعديل الرصيد بنجاح! الرصيد الجديد: {target.balance:,.2f} ر.ي", user=target.to_dict())

@app.route('/api/admin/networks', methods=['GET', 'POST'])
def api_admin_networks():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    if request.method == 'GET':
        nets = Network.query.all()
        return jsonify(success=True, networks=[n.to_dict(include_packages=True, current_user=user) for n in nets])

    data = request.get_json(force=True, silent=True) or {}
    name = data.get('name', '').strip()
    code = data.get('code', '').strip().lower() or re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())
    logo_icon = data.get('logo_icon', 'fa-wifi')
    description = data.get('description', '').strip()
    login_url = data.get('login_url', '').strip()
    owner_id = data.get('owner_id') or None

    if not name:
        return jsonify(success=False, message="يرجى كتابة اسم الشبكة"), 400

    if Network.query.filter_by(code=code).first():
        code = f"{code}_{random.randint(10, 99)}"

    net = Network(name=name, code=code, logo_icon=logo_icon, description=description, login_url=login_url, owner_id=owner_id)
    db.session.add(net)
    db.session.commit()
    return jsonify(success=True, message="تمت إضافة الشبكة بنجاح!", network=net.to_dict(current_user=user))

@app.route('/api/admin/networks/<int:n_id>', methods=['PUT', 'DELETE'])
def api_admin_network_modify(n_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    net = Network.query.get(n_id)
    if not net:
        return jsonify(success=False, message="الشبكة غير موجودة"), 404

    if request.method == 'DELETE':
        db.session.delete(net)
        db.session.commit()
        return jsonify(success=True, message="تم حذف الشبكة بنجاح")

    data = request.get_json(force=True, silent=True) or {}
    if 'name' in data and data['name'].strip():
        net.name = data['name'].strip()
    if 'logo_icon' in data:
        net.logo_icon = data['logo_icon']
    if 'description' in data:
        net.description = data['description']
    if 'login_url' in data:
        net.login_url = data['login_url']
    if 'owner_id' in data:
        net.owner_id = data['owner_id'] or None
    if 'is_active' in data:
        net.is_active = bool(data['is_active'])

    db.session.commit()
    return jsonify(success=True, message="تم تحديث الشبكة بنجاح!", network=net.to_dict(current_user=user))

@app.route('/api/admin/packages', methods=['POST'])
def api_admin_package_add():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    data = request.get_json(force=True, silent=True) or {}
    net_id = data.get('network_id')
    name = data.get('name', '').strip()
    price = float(data.get('price', 0))
    duration = data.get('duration', '').strip()
    data_limit = data.get('data_limit', '').strip()
    description = data.get('description', '').strip()

    if not net_id or not name or price <= 0:
        return jsonify(success=False, message="يرجى تحديد الشبكة والاسم وسعر صحيح للباقة"), 400

    pkg = CardPackage(network_id=net_id, name=name, price=price, duration=duration, data_limit=data_limit, description=description)
    db.session.add(pkg)
    db.session.commit()
    return jsonify(success=True, message="تمت إضافة الباقة بنجاح!", package=pkg.to_dict(current_user=user))

@app.route('/api/admin/packages/<int:p_id>', methods=['PUT', 'DELETE'])
def api_admin_package_modify(p_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    pkg = CardPackage.query.get(p_id)
    if not pkg:
        return jsonify(success=False, message="الباقة غير موجودة"), 404

    if request.method == 'DELETE':
        db.session.delete(pkg)
        db.session.commit()
        return jsonify(success=True, message="تم حذف الباقة بنجاح")

    data = request.get_json(force=True, silent=True) or {}
    if 'name' in data and data['name'].strip():
        pkg.name = data['name'].strip()
    if 'price' in data and float(data['price']) > 0:
        pkg.price = float(data['price'])
    if 'duration' in data:
        pkg.duration = data['duration']
    if 'data_limit' in data:
        pkg.data_limit = data['data_limit']
    if 'description' in data:
        pkg.description = data['description']
    if 'is_active' in data:
        pkg.is_active = bool(data['is_active'])

    db.session.commit()
    return jsonify(success=True, message="تم تحديث الباقة بنجاح!", package=pkg.to_dict(current_user=user))

@app.route('/api/admin/cards-inventory', methods=['GET'])
def api_admin_cards_inventory():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    cards = Card.query.order_by(Card.created_at.desc()).limit(500).all()
    return jsonify(success=True, cards=[c.to_dict() for c in cards])

@app.route('/api/admin/banks', methods=['GET', 'POST'])
def api_admin_banks():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    if request.method == 'GET':
        banks = BankAccount.query.all()
        return jsonify(success=True, banks=[b.to_dict() for b in banks])

    data = request.get_json(force=True, silent=True) or {}
    bank_name = data.get('bank_name', '').strip()
    account_number = data.get('account_number', '').strip()
    account_name = data.get('account_name', '').strip()
    instructions = data.get('instructions', '').strip()
    logo_icon = data.get('logo_icon', 'fa-landmark')

    if not bank_name or not account_number or not account_name:
        return jsonify(success=False, message="يرجى كتابة اسم البنك ورقم الحساب واسم المستفيد"), 400

    bank = BankAccount(bank_name=bank_name, account_number=account_number, account_name=account_name, instructions=instructions, logo_icon=logo_icon)
    db.session.add(bank)
    db.session.commit()
    return jsonify(success=True, message="تمت إضافة الحساب البنكي بنجاح!", bank=bank.to_dict())

@app.route('/api/admin/banks/<int:b_id>', methods=['PUT', 'DELETE'])
def api_admin_bank_modify(b_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    bank = BankAccount.query.get(b_id)
    if not bank:
        return jsonify(success=False, message="الحساب البنكي غير موجود"), 404

    if request.method == 'DELETE':
        db.session.delete(bank)
        db.session.commit()
        return jsonify(success=True, message="تم حذف الحساب البنكي بنجاح")

    data = request.get_json(force=True, silent=True) or {}
    if 'bank_name' in data: bank.bank_name = data['bank_name']
    if 'account_number' in data: bank.account_number = data['account_number']
    if 'account_name' in data: bank.account_name = data['account_name']
    if 'instructions' in data: bank.instructions = data['instructions']
    if 'logo_icon' in data: bank.logo_icon = data['logo_icon']
    if 'is_active' in data: bank.is_active = bool(data['is_active'])

    db.session.commit()
    return jsonify(success=True, message="تم تحديث بيانات الحساب البنكي!", bank=bank.to_dict())

@app.route('/api/admin/customers-master-report', methods=['GET'])
def api_admin_customers_master_report():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    users = User.query.filter(User.role.in_(['client', 'customer'])).all()
    reports = []
    total_customers_balance = 0
    total_customers_purchases = 0

    for u in users:
        bought_cards = Card.query.filter_by(buyer_id=u.id, status='sold').all()
        spent = sum(float(c.package.price) for c in bought_cards if c.package)
        total_customers_balance += float(u.balance)
        total_customers_purchases += spent

        reports.append({
            "id": u.id,
            "full_name": u.full_name,
            "username": u.username,
            "phone": u.phone or '',
            "email": u.email or '',
            "balance": round(float(u.balance), 2),
            "purchased_cards_count": len(bought_cards),
            "total_spent": round(spent, 2),
            "created_at": u.created_at.isoformat() if u.created_at else ''
        })

    return jsonify(
        success=True,
        total_customers_count=len(users),
        total_customers_balance=round(total_customers_balance, 2),
        total_customers_purchases=round(total_customers_purchases, 2),
        customers=reports
    )

@app.route('/api/admin/networks/<int:n_id>/statement', methods=['GET'])
def api_admin_network_statement(n_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    net = Network.query.get(n_id)
    if not net:
        return jsonify(success=False, message="الشبكة غير موجودة"), 404

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = Card.query.filter_by(network_id=net.id, status='sold')
    if date_from:
        query = query.filter(Card.purchased_at >= datetime.fromisoformat(date_from))
    if date_to:
        query = query.filter(Card.purchased_at <= datetime.fromisoformat(date_to + ' 23:59:59'))

    sold_cards = query.order_by(Card.purchased_at.desc()).all()
    items = []
    total_sales = 0

    for c in sold_cards:
        p_price = float(c.package.price) if c.package else 0
        total_sales += p_price
        buyer = User.query.get(c.buyer_id) if c.buyer_id else None
        items.append({
            "card_id": c.id,
            "package_name": c.package.name if c.package else '',
            "price": p_price,
            "pin_code": c.pin_code,
            "serial_number": c.serial_number or '',
            "buyer_name": buyer.full_name if buyer else 'عميل',
            "purchased_at": c.purchased_at.isoformat() if c.purchased_at else ''
        })

    return jsonify(
        success=True,
        network=net.to_dict(include_packages=False),
        total_sales=total_sales,
        sold_count=len(items),
        items=items
    )

# ==================== 6. مسار Webhook التلجرام ====================

@app.route('/api/telegram/webhook', methods=['POST', 'GET'])
@app.route('/telegram-webhook', methods=['POST', 'GET'])
def api_telegram_webhook():
    if request.method == 'GET':
        return jsonify(success=True, message="Telegram webhook endpoint is active")

    data = request.get_json(force=True, silent=True) or {}
    if 'callback_query' in data:
        cq = data['callback_query']
        process_telegram_callback_action(
            cq.get('data', ''),
            cq.get('id'),
            cq.get('message', {}).get('message_id'),
            cq.get('message', {}).get('chat', {}).get('id')
        )
    return jsonify(ok=True)

# ==================== 7. خدمة الملفات الثابتة والصفحة الرئيسية ====================

@app.after_request
def add_cache_headers(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/')
def serve_index():
    for candidate in [os.path.join(PUBLIC_DIR, 'index.html'), os.path.join(BASE_DIR, 'index.html')]:
        if os.path.exists(candidate):
            with open(candidate, 'r', encoding='utf-8') as f:
                return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
    return "<h1>Yemen Wi-Fi Wallet Platform</h1>", 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/<path:filename>')
def serve_static(filename):
    # حماية من إعادة توجيه امتدادات الملفات إلى صفحة index.html
    ext = os.path.splitext(filename)[1].lower()
    
    # محاولة إيجاد الملف في public أو في الجذر
    for root_dir in [PUBLIC_DIR, BASE_DIR]:
        target = os.path.join(root_dir, filename)
        if os.path.exists(target) and not os.path.isdir(target):
            mime_types = {
                '.js': 'application/javascript; charset=utf-8',
                '.css': 'text/css; charset=utf-8',
                '.json': 'application/json; charset=utf-8',
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.webp': 'image/webp',
                '.svg': 'image/svg+xml',
                '.ico': 'image/x-icon',
                '.html': 'text/html; charset=utf-8',
                '.pdf': 'application/pdf',
                '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            }
            content_type = mime_types.get(ext, 'application/octet-stream')
            with open(target, 'rb') as f:
                return f.read(), 200, {'Content-Type': content_type}

    # إذا كان الملف المفقود ملف كود أو صورة نرجع 404 مباشرة لتجنب أخطاء MIME Type
    if ext in ['.js', '.css', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.ico', '.json']:
        return jsonify(error="Asset not found", path=filename), 404

    # المسارات الأخرى تعود للصفحة الرئيسية (SPA Fallback)
    return serve_index()

# ==================== نقطة الإقلاع والتشغيل ====================

# تشغيل التهيئة التلقائية ومستمع تيليجرام لضمان العمل تحت كافة خوادم الويب (Gunicorn / Docker / Direct Python)
try:
    seed_initial_database()
except Exception as e:
    print("[Startup DB Init Warning]:", e)

try:
    start_telegram_listener()
except Exception as e:
    print("[Startup Telegram Warning]:", e)

def run_server():
    print("=" * 65)
    print(f"🚀 خادم محفظة كروت الواي فاي (اليمن) يعمل الآن بنجاح على المنفذ {PORT}")
    print(f"🗄️ محرك البيانات: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")
    print(f"📱 البوت التفاعلي المربوط: @Croati_Bot (Chat ID: {CHAT_ID})")
    print(f"👑 حساب المدير العام: مشعل المريسي (Moshal380.)")
    print("=" * 65)

    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)

if __name__ == '__main__':
    run_server()
