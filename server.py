#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
خادم منصة محفظة كروت شبكات الواي فاي (Yemen Wi-Fi Wallet)
مرقى ومحدث بالكامل للعمل بنظام قواعد البيانات العلائقية الاحترافية (PostgreSQL / SQLAlchemy)
- دعم DATABASE_URL التلقائي لـ Railway و Render مع الترقية الآلية للأعمدة
- نظام قفل المعاملات (Database Locking / with_for_update) لمنع بيع الكروت مرتين
- عزل بيانات ملاك الشبكات برمجياً بنسبة 100%
- رفع الكروت دفعة واحدة عبر 3 طرق: يدوي (Textarea)، ملف إكسل (Excel .xlsx)، ملف بي دي إف (PDF .pdf)
- منع تكرار الكروت أو المستخدمين برسائل توضيحية دقيقة
- اعتماد سندات الإيداع المباشر من داخل بوت التلجرام نفسه عبر أزرار تفاعلية (Inline Keyboard)
- ميزة الكرت المجاني الإضافي على ظهر الكرت المشترى مع تحديد وقته
- إدارة شاملة (إضافة / تعديل / حذف) للحسابات والشبكات والباقات والبنوك
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
import urllib.error
from datetime import datetime
from sqlalchemy import text

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

import base64
# استيراد النماذج وقاعدة البيانات
from models import db, User, Network, CardPackage, Card, Order, Deposit, BankAccount, SessionToken, OtpRecord, Payout

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
    db_path = os.path.join(DATA_DIR, 'yemen_wifi.db')
    DATABASE_URL = f"sqlite:///{db_path}"
    print(f"[DB] Using local SQLite database at: {db_path}")
else:
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
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8939237549:AAES0BcMqQy61KVF-qAEQsAHJjNh_J8X_W8').strip()
CHAT_ID = os.environ.get('CHAT_ID', '5958490522').strip()
PORT = int(os.environ.get('PORT', 8080))

# ==================== دوال الجلسات والمصادقة المساعدة (مع بقاء دائم للجلسة) ====================

def get_session_id_from_request():
    """استخراج معرف الجلسة من الكوكيز أو الهيدر لضمان عدم الخروج من الحساب عند التحديث أو العودة"""
    sid = request.cookies.get('session_id')
    if not sid:
        sid = request.headers.get('X-Session-Token')
    if not sid:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            sid = auth_header.replace('Bearer ', '', 1).strip()
    if not sid:
        sid = request.args.get('session_token')
    return sid.strip() if sid else None

def get_current_user():
    session_id = get_session_id_from_request()
    if not session_id:
        return None
    session_token = SessionToken.query.filter_by(session_id=session_id).first()
    if not session_token:
        return None
    user = User.query.get(session_token.user_id)
    return user

def create_user_session(response, user_id, existing_session_id=None):
    session_id = existing_session_id or str(uuid.uuid4())
    if not SessionToken.query.filter_by(session_id=session_id).first():
        token = SessionToken(session_id=session_id, user_id=user_id, created_at=datetime.utcnow())
        db.session.add(token)
        db.session.commit()
    response.set_cookie(
        'session_id',
        session_id,
        max_age=365 * 24 * 3600, # سنة كاملة لضمان البقاء داخل الحساب دائماً
        httponly=False,
        samesite='Lax',
        path='/'
    )
    return session_id

def remove_user_session(response):
    session_id = get_session_id_from_request()
    if session_id:
        SessionToken.query.filter_by(session_id=session_id).delete()
        db.session.commit()
    response.set_cookie('session_id', '', expires=0, path='/')

def parse_iso_date(date_str, is_end=False):
    """تحويل نص التاريخ بمرونة إلى كائن datetime"""
    if not date_str:
        return None
    try:
        clean = str(date_str).strip()
        if len(clean) == 10:
            clean = clean + (' 23:59:59' if is_end else ' 00:00:00')
        return datetime.fromisoformat(clean)
    except Exception:
        return None

# ==================== دوال المحاسبة وصرف المستحقات والأرباح الموحدة ====================

def get_network_owner_user(net):
    """جلب حساب مالك الشبكة المرتبط بالشبكة إن وجد"""
    if not net:
        return None
    if net.owner_id:
        u = User.query.get(net.owner_id)
        if u:
            return u
    return User.query.filter_by(network_id=net.id, role='network_owner').first()

def compute_card_sale_split(card):
    """احتساب سعر البيع، مستحقات مالك الشبكة (سعر التكلفة)، وصافي ربح المنصة للكرت المباع"""
    if not card or not card.package:
        return 0.0, 0.0, 0.0
    p_price = float(card.package.price or 0)
    raw_cost = getattr(card.package, 'cost_price', None)
    p_cost = float(raw_cost if raw_cost is not None and raw_cost > 0 else p_price * 0.85)
    p_profit = max(0.0, p_price - p_cost)
    return round(p_price, 2), round(p_cost, 2), round(p_profit, 2)

def compute_admin_profit_financials():
    """احتساب أرباح المنصة الإجمالية، المسحوبات المصروفة للمدير العام، والرصيد المتاح للصرف حالياً"""
    sold_cards = Card.query.filter_by(status='sold').all()
    gross_profit = 0.0
    total_sales = 0.0
    for c in sold_cards:
        p_price, p_cost, p_profit = compute_card_sale_split(c)
        total_sales += p_price
        gross_profit += p_profit

    gross_profit = round(gross_profit, 2)
    total_sales = round(total_sales, 2)

    admin_payouts = Payout.query.filter_by(beneficiary_type='admin_profit').all()
    disbursed_amount = round(sum(float(p.amount or 0) for p in admin_payouts), 2)
    available_balance = round(max(0.0, gross_profit - disbursed_amount), 2)

    return {
        "total_sales": total_sales,
        "gross_profit": gross_profit,
        "disbursed_amount": disbursed_amount,
        "available_balance": available_balance,
        "payouts_count": len(admin_payouts)
    }

def compute_network_financials(net):
    """احتساب مستحقات مالك الشبكة الإجمالية، المسحوبات المسلمة، والرصيد المستحق المتاح للصرف"""
    if not net:
        return {
            "total_sales": 0.0,
            "gross_dues": 0.0,
            "disbursed_amount": 0.0,
            "available_balance": 0.0,
            "net_profit": 0.0,
            "payouts_count": 0
        }

    sold_cards = Card.query.filter_by(network_id=net.id, status='sold').all()
    total_sales = 0.0
    sales_dues = 0.0
    net_profit = 0.0
    for c in sold_cards:
        p_price, p_cost, p_profit = compute_card_sale_split(c)
        total_sales += p_price
        sales_dues += p_cost
        net_profit += p_profit

    total_sales = round(total_sales, 2)
    sales_dues = round(sales_dues, 2)
    net_profit = round(net_profit, 2)

    owner_user = get_network_owner_user(net)
    net_payouts = Payout.query.filter(
        (Payout.network_id == net.id) |
        (Payout.beneficiary_key == f"network_{net.id}")
    ).all()
    disbursed_amount = round(sum(float(p.amount or 0) for p in net_payouts), 2)

    if sales_dues > 0:
        gross_dues = sales_dues
        available_balance = round(max(0.0, gross_dues - disbursed_amount), 2)
    elif owner_user and float(owner_user.balance or 0) > 0:
        available_balance = round(float(owner_user.balance or 0), 2)
        gross_dues = round(available_balance + disbursed_amount, 2)
    else:
        gross_dues = sales_dues
        available_balance = round(max(0.0, gross_dues - disbursed_amount), 2)

    return {
        "total_sales": total_sales,
        "gross_dues": gross_dues,
        "disbursed_amount": disbursed_amount,
        "available_balance": available_balance,
        "net_profit": net_profit,
        "payouts_count": len(net_payouts),
        "owner_user": owner_user
    }

def get_all_payout_beneficiaries():
    """بناء القائمة الموحدة للمستفيدين (حساب المدير العام + جميع ملاك الشبكات) مع أرصدتهم الفورية"""
    beneficiaries = []

    # 1. حساب المدير العام (أرباح المنصة)
    admin_user = User.query.filter(
        (User.username == "مشعل المريسي") | (User.role == "admin")
    ).order_by(User.id.asc()).first()
    admin_fin = compute_admin_profit_financials()
    beneficiaries.append({
        "key": "admin_profit",
        "beneficiary_type": "admin_profit",
        "label": f"حساب المدير العام (أرباح المنصة) — {admin_user.full_name if admin_user else 'مشعل المريسي'}",
        "beneficiary_name": "حساب المدير العام (أرباح المنصة)",
        "user_id": admin_user.id if admin_user else None,
        "network_id": None,
        "gross_earned": admin_fin["gross_profit"],
        "gross_dues": admin_fin["gross_profit"],
        "total_disbursed": admin_fin["disbursed_amount"],
        "available_balance": admin_fin["available_balance"]
    })

    # 2. جميع ملاك الشبكات والشبكات المسجلة
    covered_owner_ids = set()
    networks = Network.query.order_by(Network.id.asc()).all()
    for net in networks:
        fin = compute_network_financials(net)
        owner_u = fin["owner_user"]
        if owner_u:
            covered_owner_ids.add(owner_u.id)
            label = f"مالك الشبكة: {owner_u.full_name} ({net.name})"
            b_name = f"مالك الشبكة: {owner_u.full_name} - {net.name}"
        else:
            label = f"مالك شبكة: {net.name}"
            b_name = f"مالك شبكة: {net.name}"

        beneficiaries.append({
            "key": f"network_{net.id}",
            "beneficiary_type": "network_owner",
            "label": label,
            "beneficiary_name": b_name,
            "user_id": owner_u.id if owner_u else None,
            "network_id": net.id,
            "network_name": net.name,
            "owner_name": owner_u.full_name if owner_u else net.name,
            "gross_earned": fin["gross_dues"],
            "gross_dues": fin["gross_dues"],
            "total_disbursed": fin["disbursed_amount"],
            "available_balance": fin["available_balance"]
        })

    # 3. ملاك الشبكات المستقلين غير المربوطين بشبكة بعد (إن وجدوا)
    standalone_owners = User.query.filter_by(role='network_owner').order_by(User.id.asc()).all()
    for u in standalone_owners:
        if u.id in covered_owner_ids:
            continue
        u_payouts = Payout.query.filter_by(beneficiary_key=f"user_{u.id}").all()
        u_disb = round(sum(float(p.amount or 0) for p in u_payouts), 2)
        u_avail = round(max(0.0, float(u.balance or 0)), 2)
        beneficiaries.append({
            "key": f"user_{u.id}",
            "beneficiary_type": "network_owner",
            "label": f"مالك الشبكة: {u.full_name} ({u.username})",
            "beneficiary_name": f"مالك الشبكة: {u.full_name}",
            "user_id": u.id,
            "network_id": u.network_id,
            "network_name": "",
            "owner_name": u.full_name,
            "gross_earned": round(u_avail + u_disb, 2),
            "gross_dues": round(u_avail + u_disb, 2),
            "total_disbursed": u_disb,
            "available_balance": u_avail
        })

    return beneficiaries

# ==================== خدمة التلجرام الموسعة مع اعتماد السندات ====================

def send_telegram_message(text_msg, reply_markup=None, chat_id=None):
    """إرسال رسالة نصية إلى بوت تلجرام"""
    target_chat = str(chat_id or CHAT_ID).strip()
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": target_chat, "text": text_msg, "parse_mode": "HTML"}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=12) as res:
            return json.loads(res.read().decode('utf-8'))
    except urllib.error.HTTPError as he:
        try:
            err_body = he.read().decode('utf-8', errors='replace')
            print(f"[Telegram HTTP Error {he.code}]: {err_body}")
        except Exception:
            print(f"[Telegram HTTP Error]: {he}")
        return None
    except Exception as e:
        print("[Telegram Message Error]:", e)
        return None

def send_telegram_deposit_card(deposit, user):
    """إرسال إشعار إيداع جديد إلى التلجرام مزوداً بأزرار اعتماد ورفض تفاعلية مباشرة"""
    caption = (
        f"💳 <b>طلب إيداع رصيد جديد (#{deposit.id})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>العميل:</b> {deposit.customer_name}\n"
        f"📱 <b>الهاتف:</b> {deposit.phone or (user.phone if user else '') or 'غير مسجل'}\n"
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

            req = urllib.request.Request(url, data=bytes(body), headers={
                'Content-Type': f'multipart/form-data; boundary={boundary}',
                'User-Agent': 'Mozilla/5.0'
            })
            with urllib.request.urlopen(req, timeout=18) as res:
                res_data = json.loads(res.read().decode('utf-8'))
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

def answer_telegram_callback(callback_query_id, text_msg, show_alert=False):
    """الرد الفوري على ضغطة الزر التفاعلي في تيليجرام"""
    if not callback_query_id:
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
        payload = {"callback_query_id": callback_query_id, "text": text_msg, "show_alert": show_alert}
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as res:
            res.read()
    except Exception as e:
        print("[Telegram Answer Callback Error]:", e)

def edit_telegram_message_text(chat_id, message_id, new_text):
    """تعديل نص أو وصف الرسالة في التلجرام وإخفاء الأزرار بعد الاعتماد أو الرفض"""
    if not message_id:
        return False
    target_chat = str(chat_id or CHAT_ID).strip()
    try:
        msg_id_int = int(message_id)
    except Exception:
        msg_id_int = message_id

    empty_kb = {"inline_keyboard": []}

    # 1. محاولة تعديل caption الصورة أولاً (لأن السندات المرفقة بصورة ترسل عبر sendPhoto)
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageCaption"
        payload = {
            "chat_id": target_chat,
            "message_id": msg_id_int,
            "caption": new_text,
            "parse_mode": "HTML",
            "reply_markup": empty_kb
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as res:
            resp_json = json.loads(res.read().decode('utf-8'))
            if resp_json.get('ok'):
                return True
    except Exception:
        pass

    # 2. محاولة تعديل النص في حال كانت الرسالة نصية بدون صورة
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
        payload = {
            "chat_id": target_chat,
            "message_id": msg_id_int,
            "text": new_text,
            "parse_mode": "HTML",
            "reply_markup": empty_kb
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as res:
            resp_json = json.loads(res.read().decode('utf-8'))
            if resp_json.get('ok'):
                return True
    except Exception as e:
        print("[Telegram Edit Message Error]:", e)

    return False

def process_telegram_callback_action(callback_data, callback_id, message_id, chat_id):
    """تنفيذ عملية اعتماد الإيداع أو رفضه برمجياً عند الضغط على زر التلجرام"""
    if not callback_data:
        return
    target_chat = str(chat_id or CHAT_ID).strip()

    with app.app_context():
        try:
            # دعم كلا الصيغتين approve_dep_<id> و approve_<id>
            is_approve = callback_data.startswith("approve_dep_") or callback_data.startswith("approve_")
            is_reject = callback_data.startswith("reject_dep_") or callback_data.startswith("reject_")

            if is_approve:
                raw_id = callback_data.replace("approve_dep_", "").replace("approve_", "").strip()
                dep_id = int(raw_id)
                dep = Deposit.query.get(dep_id)
                if not dep:
                    answer_telegram_callback(callback_id, "❌ لم يتم العثور على طلب الإيداع في قاعدة البيانات", show_alert=True)
                    return
                if dep.status != "pending":
                    status_ar = "معتمد مسبقاً ✅" if dep.status == "approved" else "مرفوض مسبقاً ❌"
                    answer_telegram_callback(callback_id, f"⚠️ هذا الطلب تمت معالجته ({status_ar})", show_alert=True)
                    return

                user = User.query.get(dep.user_id)
                if not user:
                    answer_telegram_callback(callback_id, "❌ لم يتم العثور على حساب العميل", show_alert=True)
                    return

                bonus = round(float(dep.amount) * 0.10, 2)
                total_added = round(float(dep.amount) + bonus, 2)
                user.balance = round(float(user.balance or 0) + total_added, 2)

                dep.status = 'approved'
                dep.approved_amount = float(dep.amount)
                dep.bonus_amount = bonus
                dep.reviewed_at = datetime.utcnow()
                db.session.commit()

                answer_telegram_callback(callback_id, f"✅ تم اعتماد الإيداع (#{dep.id})!\nتمت إضافة {total_added:,.2f} ر.ي لحساب {dep.customer_name}", show_alert=True)

                update_text = (
                    f"✅ <b>تم اعتماد طلب الإيداع (#{dep.id}) عبر تيليجرام</b>\n"
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
                edited = edit_telegram_message_text(target_chat, message_id, update_text)
                if not edited:
                    send_telegram_message(update_text, chat_id=target_chat)

            elif is_reject:
                raw_id = callback_data.replace("reject_dep_", "").replace("reject_", "").strip()
                dep_id = int(raw_id)
                dep = Deposit.query.get(dep_id)
                if not dep:
                    answer_telegram_callback(callback_id, "❌ لم يتم العثور على طلب الإيداع", show_alert=True)
                    return
                if dep.status != "pending":
                    status_ar = "معتمد مسبقاً ✅" if dep.status == "approved" else "مرفوض مسبقاً ❌"
                    answer_telegram_callback(callback_id, f"⚠️ هذا الطلب تمت معالجته ({status_ar})", show_alert=True)
                    return

                dep.status = 'rejected'
                dep.reviewed_at = datetime.utcnow()
                db.session.commit()

                answer_telegram_callback(callback_id, f"❌ تم رفض طلب الإيداع (#{dep.id})", show_alert=True)
                update_text = (
                    f"❌ <b>تم رفض طلب الإيداع (#{dep.id})</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>العميل:</b> {dep.customer_name}\n"
                    f"💵 <b>المبلغ:</b> {dep.amount:,.2f} ر.ي\n"
                    f"⏱️ <b>وقت الرفض:</b> {datetime.now().strftime('%Y-%m-%d %I:%M %p')}\n"
                    f"❌ <i>تم الرفض بواسطة الإدارة عبر تيليجرام</i>"
                )
                edited = edit_telegram_message_text(target_chat, message_id, update_text)
                if not edited:
                    send_telegram_message(update_text, chat_id=target_chat)

        except Exception as e:
            db.session.rollback()
            print("[Callback Action Error]:", e)
            answer_telegram_callback(callback_id, "⚠️ حدث خطأ أثناء تنفيذ العملية", show_alert=True)

_telegram_listener_started = False
_telegram_listener_lock = threading.Lock()

def start_telegram_listener():
    """خيط خلفي (Background Thread) لاستقبال أحداث تيليجرام وضغطات الأزرار التفاعلية"""
    global _telegram_listener_started
    with _telegram_listener_lock:
        if _telegram_listener_started:
            return
        _telegram_listener_started = True

    def worker():
        # إلغاء أي Webhook قديم عالق حتى يعمل getUpdates وأزرار الاعتماد بدون أي تعارض
        try:
            del_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=false"
            req = urllib.request.Request(del_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as res:
                res.read()
        except Exception as e:
            print("[Telegram deleteWebhook Info]:", e)

        offset = 0
        while True:
            try:
                allowed = urllib.parse.quote('["message","callback_query"]')
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=15&allowed_updates={allowed}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=22) as res:
                    data = json.loads(res.read().decode('utf-8'))
                    if data.get('ok') and data.get('result'):
                        for update in data['result']:
                            offset = max(offset, update['update_id'] + 1)
                            if 'callback_query' in update:
                                cq = update['callback_query']
                                cq_data = cq.get('data', '')
                                cq_id = cq.get('id')
                                msg = cq.get('message', {}) or {}
                                msg_id = msg.get('message_id')
                                c_id = (msg.get('chat') or {}).get('id') or CHAT_ID
                                process_telegram_callback_action(cq_data, cq_id, msg_id, c_id)
            except Exception as e:
                time.sleep(3)
            time.sleep(0.8)

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
        
        matches = re.findall(r'(?:PIN[:\s]*|كود[:\s]*)?([A-Za-z0-9]{6,16})', line_clean)
        for m in matches:
            if m.lower() not in ['serial', 'number', 'expire', 'duration', 'voucher', 'wifi']:
                extracted.append({"pin": m, "serial": None})

    return extracted

# ==================== التهيئة والترقية الآلية وتنظيف الشبكات التجريبية ====================

DEMO_NETWORK_CODES = {'zain_net', 'murais_net', 'fahd_net'}
DEMO_NETWORK_NAMES = {
    'زين نت (Zain Net)', 'مريس نت (Murais Net)', 'الفهد نت (Al-Fahd Net)',
    'زين نت', 'مريس نت', 'الفهد نت'
}

def auto_migrate_schema_and_cleanup():
    """إضافة الأعمدة الجديدة تلقائياً لقاعدة البيانات وحذف الشبكات التجريبية الافتراضية"""
    # 1. ترقية جدول card_packages بالأعمدة الجديدة (cost_price, has_free_card, free_card_duration)
    alter_statements = [
        "ALTER TABLE card_packages ADD COLUMN cost_price FLOAT DEFAULT 0.0",
        "ALTER TABLE card_packages ADD COLUMN has_free_card BOOLEAN DEFAULT FALSE",
        "ALTER TABLE card_packages ADD COLUMN free_card_duration VARCHAR(100)"
    ]
    for stmt in alter_statements:
        try:
            db.session.execute(text(stmt))
            db.session.commit()
        except Exception:
            db.session.rollback()

    # 2. حذف الشبكات الافتراضية التجريبية (زين نت، مريس نت، الفهد نت) إن وجدت
    try:
        demo_nets = Network.query.filter(
            (Network.code.in_(list(DEMO_NETWORK_CODES))) |
            (Network.name.in_(list(DEMO_NETWORK_NAMES)))
        ).all()
        if demo_nets:
            for dn in demo_nets:
                # فك ارتباط أي مستخدم بهذه الشبكة التجريبية
                User.query.filter_by(network_id=dn.id).update({"network_id": None})
                Order.query.filter_by(network_id=dn.id).delete()
                Card.query.filter_by(network_id=dn.id).delete()
                CardPackage.query.filter_by(network_id=dn.id).delete()
                db.session.delete(dn)
            db.session.commit()
            print(f"[DB Cleanup] Removed {len(demo_nets)} default demo networks.")
    except Exception as e:
        db.session.rollback()
        print("[DB Demo Cleanup Warning]:", e)

def seed_initial_database():
    """تهيئة قاعدة البيانات وإنشاء حساب المدير العام والبنوك فقط (بدون شبكات تجريبية)"""
    with app.app_context():
        db.create_all()
        auto_migrate_schema_and_cleanup()

        # التأكد من وجود حساب المدير العام (مشعل المريسي)
        admin = User.query.filter(
            (User.username == "مشعل المريسي") | (User.role == "admin")
        ).first()
        if not admin:
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
            print("[DB] Default Admin (مشعل المريسي) created.")

        # إنشاء الحسابات البنكية الافتراضية إذا كان جدول البنوك فارغاً
        if BankAccount.query.count() == 0:
            banks = [
                BankAccount(bank_name="بنك الكريمي للتمويل الأصغر الإسلامي", account_number="123456789", account_name="محفظة كروت شبكات الواي فاي", logo_icon="fa-landmark", instructions="يرجى كتابة اسمك ورقم هاتفك في خانة الغرض من التحويل"),
                BankAccount(bank_name="بنك القطيبي الإسلامي", account_number="987654321", account_name="محفظة شبكات اليمن", logo_icon="fa-building-columns", instructions="إيداع عبر تطبيق بنك القطيبي أو أي فرع"),
                BankAccount(bank_name="صرافة النجم للحوالات", account_number="777000111", account_name="إدارة المحفظة الإلكترونية", logo_icon="fa-money-bill-transfer", instructions="حوالة بالاسم والرقم الموضح وإرسال سند الاستلام"),
                BankAccount(bank_name="محفظة جيب / كاش / شامل موني", account_number="777223344", account_name="محفظة كروت النت", logo_icon="fa-mobile-screen", instructions="تحويل مباشر من محفظتك الإلكترونية")
            ]
            db.session.add_all(banks)
            db.session.commit()

# ==================== 1. مسارات المصادقة والمستخدمين (Auth API) ====================

@app.route('/api/auth/me', methods=['GET'])
def api_auth_me():
    user = get_current_user()
    if user:
        sid = get_session_id_from_request()
        res = make_response(jsonify(success=True, user=user.to_dict(), session_token=sid))
        if sid:
            create_user_session(res, user.id, existing_session_id=sid)
        return res
    return jsonify(success=False, user=None)

@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    data = request.get_json(force=True, silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify(success=False, message="يرجى إدخال اسم المستخدم وكلمة المرور"), 400

    user = User.query.filter(
        (User.username.ilike(username)) |
        (User.email.ilike(username)) |
        (User.phone == username)
    ).first()

    if not user or not user.check_password(password):
        return jsonify(success=False, message="اسم المستخدم أو كلمة المرور غير صحيحة"), 401

    session_id = str(uuid.uuid4())
    res = make_response(jsonify(
        success=True,
        message=f"أهلاً بك، {user.full_name}!",
        user=user.to_dict(),
        session_token=session_id
    ))
    create_user_session(res, user.id, existing_session_id=session_id)
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

    existing = User.query.filter(User.email.ilike(email)).first()
    if existing:
        return jsonify(success=False, message="هذا البريد الإلكتروني مسجل مسبقاً في النظام"), 400

    if username and User.query.filter(User.username.ilike(username)).first():
        return jsonify(success=False, message="اسم المستخدم موجود مسبقاً، يرجى اختيار اسم آخر"), 400

    otp_code = str(random.randint(100000, 999999))
    expires = time.time() + 600

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

    session_id = str(uuid.uuid4())
    res = make_response(jsonify(
        success=True,
        message="تم تفعيل الحساب وتسجيل الدخول بنجاح!",
        user=user.to_dict(),
        session_token=session_id
    ))
    create_user_session(res, user.id, existing_session_id=session_id)
    return res

# ==================== 2. المتجر وشراء الكروت بنظام القفل (Shop & Concurrency Locking) ====================

@app.route('/api/shop/networks', methods=['GET'])
def api_shop_networks():
    user = get_current_user()
    networks = Network.query.filter_by(is_active=True).order_by(Network.id.asc()).all()
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

    if not package_id:
        return jsonify(success=False, message="يرجى اختيار الباقة المراد شراؤها"), 400

    try:
        pkg = CardPackage.query.filter_by(id=package_id, is_active=True).first()
        if not pkg:
            return jsonify(success=False, message="الباقة المطلوبة غير متوفرة أو معطلة"), 404

        locked_user = User.query.filter_by(id=user.id).with_for_update().first()
        if float(locked_user.balance or 0) < float(pkg.price or 0):
            return jsonify(success=False, message=f"رصيدك الحالي ({float(locked_user.balance or 0):,.2f} ر.ي) غير كافٍ. سعر الباقة: {float(pkg.price or 0):,.2f} ر.ي"), 400

        locked_card = Card.query.filter_by(package_id=package_id, status='available').with_for_update().first()
        if not locked_card:
            return jsonify(success=False, message="عذراً، نفد مخزون كروت هذه الباقة حالياً. يرجى مراجعة إدارة الشبكة أو تجربة باقة أخرى"), 400

        locked_user.balance = round(float(locked_user.balance or 0) - float(pkg.price or 0), 2)
        locked_card.status = 'sold'
        locked_card.buyer_id = locked_user.id
        locked_card.purchased_at = datetime.utcnow()

        # تحديث رصيد مالك الشبكة المرتبط بمستحقاته من بيع الكرت تلقائياً
        raw_cost = getattr(pkg, 'cost_price', None)
        p_cost = round(float(raw_cost if raw_cost is not None and raw_cost > 0 else float(pkg.price or 0) * 0.85), 2)
        if locked_card.network:
            owner_u = get_network_owner_user(locked_card.network)
            if owner_u and owner_u.id != locked_user.id:
                owner_u.balance = round(float(owner_u.balance or 0) + p_cost, 2)

        order_num = f"ORD-{int(time.time())}-{random.randint(1000, 9999)}"
        order = Order(
            order_number=order_num,
            user_id=locked_user.id,
            card_id=locked_card.id,
            network_id=locked_card.network_id,
            package_id=pkg.id,
            amount=float(pkg.price or 0),
            status='completed',
            created_at=datetime.utcnow()
        )
        db.session.add(order)
        db.session.commit()

        has_free = bool(getattr(pkg, 'has_free_card', False))
        free_dur = (getattr(pkg, 'free_card_duration', '') or '')

        card_info = {
            "id": locked_card.id,
            "pin_code": locked_card.pin_code,
            "serial_number": locked_card.serial_number or f"SN-{locked_card.id}",
            "package_name": pkg.name,
            "duration": pkg.duration or "",
            "data_limit": pkg.data_limit or "",
            "has_free_card": has_free,
            "free_card_duration": free_dur,
            "network_name": locked_card.network.name if locked_card.network else "",
            "login_url": locked_card.network.login_url if locked_card.network else "",
            "price": round(float(pkg.price or 0), 2),
            "purchased_at": locked_card.purchased_at.isoformat()
        }
        new_balance = round(float(locked_user.balance or 0), 2)

        free_card_note = f"\n🎁 كرت مجاني على ظهر الكرت: {free_dur}" if has_free and free_dur else ""
        send_telegram_message(
            f"🛒 <b>عملية شراء كرت جديدة</b>:\n"
            f"▪️ العميل: {user.full_name}\n"
            f"▪️ الشبكة: {card_info['network_name']}\n"
            f"▪️ الباقة: {pkg.name}{free_card_note}\n"
            f"▪️ السعر: {pkg.price:,.2f} ر.ي\n"
            f"▪️ رصيد العميل المتبقي: {new_balance:,.2f} ر.ي"
        )

        return jsonify(
            success=True,
            message="تم شراء الكرت بنجاح!",
            card=card_info,
            new_balance=new_balance,
            remaining_balance=new_balance
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

    raw_events = []
    deposits = Deposit.query.filter_by(user_id=user.id, status='approved').all()
    for d in deposits:
        dt_str = d.reviewed_at.isoformat() if d.reviewed_at else (d.created_at.isoformat() if d.created_at else '')
        amt = float(d.approved_amount if d.approved_amount is not None else (d.amount or 0))
        raw_events.append({
            "id": f"dep-{d.id}",
            "type": "deposit",
            "title": f"إيداع رصيد ({d.bank_name})",
            "description": f"إيداع رصيد معتمد عبر {d.bank_name}",
            "amount": round(amt, 2),
            "created_at": dt_str,
            "status": "مكتمل"
        })
        if d.bonus_amount and float(d.bonus_amount) > 0:
            raw_events.append({
                "id": f"bon-{d.id}",
                "type": "bonus",
                "title": "بونص إيداع إضافي مجاني (10%)",
                "description": f"مكافأة بونص إضافي 10% على الإيداع #{d.id}",
                "amount": round(float(d.bonus_amount), 2),
                "created_at": dt_str,
                "status": "مكتمل"
            })

    orders = Order.query.filter_by(user_id=user.id).all()
    for o in orders:
        pkg = CardPackage.query.get(o.package_id)
        pkg_name = pkg.name if pkg else "باقة واي فاي"
        raw_events.append({
            "id": f"ord-{o.id}",
            "type": "purchase",
            "title": f"شراء كرت: {pkg_name}",
            "description": f"شراء كرت واي فاي ({pkg_name})",
            "amount": -round(float(o.amount or 0), 2),
            "created_at": o.created_at.isoformat() if o.created_at else '',
            "status": "مكتمل"
        })

    # إذا كان المستخدم مدير عام أو مالك شبكة، ندرج المستحقات والمسحوبات في كشف حركاته
    if user.role == 'admin':
        for c in Card.query.filter_by(status='sold').all():
            p_price, p_cost, p_profit = compute_card_sale_split(c)
            if p_profit > 0:
                raw_events.append({
                    "id": f"prof-{c.id}",
                    "type": "profit",
                    "title": "ربح بيع كرت بالمنصة",
                    "description": f"ربح المنصة من بيع كرت ({c.package.name if c.package else ''}) - {c.network.name if c.network else ''}",
                    "amount": p_profit,
                    "created_at": c.purchased_at.isoformat() if c.purchased_at else '',
                    "status": "مكتمل"
                })
        for p in Payout.query.filter_by(beneficiary_type='admin_profit').all():
            ref_part = f" | رقم الحوالة/المرجع: {p.reference_number}" if p.reference_number else ""
            note_part = f" | {p.notes}" if p.notes and p.notes != p.reference_number else ""
            raw_events.append({
                "id": f"pay-{p.id}",
                "type": "payout",
                "title": "صرف مستحقات / أرباح",
                "voucher_number": p.voucher_number,
                "description": f"صرف مستحقات / أرباح - سند رقم ({p.voucher_number}) عبر {p.transfer_method}{ref_part}{note_part}",
                "amount": -round(float(p.amount or 0), 2),
                "created_at": p.created_at.isoformat() if p.created_at else '',
                "status": "مكتمل"
            })
    elif user.role == 'network_owner':
        owner_net = get_owner_network(user)
        if owner_net:
            for c in Card.query.filter_by(network_id=owner_net.id, status='sold').all():
                p_price, p_cost, p_profit = compute_card_sale_split(c)
                raw_events.append({
                    "id": f"netdue-{c.id}",
                    "type": "network_due",
                    "title": "مستحقات بيع كرت شبكة",
                    "description": f"مستحقات بيع كرت ({c.package.name if c.package else ''}) رقم {c.pin_code}",
                    "amount": p_cost,
                    "created_at": c.purchased_at.isoformat() if c.purchased_at else '',
                    "status": "مكتمل"
                })
            net_payouts = Payout.query.filter(
                (Payout.network_id == owner_net.id) | (Payout.user_id == user.id)
            ).all()
        else:
            net_payouts = Payout.query.filter_by(user_id=user.id).all()

        for p in net_payouts:
            ref_part = f" | رقم الحوالة/المرجع: {p.reference_number}" if p.reference_number else ""
            note_part = f" | {p.notes}" if p.notes and p.notes != p.reference_number else ""
            raw_events.append({
                "id": f"pay-{p.id}",
                "type": "payout",
                "title": "صرف مستحقات / أرباح",
                "voucher_number": p.voucher_number,
                "description": f"صرف مستحقات / أرباح - سند رقم ({p.voucher_number}) عبر {p.transfer_method}{ref_part}{note_part}",
                "amount": -round(float(p.amount or 0), 2),
                "created_at": p.created_at.isoformat() if p.created_at else '',
                "status": "مكتمل"
            })

    # ترتيب زمني تصاعدي لحساب الرصيد التراكمي بدقة ثم عكس الترتيب للأحدث أولاً
    raw_events.sort(key=lambda x: x['created_at'])
    running_balance = 0.0
    for ev in raw_events:
        running_balance = round(running_balance + float(ev['amount']), 2)
        ev['balance_after'] = running_balance

    raw_events.reverse()
    return jsonify(success=True, transactions=raw_events)

# ==================== 4. لوحة تحكم مالك الشبكة المتقدمة (Network Owner & 3 Bulk Methods) ====================

def get_owner_network(user):
    """جلب الشبكة الخاصة بمالك الشبكة أو الشبكة الأولى للمدير"""
    if user.role == 'admin':
        net_id_arg = request.args.get('network_id') or (request.json and request.json.get('network_id') if request.is_json else None)
        if net_id_arg:
            return Network.query.get(int(net_id_arg))
        return Network.query.order_by(Network.id.asc()).first()
    return Network.query.filter((Network.owner_id == user.id) | (Network.id == user.network_id)).first()

@app.route('/api/network-owner/my-network', methods=['GET'])
def api_network_owner_my_network():
    """عزل بيانات مالك الشبكة برمجياً: يرى فقط شبكته وباقاته وإحصائياته"""
    user = get_current_user()
    if not user or user.role not in ['network_owner', 'admin']:
        return jsonify(success=False, message="صلاحية غير مصرح بها لمالك الشبكة"), 403

    net = get_owner_network(user)
    if not net:
        return jsonify(success=False, message="لم يتم ربط حسابك بشبكة محددة بعد"), 404

    packages = CardPackage.query.filter_by(network_id=net.id).order_by(CardPackage.id.asc()).all()
    cards = Card.query.filter_by(network_id=net.id).all()

    total_cards = len(cards)
    available_cards = sum(1 for c in cards if c.status == 'available')
    sold_cards = sum(1 for c in cards if c.status == 'sold')
    fin = compute_network_financials(net)

    return jsonify(
        success=True,
        network=net.to_dict(include_packages=False),
        packages=[p.to_dict(current_user=user) for p in packages],
        stats={
            "total_packages": len(packages),
            "total_cards": total_cards,
            "available_cards": available_cards,
            "available_cards_count": available_cards,
            "sold_cards": sold_cards,
            "sold_cards_count": sold_cards,
            "total_sales": fin["total_sales"],
            "total_revenue": fin["total_sales"],
            "total_owner_dues": fin["gross_dues"],
            "total_payouts_disbursed": fin["disbursed_amount"],
            "available_balance": fin["available_balance"]
        }
    )

@app.route('/api/network-owner/packages', methods=['POST'])
def api_network_owner_packages():
    user = get_current_user()
    if not user or user.role not in ['network_owner', 'admin']:
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    data = request.get_json(force=True, silent=True) or {}
    net = None
    if user.role == 'admin' and data.get('network_id'):
        net = Network.query.get(int(data['network_id']))
    if not net:
        net = get_owner_network(user)

    if not net:
        return jsonify(success=False, message="يرجى إضافة شبكة أولاً قبل إضافة الباقة"), 404

    name = data.get('name', '').strip()
    try:
        price = float(data.get('price', 0))
    except (TypeError, ValueError):
        price = 0.0
    try:
        cost_price = float(data.get('cost_price') or 0)
    except (TypeError, ValueError):
        cost_price = 0.0
    if cost_price <= 0 and price > 0:
        cost_price = round(price * 0.85, 2)

    duration = data.get('duration', '').strip()
    data_limit = data.get('data_limit', '').strip()
    description = data.get('description', '').strip()
    has_free_card = bool(data.get('has_free_card', False))
    free_card_duration = (data.get('free_card_duration') or '').strip() if has_free_card else ''

    if not name or price <= 0:
        return jsonify(success=False, message="يرجى كتابة اسم الباقة وتحديد سعر صحيح"), 400

    pkg = CardPackage(
        network_id=net.id,
        name=name,
        price=price,
        cost_price=cost_price,
        duration=duration,
        data_limit=data_limit,
        description=description,
        has_free_card=has_free_card,
        free_card_duration=free_card_duration,
        is_active=True
    )
    db.session.add(pkg)
    db.session.commit()

    return jsonify(success=True, message="تمت إضافة الباقة بنجاح!", package=pkg.to_dict(current_user=user))

@app.route('/api/network-owner/packages/<int:p_id>', methods=['PUT', 'POST', 'DELETE'])
def api_network_owner_package_modify(p_id):
    return api_admin_package_modify(p_id)

@app.route('/api/network-owner/cards', methods=['GET'])
def api_network_owner_cards():
    user = get_current_user()
    if not user or user.role not in ['network_owner', 'admin']:
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    net = get_owner_network(user)
    if not net:
        return jsonify(success=False, message="الشبكة غير موجودة"), 404

    query = Card.query.filter_by(network_id=net.id)
    status_filter = request.args.get('status', 'all')
    if status_filter in ['available', 'sold']:
        query = query.filter_by(status=status_filter)

    pkg_filter = request.args.get('package_id', 'all')
    if pkg_filter and pkg_filter != 'all':
        try:
            query = query.filter_by(package_id=int(pkg_filter))
        except ValueError:
            pass

    cards = query.order_by(Card.created_at.desc()).limit(400).all()
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

    package_id = request.form.get('package_id') or (request.json and request.json.get('package_id') if request.is_json else None)
    if not package_id:
        return jsonify(success=False, message="يرجى تحديد الباقة التابع لها الكروت"), 400

    pkg = CardPackage.query.get(int(package_id))
    if not pkg:
        return jsonify(success=False, message="الباقة المحددة غير موجودة"), 404

    if user.role != 'admin' and user.network_id != pkg.network_id and (not pkg.network or pkg.network.owner_id != user.id):
        return jsonify(success=False, message="لا تملك صلاحية إضافة كروت لهذه الشبكة"), 403

    upload_method = request.form.get('method') or (request.json and request.json.get('method') if request.is_json else None) or 'paste'
    extracted_cards = []

    if upload_method == 'excel' or 'excel_file' in request.files:
        file = request.files.get('excel_file') or request.files.get('file')
        if not file or not file.filename:
            return jsonify(success=False, message="يرجى اختيار ملف Excel بصيغة .xlsx"), 400
        try:
            extracted_cards = parse_cards_from_excel(file.read())
        except Exception as e:
            return jsonify(success=False, message=f"فشل قراءة ملف Excel: {str(e)}"), 400

    elif upload_method == 'pdf' or 'pdf_file' in request.files:
        file = request.files.get('pdf_file') or request.files.get('file')
        if not file or not file.filename:
            return jsonify(success=False, message="يرجى اختيار ملف PDF"), 400
        try:
            extracted_cards = parse_cards_from_pdf(file.read())
        except Exception as e:
            return jsonify(success=False, message=f"فشل قراءة واستخراج الكروت من ملف PDF: {str(e)}"), 400

    elif upload_method == 'paste':
        raw_text = request.form.get('pins_text') or (request.json and request.json.get('pins_text') if request.is_json else '') or ''
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        for line in lines:
            parts = [p.strip() for p in re.split(r'[,|\t\s]+', line) if p.strip()]
            if parts:
                pin = parts[0]
                serial = parts[1] if len(parts) > 1 else None
                extracted_cards.append({"pin": pin, "serial": serial})

    elif upload_method == 'auto':
        count = int(request.form.get('count') or (request.json and request.json.get('count') if request.is_json else 10) or 10)
        count = min(max(count, 1), 200)
        for i in range(count):
            extracted_cards.append({
                "pin": str(random.randint(10000000, 99999999)),
                "serial": f"SN-{pkg.id}-{int(time.time()) % 100000}{i}"
            })

    if not extracted_cards:
        return jsonify(success=False, message="لم يتم العثور على أي أكواد كروت صحيحة في الإدخال"), 400

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

    if added_cards:
        db.session.add_all(added_cards)
        db.session.commit()

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

    if user.role != 'admin' and (not card.network or card.network.owner_id != user.id) and user.network_id != card.network_id:
        return jsonify(success=False, message="لا تملك صلاحية حذف هذا الكرت"), 403

    if card.status == 'sold':
        return jsonify(success=False, message="لا يمكن حذف كرت تم بيعه للعميل مسبقاً"), 400

    db.session.delete(card)
    db.session.commit()
    return jsonify(success=True, message="تم حذف الكرت من المخزون بنجاح")

def build_network_statement_response(net, dt_from=None, dt_to=None):
    """بناء كشف الحساب التفصيلي للشبكة شاملاً الكروت المباعة + دفتر أستاذ المستحقات والمسحوبات المسلمة"""
    query = Card.query.filter_by(network_id=net.id, status='sold')
    if dt_from:
        query = query.filter(Card.purchased_at >= dt_from)
    if dt_to:
        query = query.filter(Card.purchased_at <= dt_to)

    sold_cards = query.order_by(Card.purchased_at.desc()).all()
    items = []
    total_sales = 0.0
    total_owner_dues = 0.0
    ledger_events = []

    for c in sold_cards:
        p_price, p_cost, p_profit = compute_card_sale_split(c)
        total_sales += p_price
        total_owner_dues += p_cost
        buyer = User.query.get(c.buyer_id) if c.buyer_id else None
        dt_iso = c.purchased_at.isoformat() if c.purchased_at else ''
        items.append({
            "card_id": c.id,
            "package_name": c.package.name if c.package else '',
            "price": p_price,
            "owner_due": p_cost,
            "pin_code": c.pin_code,
            "serial_number": c.serial_number or f"SN-{c.id}",
            "buyer_name": buyer.full_name if buyer else 'عميل',
            "purchased_at": dt_iso
        })
        ledger_events.append({
            "type": "sale_due",
            "transaction_type": "sale_due",
            "category": "مستحقات مبيعات كروت",
            "category_label": "مستحقات مبيعات كروت",
            "voucher_number": f"ORD-{c.id}",
            "description": f"مستحقات بيع كرت ({c.package.name if c.package else ''}) - PIN: {c.pin_code}",
            "transfer_method": "مبيعات المنصة",
            "amount": p_cost,
            "created_at": dt_iso
        })

    payout_q = Payout.query.filter(
        (Payout.network_id == net.id) |
        (Payout.beneficiary_key == f"network_{net.id}")
    )
    if dt_from:
        payout_q = payout_q.filter(Payout.created_at >= dt_from)
    if dt_to:
        payout_q = payout_q.filter(Payout.created_at <= dt_to)
    net_payouts = payout_q.order_by(Payout.created_at.desc()).all()

    for p in net_payouts:
        ref_part = f" | رقم الحوالة/المرجع: {p.reference_number}" if p.reference_number else ""
        note_part = f" | {p.notes}" if p.notes and p.notes != p.reference_number else ""
        ledger_events.append({
            "type": "payout_settlement",
            "transaction_type": "payout_settlement",
            "category": "صرف مستحقات / أرباح",
            "category_label": "صرف مستحقات / أرباح",
            "voucher_number": p.voucher_number,
            "description": f"صرف مستحقات / أرباح - سند رقم ({p.voucher_number}) عبر {p.transfer_method}{ref_part}{note_part}",
            "transfer_method": p.transfer_method,
            "reference_number": p.reference_number or "",
            "notes": p.notes or "",
            "receipt_image": p.receipt_img or "",
            "amount": -round(float(p.amount or 0), 2),
            "created_at": p.created_at.isoformat() if p.created_at else ''
        })

    ledger_events.sort(key=lambda x: x['created_at'])
    running_bal = 0.0
    for ev in ledger_events:
        running_bal = round(running_bal + float(ev['amount']), 2)
        ev['balance_after'] = running_bal
    ledger_events.reverse()

    fin = compute_network_financials(net)
    filtered_disbursed = round(sum(float(p.amount or 0) for p in net_payouts), 2)
    final_owner_dues = round(total_owner_dues, 2) if (dt_from or dt_to) else fin["gross_dues"]
    final_disbursed = filtered_disbursed if (dt_from or dt_to) else fin["disbursed_amount"]

    summary_obj = {
        "total_sales": round(total_sales, 2),
        "owner_gross_dues": final_owner_dues,
        "total_owner_dues": final_owner_dues,
        "total_disbursed": final_disbursed,
        "total_payouts_disbursed": final_disbursed,
        "available_balance": fin["available_balance"],
        "sold_count": len(items)
    }

    return jsonify(
        success=True,
        network=net.to_dict(include_packages=False),
        summary=summary_obj,
        total_sales=round(total_sales, 2),
        total_owner_dues=final_owner_dues,
        total_payouts_disbursed=final_disbursed,
        available_balance=fin["available_balance"],
        sold_count=len(items),
        items=items,
        payouts=[p.to_dict() for p in net_payouts],
        transactions=ledger_events,
        ledger=ledger_events
    )

@app.route('/api/network-owner/statement', methods=['GET'])
def api_network_owner_statement():
    user = get_current_user()
    if not user or user.role not in ['network_owner', 'admin']:
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    net = get_owner_network(user)
    if not net:
        return jsonify(success=False, message="الشبكة غير موجودة"), 404

    dt_from = parse_iso_date(request.args.get('date_from'))
    dt_to = parse_iso_date(request.args.get('date_to'), is_end=True)
    return build_network_statement_response(net, dt_from, dt_to)

# ==================== 5. لوحة تحكم المدير العام (Super Admin API) ====================

@app.route('/api/admin/test-telegram', methods=['POST', 'GET'])
def api_admin_test_telegram():
    """فحص اتصال بوت التلجرام وإرسال رسالة اختبار فورية"""
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    # التأكد من أن مستمع التلجرام يعمل وإلغاء أي Webhook قديم
    try:
        del_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=false"
        req = urllib.request.Request(del_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as res:
            res.read()
    except Exception:
        pass

    test_msg = (
        f"✅ <b>فحص اتصال بوت التلجرام بنجاح!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👑 <b>المدير العام:</b> {user.full_name}\n"
        f"🤖 <b>البوت:</b> @Croati_Bot\n"
        f"⏱️ <b>التوقيت:</b> {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}\n"
        f"🟢 <i>إشعارات السندات وأزرار الاعتماد الفوري تعمل بكفاءة 100%</i>"
    )
    res = send_telegram_message(test_msg)
    if res and res.get('ok'):
        return jsonify(success=True, message="تم الاتصال ببوت التلجرام وإرسال رسالة الفحص بنجاح! ✅")
    return jsonify(success=False, message="تعذر إرسال رسالة الفحص إلى التلجرام، يرجى التأكد من تفعيل البوت (@Croati_Bot)"), 400

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

    total_sales = round(sum(float(c.package.price or 0) for c in Card.query.filter_by(status='sold').all() if c.package), 2)
    pending_deposits = Deposit.query.filter_by(status='pending').count()
    approved_deposits = round(sum(float(d.approved_amount if d.approved_amount is not None else (d.amount or 0)) for d in Deposit.query.filter_by(status='approved').all()), 2)
    total_payouts_disbursed = round(sum(float(p.amount or 0) for p in Payout.query.all()), 2)
    admin_fin = compute_admin_profit_financials()

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
            "total_sales_amount": total_sales,
            "pending_deposits": pending_deposits,
            "pending_deposits_count": pending_deposits,
            "approved_deposits": approved_deposits,
            "total_deposits_approved_amount": approved_deposits,
            "estimated_profit": admin_fin["gross_profit"],
            "available_admin_profit": admin_fin["available_balance"],
            "total_payouts_disbursed": total_payouts_disbursed
        }
    )

@app.route('/api/admin/network-analytics', methods=['GET'])
def api_admin_network_analytics():
    """تقارير الشبكات المستقلة للمدير العام (حل مشكلة تعذر جلب تقارير الشبكات)"""
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    networks = Network.query.order_by(Network.id.asc()).all()
    analytics = []

    for net in networks:
        pkgs_count = CardPackage.query.filter_by(network_id=net.id).count()
        avail_count = Card.query.filter_by(network_id=net.id, status='available').count()
        sold_cards_list = Card.query.filter_by(network_id=net.id, status='sold').all()
        sold_count = len(sold_cards_list)
        fin = compute_network_financials(net)

        analytics.append({
            "id": net.id,
            "name": net.name,
            "code": net.code,
            "logo_icon": net.logo_icon or 'fa-wifi',
            "is_active": net.is_active,
            "total_packages": pkgs_count,
            "available_cards": avail_count,
            "sold_cards": sold_count,
            "total_revenue": fin["total_sales"],
            "owner_dues": fin["gross_dues"],
            "disbursed_amount": fin["disbursed_amount"],
            "available_balance": fin["available_balance"]
        })

    return jsonify(success=True, analytics=analytics)

@app.route('/api/admin/profit-analytics', methods=['GET'])
def api_admin_profit_analytics():
    """محرك احتساب الأرباح والمبيعات ومستحقات الشبكات وإجمالي المسحوبات والتسويات المسلمة"""
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    dt_from = parse_iso_date(request.args.get('date_from'))
    dt_to = parse_iso_date(request.args.get('date_to'), is_end=True)

    networks = Network.query.order_by(Network.id.asc()).all()
    net_reports = []

    grand_sales = 0.0
    grand_owner_payouts = 0.0
    grand_net_profit = 0.0
    grand_sold_cards = 0
    grand_avail_cards = 0
    grand_owners_disbursed = 0.0
    grand_owners_available = 0.0

    for net in networks:
        sold_query = Card.query.filter_by(network_id=net.id, status='sold')
        if dt_from:
            sold_query = sold_query.filter(Card.purchased_at >= dt_from)
        if dt_to:
            sold_query = sold_query.filter(Card.purchased_at <= dt_to)

        sold_cards_list = sold_query.all()
        avail_count = Card.query.filter_by(network_id=net.id, status='available').count()
        pkgs_count = len(net.packages)

        net_sales = 0.0
        net_cost = 0.0
        for c in sold_cards_list:
            p_price, p_cost, p_profit = compute_card_sale_split(c)
            net_sales += p_price
            net_cost += p_cost

        net_sales = round(net_sales, 2)
        net_cost = round(net_cost, 2)
        net_profit = round(net_sales - net_cost, 2)
        margin_pct = round((net_profit / net_sales) * 100, 1) if net_sales > 0 else 15.0

        fin = compute_network_financials(net)
        owner_u = fin["owner_user"]

        grand_sales += net_sales
        grand_owner_payouts += fin["gross_dues"] if not (dt_from or dt_to) else net_cost
        grand_net_profit += net_profit
        grand_sold_cards += len(sold_cards_list)
        grand_avail_cards += avail_count
        grand_owners_disbursed += fin["disbursed_amount"]
        grand_owners_available += fin["available_balance"]

        net_reports.append({
            "id": net.id,
            "name": net.name,
            "code": net.code,
            "owner_name": owner_u.full_name if owner_u else net.name,
            "beneficiary_key": f"network_{net.id}",
            "logo_icon": net.logo_icon or 'fa-wifi',
            "total_packages": pkgs_count,
            "packages_count": pkgs_count,
            "sold_cards": len(sold_cards_list),
            "sold_cards_count": len(sold_cards_list),
            "available_cards": avail_count,
            "available_cards_count": avail_count,
            "total_sales": net_sales,
            "owner_payout": fin["gross_dues"] if not (dt_from or dt_to) else net_cost,
            "owner_net_profit": fin["gross_dues"] if not (dt_from or dt_to) else net_cost,
            "disbursed_amount": fin["disbursed_amount"],
            "available_payout": fin["available_balance"],
            "net_profit": net_profit,
            "system_commission": net_profit,
            "profit_margin_percent": margin_pct
        })

    # احتساب المسحوبات المسلمة للمدير العام وإجمالي المسحوبات والتسويات المسلمة في النظام
    payout_query = Payout.query
    if dt_from:
        payout_query = payout_query.filter(Payout.created_at >= dt_from)
    if dt_to:
        payout_query = payout_query.filter(Payout.created_at <= dt_to)
    filtered_payouts = payout_query.all()

    total_disbursed_all = round(sum(float(p.amount or 0) for p in filtered_payouts), 2)
    admin_disbursed = round(sum(float(p.amount or 0) for p in filtered_payouts if p.beneficiary_type == 'admin_profit'), 2)
    owners_disbursed = round(sum(float(p.amount or 0) for p in filtered_payouts if p.beneficiary_type == 'network_owner'), 2)

    admin_fin = compute_admin_profit_financials()
    grand_sales = round(grand_sales, 2)
    grand_owner_payouts = round(grand_owner_payouts, 2)
    grand_net_profit = round(grand_net_profit, 2)
    overall_margin = round((grand_net_profit / grand_sales) * 100, 1) if grand_sales > 0 else 0.0

    summary = {
        "total_sales_revenue": grand_sales,
        "total_platform_sales": grand_sales,
        "total_owner_payouts": grand_owner_payouts,
        "total_network_payouts": grand_owner_payouts,
        "available_owner_payouts": round(grand_owners_available, 2),
        "total_net_profit": grand_net_profit,
        "total_admin_profit": grand_net_profit,
        "available_admin_profit": admin_fin["available_balance"],
        "total_disbursed_payouts": total_disbursed_all,
        "total_payouts_disbursed": total_disbursed_all,
        "admin_disbursed_payouts": admin_disbursed,
        "owners_disbursed_payouts": owners_disbursed,
        "overall_profit_margin_percent": overall_margin,
        "total_sold_cards": grand_sold_cards,
        "total_cards_sold": grand_sold_cards,
        "total_available_cards": grand_avail_cards
    }

    return jsonify(
        success=True,
        summary=summary,
        totals=summary,
        grand_total_sales=grand_sales,
        grand_sold_cards=grand_sold_cards,
        grand_system_profit=grand_net_profit,
        total_disbursed_payouts=total_disbursed_all,
        networks=net_reports,
        beneficiaries=get_all_payout_beneficiaries()
    )

@app.route('/api/admin/deposits', methods=['GET'])
def api_admin_deposits():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    status_filter = request.args.get('status', 'pending')
    dt_from = parse_iso_date(request.args.get('date_from'))
    dt_to = parse_iso_date(request.args.get('date_to'), is_end=True)

    query = Deposit.query
    if status_filter and status_filter != 'all':
        query = query.filter_by(status=status_filter)
    if dt_from:
        query = query.filter(Deposit.created_at >= dt_from)
    if dt_to:
        query = query.filter(Deposit.created_at <= dt_to)

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

    target_user.balance = round(float(target_user.balance or 0) + total_added, 2)
    dep.status = 'approved'
    dep.approved_amount = actual_amount
    dep.bonus_amount = bonus
    dep.reviewed_at = datetime.utcnow()
    db.session.commit()

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

    data = request.get_json(force=True, silent=True) or {}
    full_name = data.get('full_name', '').strip()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    email = data.get('email', '').strip().lower() or None
    phone = data.get('phone', '').strip()
    role = data.get('role', 'client')
    if role == 'customer':
        role = 'client'
    network_id = data.get('network_id') or None
    if network_id:
        try:
            network_id = int(network_id)
        except ValueError:
            network_id = None

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
        balance=float(data.get('balance', 0) or 0),
        is_verified=True
    )
    new_u.set_password(password)
    db.session.add(new_u)
    db.session.commit()

    # إذا كان مالك شبكة وربط بشبكة نحدث owner_id للشبكة أيضاً
    if role == 'network_owner' and network_id:
        net = Network.query.get(network_id)
        if net:
            net.owner_id = new_u.id
            db.session.commit()

    return jsonify(success=True, message="تم إنشاء الحساب بنجاح!", user=new_u.to_dict())

@app.route('/api/admin/users/<int:u_id>', methods=['PUT', 'POST', 'DELETE'])
@app.route('/api/admin/users/<int:u_id>/edit', methods=['PUT', 'POST'])
@app.route('/api/admin/users/<int:u_id>/delete', methods=['POST', 'DELETE'])
def api_admin_user_modify(u_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    target = User.query.get(u_id)
    if not target:
        return jsonify(success=False, message="المستخدم غير موجود"), 404

    if request.method == 'DELETE' or request.path.endswith('/delete'):
        if target.id == user.id or target.username == 'مشعل المريسي':
            return jsonify(success=False, message="لا يمكن حذف حساب المدير العام الأساسي"), 400

        # فك ارتباط الشبكات والكروت والطلبات قبل حذف الحساب
        Network.query.filter_by(owner_id=target.id).update({"owner_id": None})
        Card.query.filter_by(buyer_id=target.id).update({"buyer_id": None})
        Payout.query.filter_by(user_id=target.id).update({"user_id": None})
        SessionToken.query.filter_by(user_id=target.id).delete()
        Order.query.filter_by(user_id=target.id).delete()
        Deposit.query.filter_by(user_id=target.id).delete()
        db.session.delete(target)
        db.session.commit()
        return jsonify(success=True, message=f"تم حذف حساب ({target.full_name}) نهائياً بنجاح")

    data = request.get_json(force=True, silent=True) or {}
    new_username = data.get('username', '').strip()
    new_email = data.get('email', '').strip().lower() or None

    if new_username and new_username.lower() != target.username.lower():
        if User.query.filter(User.username.ilike(new_username)).first():
            return jsonify(success=False, message="اسم المستخدم الجديد مسجل مسبقاً"), 400
        target.username = new_username

    if new_email and new_email != (target.email or '').lower():
        if User.query.filter(User.email.ilike(new_email)).first():
            return jsonify(success=False, message="هذا البريد الإلكتروني مسجل مسبقاً"), 400
        target.email = new_email
    elif 'email' in data and not new_email:
        target.email = None

    if 'full_name' in data and data['full_name'].strip():
        target.full_name = data['full_name'].strip()
    if 'phone' in data:
        target.phone = data['phone'].strip()
    if 'role' in data:
        r_val = data['role']
        target.role = 'client' if r_val == 'customer' else r_val
    if 'network_id' in data:
        nid = data['network_id']
        target.network_id = int(nid) if nid else None
        if target.role == 'network_owner' and target.network_id:
            net = Network.query.get(target.network_id)
            if net:
                net.owner_id = target.id
    if 'balance' in data and data['balance'] is not None:
        target.balance = float(data['balance'])
    if 'password' in data and data['password'] and data['password'].strip():
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
    action = data.get('action', 'add') # 'add', 'subtract', 'deduct', 'set'
    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        amount = -1

    if amount < 0 or (action != 'set' and amount <= 0):
        return jsonify(success=False, message="يرجى كتابة مبلغ صحيح"), 400

    if action == 'add':
        target.balance = round(float(target.balance or 0) + amount, 2)
    elif action in ['deduct', 'subtract']:
        target.balance = round(max(0.0, float(target.balance or 0) - amount), 2)
    elif action == 'set':
        target.balance = round(amount, 2)
    else:
        return jsonify(success=False, message="نوع العملية غير صحيح"), 400

    db.session.commit()
    return jsonify(success=True, message=f"تم تعديل الرصيد بنجاح! الرصيد الجديد: {target.balance:,.2f} ر.ي", user=target.to_dict())

@app.route('/api/admin/users/<int:u_id>/statement', methods=['GET'])
def api_admin_user_statement(u_id):
    """كشف حساب مفصل للمستخدم / المستفيد (العميل، مالك الشبكة، أو المدير العام)"""
    user = get_current_user()
    if not user or (user.role != 'admin' and user.id != u_id):
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    target = User.query.get(u_id)
    if not target:
        return jsonify(success=False, message="المستخدم غير موجود"), 404

    dt_from = parse_iso_date(request.args.get('date_from'))
    dt_to = parse_iso_date(request.args.get('date_to'), is_end=True)

    dep_query = Deposit.query.filter_by(user_id=target.id, status='approved')
    card_query = Card.query.filter_by(buyer_id=target.id, status='sold')
    order_query = Order.query.filter_by(user_id=target.id)

    if dt_from:
        dep_query = dep_query.filter(Deposit.created_at >= dt_from)
        card_query = card_query.filter(Card.purchased_at >= dt_from)
        order_query = order_query.filter(Order.created_at >= dt_from)
    if dt_to:
        dep_query = dep_query.filter(Deposit.created_at <= dt_to)
        card_query = card_query.filter(Card.purchased_at <= dt_to)
        order_query = order_query.filter(Order.created_at <= dt_to)

    approved_deps = dep_query.all()
    bought_cards = card_query.order_by(Card.purchased_at.desc()).all()
    orders = order_query.all()

    total_dep = round(sum(float(d.approved_amount if d.approved_amount is not None else (d.amount or 0)) for d in approved_deps), 2)
    total_bonus = round(sum(float(d.bonus_amount or 0) for d in approved_deps), 2)
    total_spent = round(sum(float(c.package.price or 0) for c in bought_cards if c.package), 2)

    # بناء دفتر الأستاذ (Transactions Ledger)
    events = []
    for d in approved_deps:
        dt_s = d.reviewed_at.isoformat() if d.reviewed_at else (d.created_at.isoformat() if d.created_at else '')
        amt = round(float(d.approved_amount if d.approved_amount is not None else (d.amount or 0)), 2)
        events.append({
            "type": "deposit",
            "category": "إيداع رصيد",
            "description": f"إيداع رصيد معتمد ({d.bank_name})",
            "amount": amt,
            "created_at": dt_s
        })
        if d.bonus_amount and float(d.bonus_amount) > 0:
            events.append({
                "type": "bonus",
                "category": "بونص مجاني 10%",
                "description": f"بونص إضافي مجاني 10% (إيداع #{d.id})",
                "amount": round(float(d.bonus_amount), 2),
                "created_at": dt_s
            })

    for o in orders:
        pkg = CardPackage.query.get(o.package_id)
        pkg_name = pkg.name if pkg else 'باقة واي فاي'
        events.append({
            "type": "purchase",
            "category": "شراء كرت",
            "description": f"شراء كرت: {pkg_name}",
            "amount": -round(float(o.amount or 0), 2),
            "created_at": o.created_at.isoformat() if o.created_at else ''
        })

    # إضافة القيود المحاسبية للأرباح ومستحقات الشبكات وسندات الصرف والتسوية
    total_payouts_disbursed = 0.0
    effective_balance = round(float(target.balance or 0), 2)

    if target.role == 'admin':
        sold_q = Card.query.filter_by(status='sold')
        if dt_from:
            sold_q = sold_q.filter(Card.purchased_at >= dt_from)
        if dt_to:
            sold_q = sold_q.filter(Card.purchased_at <= dt_to)
        for c in sold_q.all():
            p_price, p_cost, p_profit = compute_card_sale_split(c)
            if p_profit > 0:
                events.append({
                    "type": "profit",
                    "category": "أرباح المنصة",
                    "description": f"ربح المنصة من بيع كرت ({c.package.name if c.package else ''}) - {c.network.name if c.network else ''}",
                    "amount": p_profit,
                    "created_at": c.purchased_at.isoformat() if c.purchased_at else ''
                })

        pay_q = Payout.query.filter_by(beneficiary_type='admin_profit')
        if dt_from:
            pay_q = pay_q.filter(Payout.created_at >= dt_from)
        if dt_to:
            pay_q = pay_q.filter(Payout.created_at <= dt_to)
        user_payouts = pay_q.all()
        admin_fin = compute_admin_profit_financials()
        effective_balance = admin_fin["available_balance"]
    elif target.role == 'network_owner':
        owner_nets = Network.query.filter((Network.owner_id == target.id) | (Network.id == target.network_id)).all()
        net_ids = [n.id for n in owner_nets]
        if net_ids:
            sold_q = Card.query.filter(Card.network_id.in_(net_ids), Card.status == 'sold')
            if dt_from:
                sold_q = sold_q.filter(Card.purchased_at >= dt_from)
            if dt_to:
                sold_q = sold_q.filter(Card.purchased_at <= dt_to)
            for c in sold_q.all():
                p_price, p_cost, p_profit = compute_card_sale_split(c)
                events.append({
                    "type": "network_due",
                    "category": "مستحقات مبيعات كروت",
                    "description": f"مستحقات بيع كرت ({c.package.name if c.package else ''}) رقم {c.pin_code}",
                    "amount": p_cost,
                    "created_at": c.purchased_at.isoformat() if c.purchased_at else ''
                })
            pay_q = Payout.query.filter((Payout.network_id.in_(net_ids)) | (Payout.user_id == target.id))
            fin = compute_network_financials(owner_nets[0])
            effective_balance = fin["available_balance"]
        else:
            pay_q = Payout.query.filter_by(user_id=target.id)
        if dt_from:
            pay_q = pay_q.filter(Payout.created_at >= dt_from)
        if dt_to:
            pay_q = pay_q.filter(Payout.created_at <= dt_to)
        user_payouts = pay_q.all()
    else:
        pay_q = Payout.query.filter_by(user_id=target.id)
        if dt_from:
            pay_q = pay_q.filter(Payout.created_at >= dt_from)
        if dt_to:
            pay_q = pay_q.filter(Payout.created_at <= dt_to)
        user_payouts = pay_q.all()

    for p in user_payouts:
        p_amt = round(float(p.amount or 0), 2)
        total_payouts_disbursed += p_amt
        ref_part = f" | رقم الحوالة/المرجع: {p.reference_number}" if p.reference_number else ""
        note_part = f" | {p.notes}" if p.notes and p.notes != p.reference_number else ""
        events.append({
            "type": "payout",
            "category": "صرف مستحقات / أرباح",
            "voucher_number": p.voucher_number,
            "transfer_method": p.transfer_method,
            "description": f"صرف مستحقات / أرباح - سند رقم ({p.voucher_number}) عبر {p.transfer_method}{ref_part}{note_part}",
            "amount": -p_amt,
            "created_at": p.created_at.isoformat() if p.created_at else ''
        })

    events.sort(key=lambda x: x['created_at'])
    running_bal = 0.0
    for ev in events:
        running_bal = round(running_bal + float(ev['amount']), 2)
        ev['balance_after'] = running_bal
    events.reverse()

    u_dict = target.to_dict()
    if target.role in ['admin', 'network_owner']:
        u_dict['balance'] = effective_balance

    return jsonify(
        success=True,
        user=u_dict,
        summary={
            "current_balance": effective_balance,
            "total_deposits_approved": total_dep,
            "total_bonus_received": total_bonus,
            "cards_bought_count": len(bought_cards),
            "total_cards_spent": total_spent,
            "total_payouts_disbursed": round(total_payouts_disbursed, 2)
        },
        cards=[c.to_dict() for c in bought_cards],
        payouts=[p.to_dict() for p in user_payouts],
        transactions=events
    )

@app.route('/api/admin/networks', methods=['GET', 'POST'])
def api_admin_networks():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    if request.method == 'GET':
        nets = Network.query.order_by(Network.id.asc()).all()
        return jsonify(success=True, networks=[n.to_dict(include_packages=True, current_user=user, include_inactive_packages=True) for n in nets])

    data = request.get_json(force=True, silent=True) or {}
    name = data.get('name', '').strip()
    code = data.get('code', '').strip().lower() or re.sub(r'[^a-zA-Z0-9_]', '_', name.lower()) or f"net_{int(time.time())}"
    logo_icon = data.get('logo_icon') or 'fa-wifi'
    description = data.get('description', '').strip()
    login_url = data.get('login_url', '').strip()
    owner_id = data.get('owner_id') or None
    if owner_id:
        try:
            owner_id = int(owner_id)
        except ValueError:
            owner_id = None

    if not name:
        return jsonify(success=False, message="يرجى كتابة اسم الشبكة"), 400

    if Network.query.filter_by(code=code).first():
        code = f"{code}_{random.randint(100, 999)}"

    net = Network(name=name, code=code, logo_icon=logo_icon, description=description, login_url=login_url, owner_id=owner_id)
    db.session.add(net)
    db.session.commit()
    return jsonify(success=True, message="تمت إضافة الشبكة بنجاح!", network=net.to_dict(current_user=user, include_inactive_packages=True))

@app.route('/api/admin/networks/<int:n_id>', methods=['PUT', 'POST', 'DELETE'])
@app.route('/api/admin/networks/<int:n_id>/edit', methods=['PUT', 'POST'])
@app.route('/api/admin/networks/<int:n_id>/delete', methods=['POST', 'DELETE'])
def api_admin_network_modify(n_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    net = Network.query.get(n_id)
    if not net:
        return jsonify(success=False, message="الشبكة غير موجودة"), 404

    if request.method == 'DELETE' or request.path.endswith('/delete'):
        User.query.filter_by(network_id=net.id).update({"network_id": None})
        Payout.query.filter_by(network_id=net.id).update({"network_id": None})
        Order.query.filter_by(network_id=net.id).delete()
        Card.query.filter_by(network_id=net.id).delete()
        CardPackage.query.filter_by(network_id=net.id).delete()
        db.session.delete(net)
        db.session.commit()
        return jsonify(success=True, message=f"تم حذف شبكة ({net.name}) وكافة باقاتها بنجاح")

    data = request.get_json(force=True, silent=True) or {}
    if 'name' in data and data['name'].strip():
        net.name = data['name'].strip()
    if 'logo_icon' in data and data['logo_icon']:
        net.logo_icon = data['logo_icon']
    if 'description' in data:
        net.description = data['description']
    if 'login_url' in data:
        net.login_url = data['login_url']
    if 'owner_id' in data:
        net.owner_id = int(data['owner_id']) if data['owner_id'] else None
    if 'is_active' in data:
        val = data['is_active']
        net.is_active = bool(int(val)) if isinstance(val, (int, str)) and str(val).isdigit() else bool(val)

    db.session.commit()
    return jsonify(success=True, message="تم تحديث بيانات الشبكة بنجاح!", network=net.to_dict(current_user=user, include_inactive_packages=True))

@app.route('/api/admin/packages', methods=['POST'])
def api_admin_package_add():
    user = get_current_user()
    if not user or user.role not in ['admin', 'network_owner']:
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    data = request.get_json(force=True, silent=True) or {}
    net_id = data.get('network_id')
    if not net_id and user.role == 'network_owner':
        owner_net = get_owner_network(user)
        if owner_net:
            net_id = owner_net.id

    name = data.get('name', '').strip()
    try:
        price = float(data.get('price', 0))
    except (TypeError, ValueError):
        price = 0.0
    try:
        cost_price = float(data.get('cost_price') or 0)
    except (TypeError, ValueError):
        cost_price = 0.0
    if cost_price <= 0 and price > 0:
        cost_price = round(price * 0.85, 2)

    duration = data.get('duration', '').strip()
    data_limit = data.get('data_limit', '').strip()
    description = data.get('description', '').strip()
    has_free_card = bool(data.get('has_free_card', False))
    free_card_duration = (data.get('free_card_duration') or '').strip() if has_free_card else ''

    if not net_id or not name or price <= 0:
        return jsonify(success=False, message="يرجى تحديد الشبكة والاسم وسعر صحيح للباقة"), 400

    pkg = CardPackage(
        network_id=int(net_id),
        name=name,
        price=price,
        cost_price=cost_price,
        duration=duration,
        data_limit=data_limit,
        description=description,
        has_free_card=has_free_card,
        free_card_duration=free_card_duration,
        is_active=True
    )
    db.session.add(pkg)
    db.session.commit()
    return jsonify(success=True, message="تمت إضافة الباقة بنجاح!", package=pkg.to_dict(current_user=user))

@app.route('/api/admin/packages/<int:p_id>', methods=['PUT', 'POST', 'DELETE'])
@app.route('/api/admin/packages/<int:p_id>/edit', methods=['PUT', 'POST'])
@app.route('/api/admin/packages/<int:p_id>/delete', methods=['POST', 'DELETE'])
def api_admin_package_modify(p_id):
    user = get_current_user()
    if not user or user.role not in ['admin', 'network_owner']:
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    pkg = CardPackage.query.get(p_id)
    if not pkg:
        return jsonify(success=False, message="الباقة غير موجودة"), 404

    if user.role == 'network_owner':
        owner_net = get_owner_network(user)
        if not owner_net or pkg.network_id != owner_net.id:
            return jsonify(success=False, message="لا تملك صلاحية تعديل أو حذف هذه الباقة"), 403

    if request.method == 'DELETE' or request.path.endswith('/delete'):
        Order.query.filter_by(package_id=pkg.id).delete()
        Card.query.filter_by(package_id=pkg.id).delete()
        db.session.delete(pkg)
        db.session.commit()
        return jsonify(success=True, message=f"تم حذف الباقة ({pkg.name}) بنجاح")

    data = request.get_json(force=True, silent=True) or {}
    if 'name' in data and data['name'].strip():
        pkg.name = data['name'].strip()
    if 'price' in data and float(data['price'] or 0) > 0:
        pkg.price = float(data['price'])
    if 'cost_price' in data and data['cost_price'] is not None and str(data['cost_price']) != '':
        pkg.cost_price = float(data['cost_price'])
    if 'duration' in data:
        pkg.duration = data['duration']
    if 'data_limit' in data:
        pkg.data_limit = data['data_limit']
    if 'description' in data:
        pkg.description = data['description']
    if 'has_free_card' in data:
        pkg.has_free_card = bool(data['has_free_card'])
    if 'free_card_duration' in data:
        pkg.free_card_duration = (data['free_card_duration'] or '').strip() if pkg.has_free_card else ''
    if 'is_active' in data:
        val = data['is_active']
        pkg.is_active = bool(int(val)) if isinstance(val, (int, str)) and str(val).isdigit() else bool(val)

    db.session.commit()
    return jsonify(success=True, message="تم تحديث بيانات الباقة بنجاح!", package=pkg.to_dict(current_user=user))

@app.route('/api/admin/cards-inventory', methods=['GET'])
def api_admin_cards_inventory():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    net_filter = request.args.get('network_id', 'all')
    pkg_filter = request.args.get('package_id', 'all')
    status_filter = request.args.get('status', 'all')
    dt_from = parse_iso_date(request.args.get('date_from'))
    dt_to = parse_iso_date(request.args.get('date_to'), is_end=True)

    query = Card.query
    if net_filter and net_filter != 'all':
        try:
            query = query.filter_by(network_id=int(net_filter))
        except ValueError:
            pass
    if pkg_filter and pkg_filter != 'all':
        try:
            query = query.filter_by(package_id=int(pkg_filter))
        except ValueError:
            pass
    if status_filter in ['available', 'sold']:
        query = query.filter_by(status=status_filter)
    if dt_from:
        query = query.filter((Card.purchased_at >= dt_from) | (Card.created_at >= dt_from))
    if dt_to:
        query = query.filter((Card.purchased_at <= dt_to) | (Card.created_at <= dt_to))

    all_matching = query.order_by(Card.created_at.desc()).limit(600).all()
    cards_dicts = [c.to_dict() for c in all_matching]

    total_c = len(cards_dicts)
    avail_c = sum(1 for c in cards_dicts if c['status'] == 'available')
    sold_c = sum(1 for c in cards_dicts if c['status'] == 'sold')
    sold_val = round(sum(float(c['price'] or 0) for c in cards_dicts if c['status'] == 'sold'), 2)
    profit_val = round(sum(float(c['profit'] or 0) for c in cards_dicts if c['status'] == 'sold'), 2)

    return jsonify(
        success=True,
        cards=cards_dicts,
        metrics={
            "total_cards": total_c,
            "available_cards": avail_c,
            "sold_cards": sold_c,
            "total_sold_value": sold_val,
            "total_profit_value": profit_val
        }
    )

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
    account_name = data.get('account_name', '').strip() or "محفظة كروت الواي فاي"
    instructions = data.get('instructions', '').strip()
    logo_icon = data.get('logo_icon', 'fa-landmark')

    if not bank_name or not account_number:
        return jsonify(success=False, message="يرجى كتابة اسم البنك ورقم الحساب"), 400

    bank = BankAccount(bank_name=bank_name, account_number=account_number, account_name=account_name, instructions=instructions, logo_icon=logo_icon)
    db.session.add(bank)
    db.session.commit()
    return jsonify(success=True, message="تمت إضافة الحساب البنكي بنجاح!", bank=bank.to_dict())

@app.route('/api/admin/banks/<int:b_id>', methods=['PUT', 'POST', 'DELETE'])
@app.route('/api/admin/banks/<int:b_id>/delete', methods=['POST', 'DELETE'])
def api_admin_bank_modify(b_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها"), 403

    bank = BankAccount.query.get(b_id)
    if not bank:
        return jsonify(success=False, message="الحساب البنكي غير موجود"), 404

    if request.method == 'DELETE' or request.path.endswith('/delete'):
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

    dt_from = parse_iso_date(request.args.get('date_from'))
    dt_to = parse_iso_date(request.args.get('date_to'), is_end=True)

    users = User.query.order_by(User.id.asc()).all()
    reports = []
    total_customers_balance = 0.0
    total_deposits_vol = 0.0
    total_bonus_vol = 0.0
    total_customers_purchases = 0.0
    total_cards_count = 0

    for u in users:
        dep_q = Deposit.query.filter_by(user_id=u.id, status='approved')
        card_q = Card.query.filter_by(buyer_id=u.id, status='sold')
        if dt_from:
            dep_q = dep_q.filter(Deposit.created_at >= dt_from)
            card_q = card_q.filter(Card.purchased_at >= dt_from)
        if dt_to:
            dep_q = dep_q.filter(Deposit.created_at <= dt_to)
            card_q = card_q.filter(Card.purchased_at <= dt_to)

        approved_deps = dep_q.all()
        bought_cards = card_q.all()

        u_dep = round(sum(float(d.approved_amount if d.approved_amount is not None else (d.amount or 0)) for d in approved_deps), 2)
        u_bon = round(sum(float(d.bonus_amount or 0) for d in approved_deps), 2)
        u_spent = round(sum(float(c.package.price or 0) for c in bought_cards if c.package), 2)
        u_bal = round(float(u.balance or 0), 2)

        total_customers_balance += u_bal
        total_deposits_vol += u_dep
        total_bonus_vol += u_bon
        total_customers_purchases += u_spent
        total_cards_count += len(bought_cards)

        norm_role = 'customer' if u.role in ['client', 'customer'] else u.role
        reports.append({
            "id": u.id,
            "full_name": u.full_name,
            "username": u.username,
            "phone": u.phone or '',
            "email": u.email or '',
            "role": norm_role,
            "balance": u_bal,
            "current_balance": u_bal,
            "total_deposits_approved_amount": u_dep,
            "deposits_approved": u_dep,
            "total_bonus_received": u_bon,
            "bonus_received": u_bon,
            "cards_bought_count": len(bought_cards),
            "purchased_cards_count": len(bought_cards),
            "total_cards_spent": u_spent,
            "total_spent": u_spent,
            "created_at": u.created_at.isoformat() if u.created_at else ''
        })

    summary = {
        "total_customers_count": len(reports),
        "total_customers": len(reports),
        "total_current_balances": round(total_customers_balance, 2),
        "total_deposits_volume": round(total_deposits_vol, 2),
        "total_deposits_approved": round(total_deposits_vol, 2),
        "total_bonus_volume": round(total_bonus_vol, 2),
        "total_bonus_given": round(total_bonus_vol, 2),
        "total_cards_sales_volume": round(total_customers_purchases, 2),
        "total_cards_spent": round(total_customers_purchases, 2),
        "total_cards_count": total_cards_count
    }

    return jsonify(
        success=True,
        summary=summary,
        totals=summary,
        total_customers_count=len(reports),
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

    dt_from = parse_iso_date(request.args.get('date_from'))
    dt_to = parse_iso_date(request.args.get('date_to'), is_end=True)

    return build_network_statement_response(net, dt_from, dt_to)

# ==================== 5.5 نظام صرف وتسوية المستحقات والأرباح الموحد (Unified Payouts) ====================

@app.route('/api/admin/payouts/beneficiaries', methods=['GET'])
def api_admin_payout_beneficiaries():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية غير مصرح بها — للمدير العام فقط"), 403

    beneficiaries = get_all_payout_beneficiaries()
    admin_fin = compute_admin_profit_financials()
    return jsonify(
        success=True,
        beneficiaries=beneficiaries,
        admin_financials=admin_fin
    )

@app.route('/api/admin/payouts', methods=['GET', 'POST'])
def api_admin_payouts():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify(success=False, message="صلاحية هذه العملية للمدير العام فقط (Admin Only)"), 403

    if request.method == 'GET':
        beneficiary_filter = (request.args.get('beneficiary_key') or '').strip()
        beneficiary_type = (request.args.get('beneficiary_type') or '').strip()
        dt_from = parse_iso_date(request.args.get('date_from'))
        dt_to = parse_iso_date(request.args.get('date_to'), is_end=True)

        q = Payout.query
        if beneficiary_filter and beneficiary_filter != 'all':
            q = q.filter(Payout.beneficiary_key == beneficiary_filter)
        if beneficiary_type and beneficiary_type != 'all':
            q = q.filter(Payout.beneficiary_type == beneficiary_type)
        if dt_from:
            q = q.filter(Payout.created_at >= dt_from)
        if dt_to:
            q = q.filter(Payout.created_at <= dt_to)

        payout_rows = q.order_by(Payout.created_at.desc()).all()
        payouts_list = [p.to_dict() for p in payout_rows]

        all_payout_rows = Payout.query.all()
        total_all_disbursed = round(sum(float(p.amount or 0) for p in all_payout_rows), 2)
        total_admin_disbursed = round(sum(float(p.amount or 0) for p in all_payout_rows if p.beneficiary_type == 'admin_profit'), 2)
        total_networks_disbursed = round(sum(float(p.amount or 0) for p in all_payout_rows if p.beneficiary_type != 'admin_profit'), 2)

        beneficiaries = get_all_payout_beneficiaries()

        summary_obj = {
            "total_disbursed_all": total_all_disbursed,
            "total_admin_profit_disbursed": total_admin_disbursed,
            "total_network_owners_disbursed": total_networks_disbursed,
            "filtered_count": len(payouts_list),
            "filtered_disbursed": round(sum(float(p.amount or 0) for p in payout_rows), 2)
        }

        return jsonify(
            success=True,
            payouts=payouts_list,
            summary=summary_obj,
            total_count=len(payouts_list),
            total_disbursed=total_all_disbursed,
            total_admin_disbursed=total_admin_disbursed,
            total_networks_disbursed=total_networks_disbursed,
            beneficiaries=beneficiaries
        )

    # POST: إنشاء واعتماد سند صرف مستحقات / أرباح جديد مع قفل المعاملة (with_for_update)
    if request.form:
        data = request.form.to_dict()
    else:
        data = request.get_json(force=True, silent=True) or {}

    beneficiary_key = (data.get('beneficiary_key') or '').strip()
    transfer_method = (data.get('transfer_method') or 'الكريمي').strip()
    reference_number = (data.get('reference_number') or '').strip()
    notes = (data.get('notes') or '').strip()

    try:
        amount = round(float(data.get('amount') or 0), 2)
    except Exception:
        return jsonify(success=False, message="قيمة المبلغ المراد صرفه غير صالحة"), 400

    if not beneficiary_key:
        return jsonify(success=False, message="يرجى اختيار المستفيد من القائمة"), 400

    if amount <= 0:
        return jsonify(success=False, message="يرجى إدخال مبلغ صحيح أكبر من الصفر"), 400

    allowed_methods = ['الكريمي', 'بنك الشرق', 'كاش', 'حوالة أخرى']
    if transfer_method not in allowed_methods:
        transfer_method = transfer_method or 'الكريمي'

    # معالجة صورة السند المرفقة (إن وجدت)
    receipt_img_value = (data.get('receipt_img') or data.get('receipt_base64') or '').strip()
    uploaded_file = request.files.get('receipt_image') or request.files.get('receipt') or request.files.get('receipt_img')
    if uploaded_file and uploaded_file.filename:
        ext = os.path.splitext(uploaded_file.filename)[1].lower()
        if ext not in ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf']:
            ext = '.jpg'
        file_bytes = uploaded_file.read()
        if file_bytes:
            fname = f"payout_{int(time.time() * 1000)}_{random.randint(100,999)}{ext}"
            fpath = os.path.join(UPLOADS_DIR, fname)
            try:
                with open(fpath, 'wb') as f_out:
                    f_out.write(file_bytes)
            except Exception as e:
                print("[Payout Receipt File Save Warning]:", e)
            mime_map = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.webp': 'image/webp',
                '.gif': 'image/gif',
                '.pdf': 'application/pdf'
            }
            mime_t = mime_map.get(ext, 'image/jpeg')
            b64_str = base64.b64encode(file_bytes).decode('utf-8')
            receipt_img_value = f"data:{mime_t};base64,{b64_str}"

    try:
        beneficiary_type = ''
        beneficiary_name = ''
        target_user_id = None
        target_network_id = None
        available_before = 0.0

        if beneficiary_key == 'admin_profit':
            # قفل صف المدير العام في قاعدة البيانات لمنع التعارض المتزامن (Concurrency Locking)
            locked_admin = User.query.filter_by(id=user.id).with_for_update().first()
            admin_fin = compute_admin_profit_financials()
            available_before = round(float(admin_fin['available_balance']), 2)
            beneficiary_type = 'admin_profit'
            beneficiary_name = 'حساب المدير العام (أرباح المنصة)'
            target_user_id = locked_admin.id if locked_admin else user.id
            target_network_id = None

            if amount > available_before:
                db.session.rollback()
                return jsonify(
                    success=False,
                    message=f"مرفوض: المبلغ المطلوب صرفه ({amount} ر.ي) أكبر من رصيد أرباح المنصة المتاح حالياً ({available_before} ر.ي)"
                ), 400

        elif beneficiary_key.startswith('network_') or beneficiary_key.startswith('network:'):
            sep = '_' if beneficiary_key.startswith('network_') else ':'
            try:
                net_id = int(beneficiary_key.split(sep, 1)[1])
            except Exception:
                return jsonify(success=False, message="معرف الشبكة غير صالح"), 400

            beneficiary_key = f"network_{net_id}"

            # قفل صف الشبكة وصف المالك في قاعدة البيانات أثناء التحقق والخصم
            locked_net = Network.query.filter_by(id=net_id).with_for_update().first()
            if not locked_net:
                db.session.rollback()
                return jsonify(success=False, message="الشبكة المحددة غير موجودة"), 404

            owner_user = get_network_owner_user(locked_net)
            locked_owner = None
            if owner_user:
                locked_owner = User.query.filter_by(id=owner_user.id).with_for_update().first()

            net_fin = compute_network_financials(locked_net)
            available_before = round(float(net_fin['available_balance']), 2)

            if amount > available_before:
                db.session.rollback()
                return jsonify(
                    success=False,
                    message=f"مرفوض: المبلغ المطلوب صرفه ({amount} ر.ي) أكبر من الرصيد المتاح للمستفيد حالياً ({available_before} ر.ي)"
                ), 400

            beneficiary_type = 'network_owner'
            owner_display = getattr(locked_net, 'owner_name', None) or (locked_owner.full_name if locked_owner else 'مالك شبكة')
            beneficiary_name = f"مالك الشبكة: {owner_display} ({locked_net.name})"
            target_network_id = locked_net.id
            target_user_id = locked_owner.id if locked_owner else locked_net.owner_id

            available_after_calc = round(max(0.0, available_before - amount), 2)
            if locked_owner:
                locked_owner.balance = available_after_calc

        elif beneficiary_key.startswith('user_') or beneficiary_key.startswith('owner_user:'):
            sep = '_' if beneficiary_key.startswith('user_') else ':'
            try:
                owner_uid = int(beneficiary_key.split(sep, 1)[1])
            except Exception:
                return jsonify(success=False, message="معرف مالك الشبكة غير صالح"), 400

            beneficiary_key = f"user_{owner_uid}"

            locked_owner = User.query.filter_by(id=owner_uid).with_for_update().first()
            if not locked_owner:
                db.session.rollback()
                return jsonify(success=False, message="حساب مالك الشبكة غير موجود"), 404

            available_before = round(float(locked_owner.balance or 0), 2)
            if amount > available_before:
                db.session.rollback()
                return jsonify(
                    success=False,
                    message=f"مرفوض: المبلغ المطلوب صرفه ({amount} ر.ي) أكبر من الرصيد المتاح للمستفيد حالياً ({available_before} ر.ي)"
                ), 400

            beneficiary_type = 'network_owner'
            beneficiary_name = f"مالك الشبكة: {locked_owner.full_name} (@{locked_owner.username})"
            target_user_id = locked_owner.id
            target_network_id = None
            locked_owner.balance = round(max(0.0, available_before - amount), 2)

        else:
            return jsonify(success=False, message="نوع المستفيد المحدد غير معروف"), 400

        available_after = round(max(0.0, available_before - amount), 2)

        now_dt = datetime.utcnow()
        voucher_number = f"PV-{now_dt.strftime('%Y%m%d')}-{random.randint(1000, 9999)}"
        while Payout.query.filter_by(voucher_number=voucher_number).first():
            voucher_number = f"PV-{now_dt.strftime('%Y%m%d')}-{random.randint(10000, 99999)}"

        new_payout = Payout(
            voucher_number=voucher_number,
            beneficiary_type=beneficiary_type,
            beneficiary_key=beneficiary_key,
            beneficiary_name=beneficiary_name,
            user_id=target_user_id,
            network_id=target_network_id,
            available_balance_before=available_before,
            amount=amount,
            available_balance_after=available_after,
            transfer_method=transfer_method,
            reference_number=reference_number or voucher_number,
            notes=notes,
            receipt_img=receipt_img_value,
            admin_id=user.id,
            created_at=now_dt
        )
        db.session.add(new_payout)
        db.session.commit()

        # إشعار تلجرام فوري بتسجيل سند الصرف والتسوية
        try:
            tg_msg = (
                f"💸 <b>سند صرف وتسوية مستحقات / أرباح معتمد</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🧾 <b>رقم السند:</b> <code>{new_payout.voucher_number}</code>\n"
                f"👤 <b>المستفيد:</b> {new_payout.beneficiary_name}\n"
                f"💰 <b>الرصيد المتاح قبل الصرف:</b> {available_before:,.0f} ر.ي\n"
                f"📤 <b>المبلغ المصروف:</b> <b>-{amount:,.0f} ر.ي</b>\n"
                f"💵 <b>الرصيد المتبقي بعد الصرف:</b> {available_after:,.0f} ر.ي\n"
                f"🏦 <b>وسيلة التحويل:</b> {transfer_method}\n"
                f"🔢 <b>رقم الحوالة / المرجع:</b> {reference_number or '—'}\n"
                f"📝 <b>ملاحظات:</b> {notes or '—'}\n"
                f"👑 <b>بواسطة المدير:</b> {user.full_name}"
            )
            threading.Thread(target=send_telegram_message, args=(tg_msg,), daemon=True).start()
        except Exception as tg_err:
            print("[Payout Telegram Notify Error]:", tg_err)

        return jsonify(
            success=True,
            message=f"تم اعتماد وصرف مبلغ ({amount:,.0f} ر.ي) لصالح ({beneficiary_name}) بنجاح — رقم السند: {voucher_number}",
            payout=new_payout.to_dict(),
            available_balance=available_after,
            available_balance_after=available_after,
            beneficiaries=get_all_payout_beneficiaries()
        )
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, message=f"حدث خطأ أثناء تنفيذ عملية الصرف: {str(e)}"), 500


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
            (cq.get('message') or {}).get('message_id'),
            ((cq.get('message') or {}).get('chat') or {}).get('id')
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
    ext = os.path.splitext(filename)[1].lower()
    
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

    if ext in ['.js', '.css', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.ico', '.json']:
        return jsonify(error="Asset not found", path=filename), 404

    return serve_index()

# ==================== نقطة الإقلاع والتشغيل ====================

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
