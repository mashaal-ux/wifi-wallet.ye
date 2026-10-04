#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
خادم منصة محفظة كروت شبكات الواي فاي والمدفوعات (Yemen Wi-Fi Wallet)
محدث وشامل لجميع الميزات المتقدمة: البحث، التحقق بالإيميل (OTP)، بونص 10%، إشعارات التلجرام الموسعة، كشوفات الحساب
"""

import os
import sys
import json
import time
import random
import uuid
import urllib.request
import urllib.error
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from datetime import datetime

# ضبط ترميز الطرفية على ويندوز
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# إعدادات بوت التلجرام
BOT_TOKEN = '8939237549:AAES0BcMqQy61KVF-qAEQsAHJjNh_J8X_W8'
CHAT_ID = '5958490522'

PORT = int(os.environ.get('PORT', 3000))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, 'public')
UPLOADS_DIR = os.path.join(PUBLIC_DIR, 'uploads', 'receipts')
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_FILE = os.path.join(DATA_DIR, 'database.json')

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# جلسات المستخدمين ورموز التحقق OTP
SESSIONS = {}
OTP_STORE = {}

# ==================== إدارة قاعدة البيانات JSON ====================

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print("[DB] Load error, resetting:", e)
    
    # حساب المدير العام الوحيد
    data = {
        "users": [
            {
                "id": 1,
                "full_name": "مشعل المريسي",
                "username": "مشعل المريسي",
                "phone": "777000000",
                "email": "admin@yemenwifi.com",
                "password": "Moshal380.",
                "role": "admin",
                "balance": 0,
                "network_id": None,
                "is_verified": True,
                "created_at": datetime.now().isoformat()
            }
        ],
        "networks": [
            {"id": 1, "name": "زين نت (Zain Net)", "code": "zain_net", "logo_icon": "fa-wifi", "description": "شبكة زين نت عالية السرعة - تغطية واسعة ومستقرة", "login_url": "http://zain.wifi/login", "is_active": True, "created_at": datetime.now().isoformat()},
            {"id": 2, "name": "مريس نت (Murais Net)", "code": "murais_net", "logo_icon": "fa-tower-broadcast", "description": "شبكة مريس نت للألياف والمايكروويف - سرعات فائقة", "login_url": "http://murais.net/login", "is_active": True, "created_at": datetime.now().isoformat()},
            {"id": 3, "name": "الفهد نت (Al-Fahd Net)", "code": "fahd_net", "logo_icon": "fa-bolt", "description": "شبكة الفهد نت الذكية - باقات يومية وشهرية توفيرية", "login_url": "http://fahd.wifi/login", "is_active": True, "created_at": datetime.now().isoformat()}
        ],
        "packages": [
            {"id": 1, "network_id": 1, "name": "باقة 3 ساعات سريعة", "price": 100, "duration": "3 ساعات", "data_limit": "500 ميجابايت", "description": "صالحة 3 ساعات", "is_active": True},
            {"id": 2, "network_id": 1, "name": "باقة 24 ساعة يومية", "price": 300, "duration": "24 ساعة", "data_limit": "2 جيجابايت", "description": "يوم كامل", "is_active": True},
            {"id": 3, "network_id": 1, "name": "باقة 3 أيام اقتصادية", "price": 700, "duration": "3 أيام", "data_limit": "5 جيجابايت", "description": "توفير 72 ساعة", "is_active": True},
            {"id": 4, "network_id": 1, "name": "باقة أسبوعية غير محدودة", "price": 1500, "duration": "7 أيام", "data_limit": "15 جيجابايت", "description": "سرعة فائقة", "is_active": True},
            {"id": 5, "network_id": 1, "name": "باقة شهرية ذهبية", "price": 5000, "duration": "30 يوماً", "data_limit": "60 جيجابايت", "description": "الباقة الشهرية الأكثر طلباً", "is_active": True},

            {"id": 6, "network_id": 2, "name": "مريس - كرت ساعة واحدة", "price": 100, "duration": "1 ساعة", "data_limit": "1 جيجابايت", "description": "سرعة تيربو للألعاب", "is_active": True},
            {"id": 7, "network_id": 2, "name": "مريس - كرت يومي ممتاز", "price": 350, "duration": "24 ساعة", "data_limit": "3 جيجابايت", "description": "باقة 24 ساعة", "is_active": True},
            {"id": 8, "network_id": 2, "name": "مريس - باقة أسبوعية برو", "price": 1600, "duration": "7 أيام", "data_limit": "18 جيجابايت", "description": "استقرار وثبات", "is_active": True},
            {"id": 9, "network_id": 2, "name": "مريس - باقة شهرية VIP", "price": 5500, "duration": "30 يوماً", "data_limit": "70 جيجابايت", "description": "بنج منخفض للألعاب", "is_active": True},

            {"id": 10, "network_id": 3, "name": "الفهد - كرت ساعتين", "price": 150, "duration": "2 ساعة", "data_limit": "1.5 جيجابايت", "description": "باقة سريعة", "is_active": True},
            {"id": 11, "network_id": 3, "name": "الفهد - كرت 48 ساعة", "price": 600, "duration": "يومان", "data_limit": "4 جيجابايت", "description": "يومان اتصال سلس", "is_active": True},
            {"id": 12, "network_id": 3, "name": "الفهد - باقة أسبوع بلس", "price": 1400, "duration": "7 أيام", "data_limit": "14 جيجابايت", "description": "عروض الفهد", "is_active": True},
            {"id": 13, "network_id": 3, "name": "الفهد - باقة شهرية توفير", "price": 4800, "duration": "30 يوماً", "data_limit": "50 جيجابايت", "description": "أقوى عروض الفهد", "is_active": True}
        ],
        "cards": [
            {
                "id": i,
                "package_id": ((i - 1) % 13) + 1,
                "network_id": 1 if (((i - 1) % 13) + 1) <= 5 else (2 if (((i - 1) % 13) + 1) <= 9 else 3),
                "pin_code": str(random.randint(10000000, 99999999)),
                "serial_number": f"SN-INIT-{1000 + i}",
                "status": "available",
                "buyer_id": None,
                "purchased_at": None,
                "created_at": datetime.now().isoformat()
            }
            for i in range(1, 27)
        ],
        "deposits": [],
        "bank_accounts": [
            {"id": 1, "bank_name": "بنك الكريمي للتمويل الأصغر الإسلامي", "account_number": "123456789", "account_name": "محفظة كروت شبكات الواي فاي", "logo_icon": "fa-landmark", "instructions": "يرجى كتابة اسمك ورقم هاتفك في خانة الغرض من التحويل", "is_active": True},
            {"id": 2, "bank_name": "بنك القطيبي الإسلامي", "account_number": "987654321", "account_name": "محفظة شبكات اليمن", "logo_icon": "fa-building-columns", "instructions": "إيداع عبر تطبيق بنك القطيبي أو أي فرع", "is_active": True},
            {"id": 3, "bank_name": "صرافة النجم للحوالات", "account_number": "777000111", "account_name": "إدارة المحفظة الإلكترونية", "logo_icon": "fa-money-bill-transfer", "instructions": "حوالة بالاسم والرقم الموضح وإرسال سند الاستلام", "is_active": True},
            {"id": 4, "bank_name": "محفظة جيب / كاش / شامل موني", "account_number": "777223344", "account_name": "محفظة كروت النت", "logo_icon": "fa-mobile-screen", "instructions": "تحويل مباشر من محفظتك الإلكترونية", "is_active": True}
        ],
        "transactions": []
    }
    save_db(data)
    return data

def save_db(data):
    try:
        temp_file = DB_FILE + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, DB_FILE)
        return True
    except Exception as e:
        print("[DB] Save error:", e)
        return False

# ==================== خدمة التلجرام الموسعة (@Croati_Bot) ====================

def send_telegram_message(text):
    """إرسال رسالة نصية عامة إلى بوت تيليجرام"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = json.dumps({"chat_id": CHAT_ID, "text": text}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True, json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print("[Telegram Msg Error]:", e)
        return False, str(e)

def send_telegram_deposit_notification(customer_name, amount, bank_name, deposit_id, image_path, notes=""):
    """إرسال إشعار طلب إيداع مع صورة السند"""
    try:
        formatted_amount = f"{float(amount):,.0f}"
        now_str = datetime.now().strftime("%Y-%m-%d %I:%M %p")
        
        caption = (
            f"📥 طلب إيداع جديد في المحفظة\n"
            f"👤 اسم العميل: {customer_name}\n"
            f"💰 المبلغ: {formatted_amount} ر.ي\n"
            f"🏦 البنك: {bank_name}\n"
            f"🆔 رقم الطلب: #{deposit_id}\n"
            f"📅 التاريخ: {now_str}"
        )
        if notes:
            caption += f"\n📝 ملاحظة: {notes}"

        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        
        if image_path and os.path.exists(image_path):
            boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
            filename = os.path.basename(image_path)
            
            with open(image_path, 'rb') as f:
                file_bytes = f.read()

            body = bytearray()
            body.extend(f"--{boundary}\r\n".encode('utf-8'))
            body.extend(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{CHAT_ID}\r\n'.encode('utf-8'))
            body.extend(f"--{boundary}\r\n".encode('utf-8'))
            body.extend(f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode('utf-8'))
            body.extend(f"--{boundary}\r\n".encode('utf-8'))
            body.extend(f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'.encode('utf-8'))
            body.extend(b'Content-Type: image/jpeg\r\n\r\n')
            body.extend(file_bytes)
            body.extend(b'\r\n')
            body.extend(f"--{boundary}--\r\n".encode('utf-8'))

            req = urllib.request.Request(url, data=bytes(body))
            req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
            req.add_header('Content-Length', str(len(body)))
            
            with urllib.request.urlopen(req, timeout=15) as resp:
                return True, json.loads(resp.read().decode('utf-8'))
        else:
            return send_telegram_message(caption)

    except Exception as e:
        print("[Telegram Deposit Error]:", e)
        return False, str(e)

# ==================== معالج الطلبات HTTP Handler ====================

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    pass

class RequestHandler(SimpleHTTPRequestHandler):

    def get_session_user(self):
        cookie = self.headers.get('Cookie', '')
        session_id = None
        for part in cookie.split(';'):
            part = part.strip()
            if part.startswith('session_id='):
                session_id = part.split('=', 1)[1]
                break
        
        if session_id and session_id in SESSIONS:
            user_id = SESSIONS[session_id]
            db = load_db()
            for u in db.get('users', []):
                if u['id'] == user_id:
                    return u, session_id
        return None, session_id

    def set_session_cookie(self, user_id):
        session_id = str(uuid.uuid4())
        SESSIONS[session_id] = user_id
        self.send_header('Set-Cookie', f'session_id={session_id}; Path=/; HttpOnly; Max-Age=604800')
        return session_id

    def clear_session_cookie(self, session_id):
        if session_id and session_id in SESSIONS:
            del SESSIONS[session_id]
        self.send_header('Set-Cookie', 'session_id=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT')

    def send_json(self, status_code, payload):
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))

    def read_json_body(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 0:
                raw = self.rfile.read(content_length).decode('utf-8')
                return json.loads(raw)
        except Exception:
            pass
        return {}

    # معالجة طلبات HEAD لفحص صحة السيرفر على Render و Cloudflare
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()

    # معالجة طلبات GET
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        user, session_id = self.get_session_user()
        db = load_db()

        # 1. المصادقة والتحقق
        if path == '/api/auth/me':
            if user:
                network = None
                if user.get('network_id'):
                    for n in db.get('networks', []):
                        if n['id'] == user['network_id']:
                            network = n
                            break
                return self.send_json(200, {
                    "success": True,
                    "user": {
                        "id": user["id"],
                        "full_name": user["full_name"],
                        "username": user["username"],
                        "phone": user.get("phone", ""),
                        "email": user.get("email", ""),
                        "role": user["role"],
                        "balance": user.get("balance", 0),
                        "network_id": user.get("network_id"),
                        "network": network
                    }
                })
            else:
                return self.send_json(200, {"success": False, "user": None})

        # 2. متجر الشبكات (مع إخفاء عدد الكروت الدقيق للعملاء)
        elif path == '/api/shop/networks':
            networks = [n for n in db.get('networks', []) if n.get('is_active', True)]
            packages = [p for p in db.get('packages', []) if p.get('is_active', True)]
            cards = [c for c in db.get('cards', []) if c.get('status') == 'available']

            is_privileged = user and (user.get('role') == 'admin' or user.get('role') == 'network_owner')

            res = []
            for net in networks:
                net_pkgs = []
                for pkg in packages:
                    if pkg['network_id'] == net['id']:
                        real_count = sum(1 for c in cards if c['package_id'] == pkg['id'])
                        pkg_copy = dict(pkg)
                        # إذا كان عميلاً عادياً: نظهر فقط إذا كان متوفراً (1) أو نافداً (0) دون كشف العدد الدقيق
                        pkg_copy['available_count'] = real_count if is_privileged else (1 if real_count > 0 else 0)
                        pkg_copy['is_available'] = real_count > 0
                        net_pkgs.append(pkg_copy)
                net_copy = dict(net)
                net_copy['packages'] = net_pkgs
                res.append(net_copy)
            return self.send_json(200, {"success": True, "networks": res})

        # 3. كروتي المشتراة
        elif path == '/api/shop/my-cards':
            if not user:
                return self.send_json(401, {"success": False, "message": "غير مسجل الدخول"})
            
            user_cards = [c for c in db.get('cards', []) if c.get('buyer_id') == user['id']]
            user_cards.sort(key=lambda x: x.get('purchased_at') or '', reverse=True)
            
            res = []
            for c in user_cards:
                pkg = next((p for p in db.get('packages', []) if p['id'] == c['package_id']), {})
                net = next((n for n in db.get('networks', []) if n['id'] == c['network_id']), {})
                res.append({
                    "id": c["id"],
                    "pin_code": c["pin_code"],
                    "serial_number": c["serial_number"],
                    "purchased_at": c.get("purchased_at"),
                    "package_name": pkg.get("name", "باقة"),
                    "price": pkg.get("price", 0),
                    "duration": pkg.get("duration", ""),
                    "data_limit": pkg.get("data_limit", ""),
                    "network_name": net.get("name", "شبكة واي فاي"),
                    "login_url": net.get("login_url", "")
                })
            return self.send_json(200, {"success": True, "cards": res})

        # 4. بنوك الإيداع
        elif path in ['/api/wallet/banks', '/api/shop/banks']:
            banks = [b for b in db.get('bank_accounts', []) if b.get('is_active', True)]
            return self.send_json(200, {"success": True, "banks": banks})

        # 5. طلبات الإيداع للمستخدم
        elif path == '/api/wallet/my-deposits':
            if not user:
                return self.send_json(401, {"success": False, "message": "غير مسجل الدخول"})
            deposits = [d for d in db.get('deposits', []) if d.get('user_id') == user['id']]
            deposits.sort(key=lambda x: x.get('created_at') or '', reverse=True)
            return self.send_json(200, {"success": True, "deposits": deposits})

        # 6. كشف حساب العميل وحركات المحفظة
        elif path == '/api/wallet/transactions':
            if not user:
                return self.send_json(401, {"success": False, "message": "غير مسجل الدخول"})
            transactions = [t for t in db.get('transactions', []) if t.get('user_id') == user['id']]
            transactions.sort(key=lambda x: x.get('created_at') or '', reverse=True)
            return self.send_json(200, {"success": True, "transactions": transactions, "user": user})

        # 7. مسارات الإدارة العليا (Admin)
        elif path == '/api/admin/deposits':
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            
            deposits = list(db.get('deposits', []))
            users_map = {u['id']: u for u in db.get('users', [])}
            
            # تصفية حسب الحالة
            status_filter = query.get('status', ['all'])[0]
            if status_filter != 'all':
                deposits = [d for d in deposits if d.get('status') == status_filter]
                
            # تصفية حسب الفترة (من - إلى)
            date_from = query.get('date_from', [''])[0]
            date_to = query.get('date_to', [''])[0]
            if date_from:
                deposits = [d for d in deposits if (d.get('created_at') or '')[:10] >= date_from]
            if date_to:
                deposits = [d for d in deposits if (d.get('created_at') or '')[:10] <= date_to]

            enriched = []
            for d in deposits:
                u = users_map.get(d.get('user_id'), {})
                d_copy = dict(d)
                d_copy['user_username'] = u.get('username', '')
                d_copy['user_phone'] = u.get('phone', '')
                enriched.append(d_copy)

            enriched.sort(key=lambda x: x.get('created_at') or '', reverse=True)
            return self.send_json(200, {"success": True, "deposits": enriched})

        elif path == '/api/admin/stats':
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            
            deposits = db.get('deposits', [])
            approved_sum = sum(float(d.get('approved_amount', d['amount'])) for d in deposits if d.get('status') == 'approved')
            pending_count = sum(1 for d in deposits if d.get('status') == 'pending')
            cards = db.get('cards', [])
            sold_count = sum(1 for c in cards if c.get('status') == 'sold')
            avail_count = sum(1 for c in cards if c.get('status') == 'available')

            return self.send_json(200, {
                "success": True,
                "stats": {
                    "total_users": len(db.get('users', [])),
                    "total_deposits_approved_amount": approved_sum,
                    "pending_deposits_count": pending_count,
                    "total_cards": len(cards),
                    "sold_cards": sold_count,
                    "available_cards": avail_count,
                    "networks_count": len(db.get('networks', []))
                }
            })

        # إحصائيات وتقارير الشبكات التفصيلية للمدير العام
        elif path == '/api/admin/network-analytics':
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            
            networks = db.get('networks', [])
            packages = db.get('packages', [])
            cards = db.get('cards', [])

            analytics = []
            for net in networks:
                net_pkgs = [p for p in packages if p.get('network_id') == net['id']]
                net_cards = [c for c in cards if c.get('network_id') == net['id']]
                avail_count = sum(1 for c in net_cards if c.get('status') == 'available')
                sold_count = sum(1 for c in net_cards if c.get('status') == 'sold')
                revenue = 0
                for c in net_cards:
                    if c.get('status') == 'sold':
                        pkg = next((p for p in net_pkgs if p['id'] == c.get('package_id')), None)
                        if pkg: revenue += float(pkg.get('price', 0))

                analytics.append({
                    "id": net['id'],
                    "name": net['name'],
                    "logo_icon": net.get('logo_icon', 'fa-wifi'),
                    "total_packages": len(net_pkgs),
                    "total_cards": len(net_cards),
                    "available_cards": avail_count,
                    "sold_cards": sold_count,
                    "total_revenue": revenue
                })
            return self.send_json(200, {"success": True, "analytics": analytics})

        # كشف حساب شبكة محددة للمدير العام
        elif path.startswith('/api/admin/networks/') and path.endswith('/statement'):
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            
            net_id = int(path.split('/')[4])
            net = next((n for n in db.get('networks', []) if n['id'] == net_id), None)
            if not net:
                return self.send_json(404, {"success": False, "message": "الشبكة غير موجودة"})

            packages = db.get('packages', [])
            cards = db.get('cards', [])
            users = db.get('users', [])

            sold_cards = [c for c in cards if c.get('network_id') == net_id and c.get('status') == 'sold']
            
            # تصفية التاريخ (من - إلى)
            date_from = query.get('date_from', [''])[0]
            date_to = query.get('date_to', [''])[0]
            if date_from:
                sold_cards = [c for c in sold_cards if (c.get('purchased_at') or '')[:10] >= date_from]
            if date_to:
                sold_cards = [c for c in sold_cards if (c.get('purchased_at') or '')[:10] <= date_to]

            sold_cards.sort(key=lambda x: x.get('purchased_at') or '', reverse=True)

            items = []
            total_sales = 0
            for c in sold_cards:
                pkg = next((p for p in packages if p['id'] == c.get('package_id')), {})
                buyer = next((u for u in users if u['id'] == c.get('buyer_id')), {})
                p_price = float(pkg.get('price', 0))
                total_sales += p_price
                items.append({
                    "card_id": c['id'],
                    "package_name": pkg.get('name', 'باقة'),
                    "price": p_price,
                    "pin_code": c.get('pin_code'),
                    "serial_number": c.get('serial_number'),
                    "buyer_name": buyer.get('full_name', 'عميل'),
                    "purchased_at": c.get('purchased_at')
                })

            return self.send_json(200, {
                "success": True,
                "network": net,
                "total_sales": total_sales,
                "sold_count": len(items),
                "date_from": date_from,
                "date_to": date_to,
                "items": items
            })

        # كشف حساب مالك الشبكة
        elif path == '/api/network-owner/statement':
            if not user or (user.get('role') != 'network_owner' and user.get('role') != 'admin'):
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            
            net_id = user.get('network_id') or 1
            net = next((n for n in db.get('networks', []) if n['id'] == net_id), None)
            packages = db.get('packages', [])
            cards = db.get('cards', [])
            users = db.get('users', [])

            sold_cards = [c for c in cards if c.get('network_id') == net_id and c.get('status') == 'sold']
            
            # تصفية التاريخ (من - إلى)
            date_from = query.get('date_from', [''])[0]
            date_to = query.get('date_to', [''])[0]
            if date_from:
                sold_cards = [c for c in sold_cards if (c.get('purchased_at') or '')[:10] >= date_from]
            if date_to:
                sold_cards = [c for c in sold_cards if (c.get('purchased_at') or '')[:10] <= date_to]

            sold_cards.sort(key=lambda x: x.get('purchased_at') or '', reverse=True)

            items = []
            total_sales = 0
            for c in sold_cards:
                pkg = next((p for p in packages if p['id'] == c.get('package_id')), {})
                buyer = next((u for u in users if u['id'] == c.get('buyer_id')), {})
                p_price = float(pkg.get('price', 0))
                total_sales += p_price
                items.append({
                    "card_id": c['id'],
                    "package_name": pkg.get('name', 'باقة'),
                    "price": p_price,
                    "pin_code": c.get('pin_code'),
                    "serial_number": c.get('serial_number'),
                    "buyer_name": buyer.get('full_name', 'عميل'),
                    "purchased_at": c.get('purchased_at')
                })

            return self.send_json(200, {
                "success": True,
                "network": net or {"id": net_id, "name": "شبكتي"},
                "totals": {
                    "total_cards_sold": len(items),
                    "total_revenue": total_sales
                },
                "date_from": date_from,
                "date_to": date_to,
                "items": items
            })

        elif path == '/api/admin/profit-analytics':
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            
            networks = db.get('networks', [])
            packages = db.get('packages', [])
            cards = db.get('cards', [])

            date_from = query.get('date_from', [''])[0]
            date_to = query.get('date_to', [''])[0]

            total_sales = 0
            total_cost = 0
            net_summary = []

            for net in networks:
                net_pkgs = [p for p in packages if p.get('network_id') == net['id']]
                net_cards = [c for c in cards if c.get('network_id') == net['id']]
                sold_cards = [c for c in net_cards if c.get('status') == 'sold']
                avail_cards = [c for c in net_cards if c.get('status') == 'available']

                if date_from:
                    sold_cards = [c for c in sold_cards if (c.get('purchased_at') or '')[:10] >= date_from]
                if date_to:
                    sold_cards = [c for c in sold_cards if (c.get('purchased_at') or '')[:10] <= date_to]

                net_sales = 0
                net_cost = 0
                pkg_breakdown = []

                for p in net_pkgs:
                    p_sold = len([c for c in sold_cards if c.get('package_id') == p['id']])
                    p_avail = len([c for c in avail_cards if c.get('package_id') == p['id']])
                    selling_price = float(p.get('price', 0))
                    cost_price = float(p.get('cost_price', round(selling_price * 0.85)))
                    profit_per_card = selling_price - cost_price
                    pkg_revenue = p_sold * selling_price
                    pkg_payout = p_sold * cost_price
                    pkg_profit = p_sold * profit_per_card

                    net_sales += pkg_revenue
                    net_cost += pkg_payout

                    pkg_breakdown.append({
                        "id": p['id'],
                        "name": p.get('name', 'باقة'),
                        "duration": p.get('duration', ''),
                        "data_limit": p.get('data_limit', ''),
                        "price": selling_price,
                        "cost_price": cost_price,
                        "profit_per_card": profit_per_card,
                        "sold_count": p_sold,
                        "available_count": p_avail,
                        "total_revenue": pkg_revenue,
                        "owner_payout": pkg_payout,
                        "admin_profit": pkg_profit
                    })

                net_profit = net_sales - net_cost
                total_sales += net_sales
                total_cost += net_cost

                net_summary.append({
                    "id": net['id'],
                    "name": net['name'],
                    "logo_icon": net.get('logo_icon', 'fa-wifi'),
                    "packages_count": len(net_pkgs),
                    "total_cards": len(net_cards),
                    "available_cards": len(avail_cards),
                    "sold_cards": len(sold_cards),
                    "total_sales": net_sales,
                    "owner_due": net_cost,
                    "admin_profit": net_profit,
                    "profit_margin_pct": round((net_profit / net_sales * 100), 1) if net_sales > 0 else 0,
                    "packages": pkg_breakdown
                })

            total_profit = total_sales - total_cost
            overall_margin = round((total_profit / total_sales * 100), 1) if total_sales > 0 else 0

            return self.send_json(200, {
                "success": True,
                "summary": {
                    "total_platform_sales": total_sales,
                    "total_network_payouts": total_cost,
                    "total_admin_profit": total_profit,
                    "overall_profit_margin_pct": overall_margin,
                    "total_networks": len(networks),
                    "total_cards_count": len(cards),
                    "total_sold_count": sum(n['sold_cards'] for n in net_summary),
                    "total_available_count": sum(n['available_cards'] for n in net_summary),
                    "date_from": date_from,
                    "date_to": date_to
                },
                "networks": net_summary
            })

        elif path == '/api/admin/cards-inventory':
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            
            net_filter = query.get('network_id', ['all'])[0]
            pkg_filter = query.get('package_id', ['all'])[0]
            status_filter = query.get('status', ['all'])[0]
            date_from = query.get('date_from', [''])[0]
            date_to = query.get('date_to', [''])[0]

            all_cards = db.get('cards', [])
            packages = db.get('packages', [])
            networks = db.get('networks', [])
            users = db.get('users', [])

            filtered = []
            for c in all_cards:
                if net_filter != 'all' and str(c.get('network_id')) != str(net_filter):
                    continue
                if pkg_filter != 'all' and str(c.get('package_id')) != str(pkg_filter):
                    continue
                if status_filter != 'all' and c.get('status') != status_filter:
                    continue
                
                c_date = (c.get('purchased_at') or c.get('created_at') or '')[:10]
                if date_from and c_date and c_date < date_from:
                    continue
                if date_to and c_date and c_date > date_to:
                    continue

                filtered.append(c)

            filtered.sort(key=lambda x: x.get('created_at') or '', reverse=True)

            items = []
            total_val = 0
            sold_val = 0
            profit_val = 0

            for c in filtered:
                pkg = next((p for p in packages if p['id'] == c.get('package_id')), {})
                net = next((n for n in networks if n['id'] == c.get('network_id')), {})
                buyer = None
                if c.get('buyer_id'):
                    buyer = next((u for u in users if u['id'] == c.get('buyer_id')), None)

                price = float(pkg.get('price', 0))
                cost_price = float(pkg.get('cost_price', round(price * 0.85)))
                profit = price - cost_price

                total_val += price
                if c.get('status') == 'sold':
                    sold_val += price
                    profit_val += profit

                items.append({
                    "id": c['id'],
                    "pin_code": c.get('pin_code'),
                    "serial_number": c.get('serial_number'),
                    "status": c.get('status', 'available'),
                    "network_id": c.get('network_id'),
                    "network_name": net.get('name', 'شبكة واي فاي'),
                    "package_id": c.get('package_id'),
                    "package_name": pkg.get('name', 'باقة'),
                    "duration": pkg.get('duration', ''),
                    "data_limit": pkg.get('data_limit', ''),
                    "price": price,
                    "cost_price": cost_price,
                    "profit": profit,
                    "buyer_id": c.get('buyer_id'),
                    "buyer_name": buyer.get('full_name') if buyer else ('عميل' if c.get('status') == 'sold' else None),
                    "buyer_phone": buyer.get('phone') if buyer else None,
                    "purchased_at": c.get('purchased_at'),
                    "created_at": c.get('created_at')
                })

            avail_count_in_filter = len([c for c in items if c.get('status') == 'available'])
            sold_count_in_filter = len([c for c in items if c.get('status') == 'sold'])

            return self.send_json(200, {
                "success": True,
                "total_count": len(items),
                "total_value": total_val,
                "sold_value": sold_val,
                "realized_profit": profit_val,
                "date_from": date_from,
                "date_to": date_to,
                "metrics": {
                    "total_cards": len(items),
                    "available_cards": avail_count_in_filter,
                    "sold_cards": sold_count_in_filter,
                    "total_sold_value": sold_val,
                    "total_profit": profit_val
                },
                "cards": items
            })

        # كشف حساب تفصيلي لعميل محدد للمدير العام
        elif path.startswith('/api/admin/users/') and path.endswith('/statement'):
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            
            target_id = int(path.split('/')[4])
            target_user = next((u for u in db.get('users', []) if u['id'] == target_id), None)
            if not target_user:
                return self.send_json(404, {"success": False, "message": "المستخدم غير موجود"})

            date_from = query.get('date_from', [''])[0]
            date_to = query.get('date_to', [''])[0]

            transactions = [t for t in db.get('transactions', []) if t.get('user_id') == target_id]
            deposits = [d for d in db.get('deposits', []) if d.get('user_id') == target_id]
            bought_cards = [c for c in db.get('cards', []) if c.get('buyer_id') == target_id]

            if date_from:
                transactions = [t for t in transactions if (t.get('created_at') or '')[:10] >= date_from]
                deposits = [d for d in deposits if (d.get('created_at') or '')[:10] >= date_from]
                bought_cards = [c for c in bought_cards if (c.get('purchased_at') or '')[:10] >= date_from]
            if date_to:
                transactions = [t for t in transactions if (t.get('created_at') or '')[:10] <= date_to]
                deposits = [d for d in deposits if (d.get('created_at') or '')[:10] <= date_to]
                bought_cards = [c for c in bought_cards if (c.get('purchased_at') or '')[:10] <= date_to]

            transactions.sort(key=lambda x: x.get('created_at') or '', reverse=True)
            deposits.sort(key=lambda x: x.get('created_at') or '', reverse=True)
            bought_cards.sort(key=lambda x: x.get('purchased_at') or '', reverse=True)

            enhanced_cards = []
            total_spent = 0
            for bc in bought_cards:
                pkg = next((p for p in db.get('packages', []) if p['id'] == bc.get('package_id')), {})
                net = next((n for n in db.get('networks', []) if n['id'] == bc.get('network_id')), {})
                p_price = float(pkg.get('price', 0))
                total_spent += p_price
                enhanced_cards.append({
                    "id": bc['id'],
                    "pin_code": bc.get('pin_code'),
                    "serial_number": bc.get('serial_number'),
                    "network_name": net.get('name', 'شبكة واي فاي'),
                    "package_name": pkg.get('name', 'باقة'),
                    "price": p_price,
                    "duration": pkg.get('duration', ''),
                    "data_limit": pkg.get('data_limit', ''),
                    "purchased_at": bc.get('purchased_at')
                })

            total_deposits = sum(float(d.get('approved_amount', d.get('amount', 0))) for d in deposits if d.get('status') == 'approved')
            total_bonus = sum(float(d.get('bonus_amount', round(float(d.get('approved_amount', d.get('amount', 0))) * 0.10))) for d in deposits if d.get('status') == 'approved')

            statement_summary = {
                "current_balance": target_user.get('balance', 0),
                "total_deposits": total_deposits,
                "total_deposits_approved_amount": total_deposits,
                "total_bonus": total_bonus,
                "total_bonus_received": total_bonus,
                "total_spent": total_spent,
                "total_cards_spent": total_spent,
                "cards_count": len(enhanced_cards),
                "cards_bought_count": len(enhanced_cards),
                "date_from": date_from,
                "date_to": date_to
            }

            return self.send_json(200, {
                "success": True,
                "user": {
                    "id": target_user['id'],
                    "full_name": target_user['full_name'],
                    "username": target_user['username'],
                    "phone": target_user.get('phone', ''),
                    "email": target_user.get('email', ''),
                    "role": target_user.get('role', 'customer'),
                    "balance": target_user.get('balance', 0),
                    "created_at": target_user.get('created_at', '')
                },
                "totals": statement_summary,
                "summary": statement_summary,
                "transactions": transactions,
                "deposits": deposits,
                "cards": enhanced_cards,
                "date_from": date_from,
                "date_to": date_to
            })

        elif path == '/api/admin/customers-master-report':
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})

            users = db.get('users', [])
            deposits = db.get('deposits', [])
            cards = db.get('cards', [])
            packages = db.get('packages', [])

            date_from = query.get('date_from', [''])[0]
            date_to = query.get('date_to', [''])[0]

            report = []
            total_balances = 0
            total_deposits_all = 0
            total_bonus_all = 0
            total_spent_all = 0

            for u in users:
                u_id = u['id']
                u_deposits = [d for d in deposits if d.get('user_id') == u_id and d.get('status') == 'approved']
                u_cards = [c for c in cards if c.get('buyer_id') == u_id]

                if date_from:
                    u_deposits = [d for d in u_deposits if (d.get('created_at') or '')[:10] >= date_from]
                    u_cards = [c for c in u_cards if (c.get('purchased_at') or '')[:10] >= date_from]
                if date_to:
                    u_deposits = [d for d in u_deposits if (d.get('created_at') or '')[:10] <= date_to]
                    u_cards = [c for c in u_cards if (c.get('purchased_at') or '')[:10] <= date_to]

                dep_sum = sum(float(d.get('approved_amount', d.get('amount', 0))) for d in u_deposits)
                bon_sum = sum(float(d.get('bonus_amount', round(float(d.get('approved_amount', d.get('amount', 0))) * 0.10))) for d in u_deposits)

                spent_sum = 0
                for uc in u_cards:
                    pkg = next((p for p in packages if p['id'] == uc.get('package_id')), {})
                    spent_sum += float(pkg.get('price', 0))

                bal = float(u.get('balance', 0))
                total_balances += bal
                total_deposits_all += dep_sum
                total_bonus_all += bon_sum
                total_spent_all += spent_sum

                net = next((n for n in db.get('networks', []) if n['id'] == u.get('network_id')), None)

                report.append({
                    "id": u['id'],
                    "full_name": u['full_name'],
                    "username": u['username'],
                    "phone": u.get('phone', '-'),
                    "email": u.get('email', '-'),
                    "role": u.get('role', 'customer'),
                    "network_name": net['name'] if net else None,
                    "balance": bal,
                    "total_deposits": dep_sum,
                    "total_deposits_approved_amount": dep_sum,
                    "total_bonus": bon_sum,
                    "total_bonus_received": bon_sum,
                    "cards_count": len(u_cards),
                    "cards_bought_count": len(u_cards),
                    "total_spent": spent_sum,
                    "total_cards_spent": spent_sum,
                    "created_at": u.get('created_at')
                })

            return self.send_json(200, {
                "success": True,
                "summary": {
                    "total_customers_count": len(report),
                    "total_balances_sum": total_balances,
                    "total_deposits_sum": total_deposits_all,
                    "total_deposits_volume": total_deposits_all,
                    "total_bonus_sum": total_bonus_all,
                    "total_bonus_volume": total_bonus_all,
                    "total_spent_sum": total_spent_all,
                    "total_cards_sales_volume": total_spent_all
                },
                "customers": report
            })

        elif path == '/api/admin/users':
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            users_list = []
            deposits = db.get('deposits', [])
            cards = db.get('cards', [])
            packages = db.get('packages', [])

            for u in db.get('users', []):
                u_id = u['id']
                net = next((n for n in db.get('networks', []) if n['id'] == u.get('network_id')), None)
                u_deposits = [d for d in deposits if d.get('user_id') == u_id and d.get('status') == 'approved']
                u_cards = [c for c in cards if c.get('buyer_id') == u_id]

                dep_sum = sum(float(d.get('approved_amount', d.get('amount', 0))) for d in u_deposits)
                bon_sum = sum(float(d.get('bonus_amount', round(float(d.get('approved_amount', d.get('amount', 0))) * 0.10))) for d in u_deposits)

                spent_sum = 0
                for uc in u_cards:
                    pkg = next((p for p in packages if p['id'] == uc.get('package_id')), {})
                    spent_sum += float(pkg.get('price', 0))

                users_list.append({
                    "id": u["id"],
                    "full_name": u["full_name"],
                    "username": u["username"],
                    "phone": u.get("phone", ""),
                    "email": u.get("email", ""),
                    "role": u.get("role", "customer"),
                    "balance": u.get("balance", 0),
                    "network_id": u.get("network_id"),
                    "network_name": net["name"] if net else None,
                    "financial_summary": {
                        "total_deposits_approved_amount": dep_sum,
                        "deposits_count": len(u_deposits),
                        "total_bonus_received": bon_sum,
                        "cards_bought_count": len(u_cards),
                        "total_cards_spent": spent_sum
                    },
                    "total_deposits": dep_sum,
                    "total_bonus": bon_sum,
                    "cards_count": len(u_cards),
                    "total_spent": spent_sum,
                    "created_at": u.get("created_at")
                })
            return self.send_json(200, {"success": True, "users": users_list})

        elif path == '/api/admin/networks':
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            return self.send_json(200, {"success": True, "networks": db.get('networks', [])})

        elif path == '/api/admin/banks':
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            return self.send_json(200, {"success": True, "banks": db.get('bank_accounts', [])})

        # 8. مسارات مالك الشبكة (Network Owner)
        elif path == '/api/network-owner/my-network':
            if not user or (user.get('role') != 'network_owner' and user.get('role') != 'admin'):
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            
            network_id = user.get('network_id') or 1
            if user.get('role') == 'admin' and query.get('network_id'):
                network_id = int(query.get('network_id')[0])

            network = next((n for n in db.get('networks', []) if n['id'] == network_id), None)
            if not network:
                return self.send_json(404, {"success": False, "message": "الشبكة غير موجودة"})

            packages = [p for p in db.get('packages', []) if p.get('network_id') == network_id]
            cards = [c for c in db.get('cards', []) if c.get('network_id') == network_id]
            avail_cards = [c for c in cards if c.get('status') == 'available']
            sold_cards = [c for c in cards if c.get('status') == 'sold']

            total_revenue = sum(next((p.get('price', 0) for p in packages if p['id'] == c.get('package_id')), 0) for c in sold_cards)

            return self.send_json(200, {
                "success": True,
                "network": network,
                "packages": packages,
                "stats": {
                    "total_packages": len(packages),
                    "total_cards": len(cards),
                    "available_cards_count": len(avail_cards),
                    "sold_cards_count": len(sold_cards),
                    "total_revenue": total_revenue
                }
            })

        elif path == '/api/network-owner/cards':
            if not user or (user.get('role') != 'network_owner' and user.get('role') != 'admin'):
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            
            network_id = user.get('network_id') or 1
            status_filter = query.get('status', ['all'])[0]
            cards = [c for c in db.get('cards', []) if c.get('network_id') == network_id]
            if status_filter != 'all':
                cards = [c for c in cards if c.get('status') == status_filter]
            
            cards.sort(key=lambda x: x.get('created_at') or '', reverse=True)
            packages = [p for p in db.get('packages', []) if p.get('network_id') == network_id]
            
            enhanced_cards = []
            for c in cards:
                pkg = next((p for p in packages if p['id'] == c.get('package_id')), {})
                buyer = None
                if c.get('buyer_id'):
                    u = next((usr for usr in db.get('users', []) if usr['id'] == c['buyer_id']), None)
                    if u: buyer = {"full_name": u["full_name"], "username": u["username"], "phone": u.get("phone", "")}
                
                c_copy = dict(c)
                c_copy['package_name'] = pkg.get('name', 'باقة')
                c_copy['package_price'] = pkg.get('price', 0)
                c_copy['buyer'] = buyer
                enhanced_cards.append(c_copy)
            
            return self.send_json(200, {"success": True, "cards": enhanced_cards})

        # منع إرجاع ملفات HTML للمسارات البرمجية غير المعرفة
        if path.startswith('/api/'):
            return self.send_json(404, {"success": False, "message": f"مسار API غير موجود ({path})"})

        # تقديم الملفات الثابتة
        return self.serve_static_files(path)

    # معالجة طلبات POST
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        user, session_id = self.get_session_user()
        db = load_db()

        # 1. إرسال رمز التحقق OTP للتسجيل بالإيميل
        if path == '/api/auth/send-otp':
            body = self.read_json_body()
            email = body.get('email', '').strip().lower()
            username = body.get('username', '').strip()

            if not email:
                return self.send_json(400, {"success": False, "message": "يرجى إدخال البريد الإلكتروني"})

            # فحص إذا كان الإيميل مسجل مسبقاً
            for u in db.get('users', []):
                if u.get('email', '').lower() == email:
                    return self.send_json(400, {"success": False, "message": "هذا البريد الإلكتروني مسجل مسبقاً"})

            # توليد كود OTP مكون من 6 أرقام
            otp_code = str(random.randint(100000, 999999))
            OTP_STORE[email] = {
                "code": otp_code,
                "expires_at": time.time() + 600 # صالحة 10 دقائق
            }

            print(f"[OTP Generated] Verification code for {email}: {otp_code}")

            # إرسال إشعار للمدير على التلجرام بأن هناك عملية تسجيل جديدة جارية
            send_telegram_message(f"🔐 كود تحقق تسجيل جديد (OTP):\n👤 المستخدم: {username}\n📧 البريد: {email}\n🔑 الرمز: {otp_code}")

            return self.send_json(200, {
                "success": True,
                "message": f"تم إرسال كود التحقق إلى بريدك الإلكتروني ({email}).",
                "otp_hint": otp_code # للتسهيل والمحاكاة المباشرة
            })

        # 2. التحقق من كود الـ OTP وإتمام التسجيل
        elif path == '/api/auth/verify-otp':
            body = self.read_json_body()
            full_name = body.get('full_name', '').strip()
            username = body.get('username', '').strip()
            phone = body.get('phone', '').strip()
            email = body.get('email', '').strip().lower()
            password = body.get('password', '').strip()
            otp_code = body.get('otp_code', '').strip()

            if not full_name or not username or not password or not email or not otp_code:
                return self.send_json(400, {"success": False, "message": "يرجى تعبئة كافة الحقول وكود التحقق"})

            # التحقق من صحة الكود
            otp_data = OTP_STORE.get(email)
            if not otp_data or otp_data.get('code') != otp_code or time.time() > otp_data.get('expires_at', 0):
                return self.send_json(400, {"success": False, "message": "كود التحقق غير صحيح أو انتهت صلاحيته"})

            # فحص تكرار اسم المستخدم
            for u in db.get('users', []):
                if u['username'].lower() == username.lower():
                    return self.send_json(400, {"success": False, "message": "اسم المستخدم مسجل مسبقاً"})

            next_id = max([u['id'] for u in db.get('users', [])] or [0]) + 1
            new_user = {
                "id": next_id,
                "full_name": full_name,
                "username": username,
                "phone": phone,
                "email": email,
                "password": password,
                "role": "customer",
                "balance": 0,
                "network_id": None,
                "is_verified": True,
                "created_at": datetime.now().isoformat()
            }
            db.setdefault('users', []).append(new_user)
            save_db(db)

            # إرسال إشعار تلجرام بتسجيل عميل جديد
            send_telegram_message(
                f"👤 تسجيل حساب عميل جديد بالمنصة:\n"
                f"▪️ الاسم: {full_name}\n"
                f"▪️ اسم المستخدم: {username}\n"
                f"▪️ الهاتف: {phone or 'غير مسجل'}\n"
                f"▪️ البريد: {email}\n"
                f"⏱️ التاريخ: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
            )

            # تسجيل الدخول وتعيين الجلسة
            self.send_response(200)
            self.set_session_cookie(new_user['id'])
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            return self.wfile.write(json.dumps({
                "success": True,
                "message": "تم تفعيل الحساب وتسجيل الدخول بنجاح!",
                "user": new_user
            }, ensure_ascii=False).encode('utf-8'))

        # 3. تسجيل الدخول
        elif path == '/api/auth/login':
            body = self.read_json_body()
            username = body.get('username', '').strip()
            password = body.get('password', '').strip()

            target_user = None
            for u in db.get('users', []):
                # مطابقة اسم المستخدم أو الإيميل أو الهاتف
                if (u['username'].lower() == username.lower() or u.get('email', '').lower() == username.lower() or u.get('phone', '') == username) and u['password'] == password:
                    target_user = u
                    break

            if target_user:
                self.send_response(200)
                self.set_session_cookie(target_user['id'])
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                
                net = None
                if target_user.get('network_id'):
                    net = next((n for n in db.get('networks', []) if n['id'] == target_user['network_id']), None)

                return self.wfile.write(json.dumps({
                    "success": True,
                    "message": "تم تسجيل الدخول بنجاح",
                    "user": {
                        "id": target_user["id"],
                        "full_name": target_user["full_name"],
                        "username": target_user["username"],
                        "phone": target_user.get("phone", ""),
                        "email": target_user.get("email", ""),
                        "role": target_user["role"],
                        "balance": target_user.get("balance", 0),
                        "network_id": target_user.get("network_id"),
                        "network": net
                    }
                }, ensure_ascii=False).encode('utf-8'))
            else:
                return self.send_json(401, {"success": False, "message": "بيانات تسجيل الدخول غير صحيحة"})

        # 4. تسجيل الخروج
        elif path == '/api/auth/logout':
            self.send_response(200)
            self.clear_session_cookie(session_id)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            return self.wfile.write(json.dumps({"success": True, "message": "تم تسجيل الخروج"}, ensure_ascii=False).encode('utf-8'))

        # 5. رفع السند والإيداع وإرساله للتلجرام
        elif path == '/api/wallet/deposit':
            if not user:
                return self.send_json(401, {"success": False, "message": "يرجى تسجيل الدخول أولاً"})

            content_type = self.headers.get('Content-Type', '')
            if 'multipart/form-data' not in content_type:
                return self.send_json(400, {"success": False, "message": "نوع الطلب يجب أن يكون multipart/form-data"})

            try:
                boundary = content_type.split('boundary=')[1].strip()
                content_length = int(self.headers.get('Content-Length', 0))
                body_bytes = self.rfile.read(content_length)

                fields, files = self.parse_multipart(body_bytes, boundary)

                customer_name = fields.get('customer_name', '').strip()
                amount_str = fields.get('amount', '0').strip()
                bank_name = fields.get('bank_name', '').strip()
                notes = fields.get('notes', '').strip()

                if not customer_name or not amount_str or not bank_name:
                    return self.send_json(400, {"success": False, "message": "يرجى ملء جميع الحقول المطلوبة (اسم المودع، المبلغ، البنك)"})

                amount = float(amount_str)
                if amount <= 0:
                    return self.send_json(400, {"success": False, "message": "المبلغ يجب أن يكون أكبر من 0"})

                receipt_file = files.get('receipt_image')
                if not receipt_file:
                    return self.send_json(400, {"success": False, "message": "يرجى إرفاق صورة السند"})

                ext = '.jpg'
                orig_name = receipt_file.get('filename', '')
                if '.' in orig_name:
                    ext = '.' + orig_name.rsplit('.', 1)[1].lower()
                
                saved_filename = f"receipt-{int(time.time())}-{uuid.uuid4().hex[:6]}{ext}"
                saved_path = os.path.join(UPLOADS_DIR, saved_filename)
                with open(saved_path, 'wb') as f:
                    f.write(receipt_file['content'])

                relative_img_url = f"/uploads/receipts/{saved_filename}"
                next_dep_id = max([d['id'] for d in db.get('deposits', [])] or [0]) + 1
                new_deposit = {
                    "id": next_dep_id,
                    "user_id": user["id"],
                    "customer_name": customer_name,
                    "amount": amount,
                    "approved_amount": amount,
                    "bank_name": bank_name,
                    "receipt_image": relative_img_url,
                    "receipt_filename": saved_filename,
                    "notes": notes,
                    "status": "pending",
                    "admin_note": "",
                    "telegram_status": "pending",
                    "created_at": datetime.now().isoformat()
                }
                db.setdefault('deposits', []).append(new_deposit)
                save_db(db)

                # إرسال السند فورياً للتلجرام
                tg_ok, tg_resp = send_telegram_deposit_notification(
                    customer_name=customer_name,
                    amount=amount,
                    bank_name=bank_name,
                    deposit_id=next_dep_id,
                    image_path=saved_path,
                    notes=notes
                )

                new_deposit['telegram_status'] = 'sent' if tg_ok else 'failed'
                save_db(db)

                return self.send_json(200, {
                    "success": True,
                    "message": "تم إرسال طلب الإيداع وصورة السند بنجاح وتم إشعار الإدارة فوراً عبر بوت تلجرام!",
                    "deposit": new_deposit,
                    "telegram_sent": tg_ok
                })

            except Exception as e:
                print("[Deposit Submit Error]:", e)
                return self.send_json(500, {"success": False, "message": f"حدث خطأ: {str(e)}"})

        # 6. شراء كرت واي فاي وتنبيه نفاذ الكروت للتلجرام
        elif path == '/api/shop/purchase':
            if not user:
                return self.send_json(401, {"success": False, "message": "يرجى تسجيل الدخول أولاً"})

            body = self.read_json_body()
            pkg_id = body.get('package_id')
            if not pkg_id:
                return self.send_json(400, {"success": False, "message": "يرجى تحديد الباقة"})

            pkg = next((p for p in db.get('packages', []) if p['id'] == int(pkg_id)), None)
            if not pkg or not pkg.get('is_active', True):
                return self.send_json(400, {"success": False, "message": "الباقة غير متوفرة"})

            net = next((n for n in db.get('networks', []) if n['id'] == pkg['network_id']), None)

            current_balance = user.get('balance', 0)
            if current_balance < pkg['price']:
                return self.send_json(400, {
                    "success": False, 
                    "message": f"رصيد المحفظة غير كافٍ. رصيدك الحالي {current_balance:,.0f} ر.ي، وسعر الكرت {pkg['price']:,.0f} ر.ي"
                })

            available_card = next((c for c in db.get('cards', []) if c['package_id'] == pkg['id'] and c['status'] == 'available'), None)
            if not available_card:
                return self.send_json(400, {"success": False, "message": "عذراً، نفد مخزون كروت هذه الباقة حالياً"})

            new_balance = current_balance - pkg['price']
            for u in db['users']:
                if u['id'] == user['id']:
                    u['balance'] = new_balance
                    break

            available_card['status'] = 'sold'
            available_card['buyer_id'] = user['id']
            available_card['purchased_at'] = datetime.now().isoformat()

            next_t_id = max([t['id'] for t in db.get('transactions', [])] or [0]) + 1
            db.setdefault('transactions', []).append({
                "id": next_t_id,
                "user_id": user["id"],
                "type": "card_purchase",
                "amount": -pkg['price'],
                "balance_after": new_balance,
                "description": f"شراء {pkg['name']} - شبكة {net['name']}",
                "reference_id": available_card["id"],
                "created_at": datetime.now().isoformat()
            })

            save_db(db)

            # فحص المخزون المتبقي للباقة وإرسال تنبيه تلجرام إذا قل عن 2 كروت أو نفد
            remaining_stock = sum(1 for c in db.get('cards', []) if c['package_id'] == pkg['id'] and c['status'] == 'available')
            if remaining_stock <= 2:
                status_text = "⚠️ تنبيه: قرب نفاذ المخزون" if remaining_stock > 0 else "🚨 تنبيه عاجل: نفد مخزون الكروت بالكامل!"
                send_telegram_message(
                    f"{status_text}\n"
                    f"🌐 الشبكة: {net['name']}\n"
                    f"📦 الباقة: {pkg['name']}\n"
                    f"🔢 الكروت المتبقية: {remaining_stock} كرت\n"
                    f"يرجى تغذية كروت جديدة للشبكة."
                )

            return self.send_json(200, {
                "success": True,
                "message": f"تم شراء الكرت بنجاح! تم خصم {pkg['price']:,.0f} ر.ي من محفظتك.",
                "remaining_balance": new_balance,
                "card": {
                    "id": available_card["id"],
                    "pin_code": available_card["pin_code"],
                    "serial_number": available_card["serial_number"],
                    "purchased_at": available_card["purchased_at"],
                    "package_name": pkg["name"],
                    "price": pkg["price"],
                    "duration": pkg.get("duration", ""),
                    "data_limit": pkg.get("data_limit", ""),
                    "network_name": net["name"],
                    "login_url": net.get("login_url", "")
                }
            })

        # 7. اعتماد طلب إيداع (مع إمكانية تعديل المبلغ وإضافة بونص 10% مجاناً تلقائياً)
        elif path.startswith('/api/admin/deposits/') and path.endswith('/approve'):
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})

            dep_id = int(path.split('/')[4])
            body = self.read_json_body()
            note = body.get('note', '')
            
            deposit = next((d for d in db.get('deposits', []) if d['id'] == dep_id), None)
            if not deposit:
                return self.send_json(404, {"success": False, "message": "طلب الإيداع غير موجود"})
            if deposit.get('status') != 'pending':
                return self.send_json(400, {"success": False, "message": f"الطلب تمت معالجته مسبقاً ({deposit.get('status')})"})

            target_user = next((u for u in db.get('users', []) if u['id'] == deposit['user_id']), None)
            if not target_user:
                return self.send_json(404, {"success": False, "message": "المستخدم غير موجود"})

            # المبلغ المعتمد بعد التعديل (أو المبلغ الأصلي)
            approved_amount = float(body.get('approved_amount', deposit['amount']))
            if approved_amount <= 0:
                approved_amount = deposit['amount']

            # حساب البونص المجاني الإضافي بنسبة 10% تلقائياً
            bonus_amount = round(approved_amount * 0.10, 2)
            total_credited = approved_amount + bonus_amount

            deposit['status'] = 'approved'
            deposit['approved_amount'] = approved_amount
            deposit['bonus_amount'] = bonus_amount
            deposit['admin_note'] = note
            deposit['reviewed_at'] = datetime.now().isoformat()

            # شحن الرصيد للعميل (المبلغ + البونص 10%)
            target_user['balance'] = target_user.get('balance', 0) + total_credited

            # تسجيل المعاملة في كشف الحساب
            next_t_id = max([t['id'] for t in db.get('transactions', [])] or [0]) + 1
            db.setdefault('transactions', []).append({
                "id": next_t_id,
                "user_id": target_user["id"],
                "type": "deposit",
                "amount": total_credited,
                "deposit_base": approved_amount,
                "bonus_amount": bonus_amount,
                "balance_after": target_user['balance'],
                "description": f"إيداع معتمد: {approved_amount:,.0f} ر.ي + بونص إضافي 10%: {bonus_amount:,.0f} ر.ي ({deposit['bank_name']})",
                "reference_id": deposit["id"],
                "created_at": datetime.now().isoformat()
            })

            save_db(db)

            # إرسال إشعار تلجرام بالاعتماد والشحن
            send_telegram_message(
                f"✅ تم اعتماد إيداع وشحن رصيد بنجاح!\n"
                f"👤 العميل: {target_user['full_name']} (@{target_user['username']})\n"
                f"💰 المبلغ المعتمد: {approved_amount:,.0f} ر.ي\n"
                f"🎁 البونص المجاني (10%): +{bonus_amount:,.0f} ر.ي\n"
                f"💎 الإجمالي المشحون: {total_credited:,.0f} ر.ي\n"
                f"🏦 البنك: {deposit['bank_name']}\n"
                f"💳 رصيد المحفظة الجديد: {target_user['balance']:,.0f} ر.ي"
            )

            return self.send_json(200, {
                "success": True,
                "message": f"تم اعتماد الإيداع بنجاح وشحن {total_credited:,.0f} ر.ي (شاملة بونص 10% مجاناً) في محفظة ({target_user['full_name']}).",
                "deposit": deposit
            })

        # 8. رفض طلب إيداع
        elif path.startswith('/api/admin/deposits/') and path.endswith('/reject'):
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})

            dep_id = int(path.split('/')[4])
            body = self.read_json_body()
            note = body.get('note', 'سند التحويل غير مطابق')

            deposit = next((d for d in db.get('deposits', []) if d['id'] == dep_id), None)
            if not deposit:
                return self.send_json(404, {"success": False, "message": "طلب الإيداع غير موجود"})

            deposit['status'] = 'rejected'
            deposit['admin_note'] = note
            deposit['reviewed_at'] = datetime.now().isoformat()
            save_db(db)

            return self.send_json(200, {"success": True, "message": "تم رفض طلب الإيداع", "deposit": deposit})

        # 9. تعديل رصيد مستخدم يدوياً
        elif path.startswith('/api/admin/users/') and path.endswith('/balance'):
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})

            u_id = int(path.split('/')[4])
            body = self.read_json_body()
            action = body.get('action', 'add')
            amount = float(body.get('amount', 0))
            note = body.get('note', '')

            target_user = next((u for u in db.get('users', []) if u['id'] == u_id), None)
            if not target_user:
                return self.send_json(404, {"success": False, "message": "المستخدم غير موجود"})

            cur_bal = target_user.get('balance', 0)
            if action == 'add': new_bal = cur_bal + amount
            elif action == 'subtract': new_bal = max(0, cur_bal - amount)
            else: new_bal = amount

            target_user['balance'] = new_bal
            next_t_id = max([t['id'] for t in db.get('transactions', [])] or [0]) + 1
            db.setdefault('transactions', []).append({
                "id": next_t_id,
                "user_id": target_user["id"],
                "type": "admin_adjustment",
                "amount": -amount if action == 'subtract' else amount,
                "balance_after": new_bal,
                "description": f"تعديل بواسطة الإدارة: {note}",
                "reference_id": user["id"],
                "created_at": datetime.now().isoformat()
            })
            save_db(db)
            return self.send_json(200, {"success": True, "message": "تم تحديث الرصيد بنجاح", "new_balance": new_bal})

        # تعديل بيانات مستخدم بواسطة المدير العام
        elif path.startswith('/api/admin/users/') and path.endswith('/edit'):
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})

            u_id = int(path.split('/')[4])
            body = self.read_json_body()
            target_user = next((u for u in db.get('users', []) if u['id'] == u_id), None)
            if not target_user:
                return self.send_json(404, {"success": False, "message": "المستخدم غير موجود"})

            if 'full_name' in body and body['full_name'].strip():
                target_user['full_name'] = body['full_name'].strip()
            if 'username' in body and body['username'].strip():
                new_uname = body['username'].strip()
                if any(u['id'] != u_id and u['username'] == new_uname for u in db.get('users', [])):
                    return self.send_json(400, {"success": False, "message": "اسم المستخدم موجود مسبقاً"})
                target_user['username'] = new_uname
            if 'phone' in body:
                target_user['phone'] = body['phone'].strip()
            if 'email' in body:
                target_user['email'] = body['email'].strip()
            if 'role' in body and body['role'] in ['customer', 'network_owner', 'admin']:
                target_user['role'] = body['role']
            if 'network_id' in body:
                net_val = body['network_id']
                target_user['network_id'] = int(net_val) if (net_val and str(net_val) != '0' and str(net_val) != 'null') else None
            if 'password' in body and body['password'].strip():
                target_user['password'] = body['password'].strip()

            save_db(db)
            return self.send_json(200, {"success": True, "message": "تم تحديث بيانات المستخدم بنجاح", "user": target_user})

        # تعديل بيانات باقة بواسطة المدير العام أو مالك الشبكة
        elif path.startswith('/api/admin/packages/') and path.endswith('/edit'):
            if not user or (user.get('role') != 'admin' and user.get('role') != 'network_owner'):
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})

            pkg_id = int(path.split('/')[4])
            body = self.read_json_body()
            target_pkg = next((p for p in db.get('packages', []) if p['id'] == pkg_id), None)
            if not target_pkg:
                return self.send_json(404, {"success": False, "message": "الباقة غير موجودة"})

            if 'name' in body and body['name'].strip():
                target_pkg['name'] = body['name'].strip()
            if 'price' in body:
                target_pkg['price'] = float(body['price'])
            if 'cost_price' in body:
                target_pkg['cost_price'] = float(body['cost_price'])
            if 'duration' in body:
                target_pkg['duration'] = body['duration'].strip()
            if 'data_limit' in body:
                target_pkg['data_limit'] = body['data_limit'].strip()
            if 'description' in body:
                target_pkg['description'] = body['description'].strip()
            if 'is_active' in body:
                target_pkg['is_active'] = bool(body['is_active'])

            save_db(db)
            return self.send_json(200, {"success": True, "message": "تم تحديث بيانات الباقة بنجاح", "package": target_pkg})

        # 10. إضافة مستخدم جديد من الإدارة
        elif path == '/api/admin/users':
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            body = self.read_json_body()
            next_id = max([u['id'] for u in db.get('users', [])] or [0]) + 1
            new_u = {
                "id": next_id,
                "full_name": body.get('full_name', '').strip(),
                "username": body.get('username', '').strip(),
                "phone": body.get('phone', '').strip(),
                "email": body.get('email', '').strip().lower(),
                "password": body.get('password', '').strip(),
                "role": body.get('role', 'customer'),
                "balance": float(body.get('balance', 0)),
                "network_id": int(body.get('network_id')) if body.get('network_id') else None,
                "is_verified": True,
                "created_at": datetime.now().isoformat()
            }
            db.setdefault('users', []).append(new_u)
            save_db(db)
            return self.send_json(200, {"success": True, "message": "تم إنشاء المستخدم بنجاح", "user": new_u})

        # 11. إضافة شبكة جديدة
        elif path == '/api/admin/networks':
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            body = self.read_json_body()
            next_id = max([n['id'] for n in db.get('networks', [])] or [0]) + 1
            new_net = {
                "id": next_id,
                "name": body.get('name', '').strip(),
                "code": f"net_{int(time.time())}",
                "logo_icon": body.get('logo_icon', 'fa-wifi'),
                "description": body.get('description', '').strip(),
                "login_url": body.get('login_url', '').strip(),
                "is_active": True,
                "created_at": datetime.now().isoformat()
            }
            db.setdefault('networks', []).append(new_net)
            save_db(db)
            return self.send_json(200, {"success": True, "message": "تمت إضافة الشبكة بنجاح", "network": new_net})

        # 12. إضافة بنك جديد
        elif path == '/api/admin/banks':
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            body = self.read_json_body()
            next_id = max([b['id'] for b in db.get('bank_accounts', [])] or [0]) + 1
            new_bank = {
                "id": next_id,
                "bank_name": body.get('bank_name', '').strip(),
                "account_number": body.get('account_number', '').strip(),
                "account_name": body.get('account_name', '').strip(),
                "instructions": body.get('instructions', '').strip(),
                "logo_icon": "fa-building-columns",
                "is_active": True
            }
            db.setdefault('bank_accounts', []).append(new_bank)
            save_db(db)
            return self.send_json(200, {"success": True, "message": "تمت إضافة الحساب البنكي بنجاح", "bank": new_bank})

        # 13. فحص اتصال بوت تلجرام
        elif path == '/api/admin/test-telegram':
            if not user or user.get('role') != 'admin':
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            ok, resp = send_telegram_deposit_notification(
                customer_name="فحص تجريبي من المدير العام",
                amount=5000,
                bank_name="بنك الكريمي - فحص النظام",
                deposit_id=9999,
                image_path=None,
                notes="هذه رسالة اختبار للتأكد من ربط البوت بالكامل"
            )
            if ok: return self.send_json(200, {"success": True, "message": "تم إرسال رسالة الاختبار بنجاح إلى @Croati_Bot"})
            else: return self.send_json(500, {"success": False, "message": f"فشل الإرسال: {resp}"})

        # 14. إضافة باقة جديدة لشبكة
        elif path == '/api/network-owner/packages':
            if not user or (user.get('role') != 'network_owner' and user.get('role') != 'admin'):
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            body = self.read_json_body()
            net_id = int(body.get('network_id')) if body.get('network_id') else (user.get('network_id') or 1)
            next_id = max([p['id'] for p in db.get('packages', [])] or [0]) + 1
            price_val = float(body.get('price', 100))
            cost_val = float(body.get('cost_price', round(price_val * 0.85)))
            new_pkg = {
                "id": next_id,
                "network_id": net_id,
                "name": body.get('name', '').strip(),
                "price": price_val,
                "cost_price": cost_val,
                "duration": body.get('duration', 'غير محدد').strip(),
                "data_limit": body.get('data_limit', 'غير محدد').strip(),
                "description": body.get('description', '').strip(),
                "is_active": True
            }
            db.setdefault('packages', []).append(new_pkg)
            save_db(db)
            return self.send_json(200, {"success": True, "message": "تمت إضافة الباقة بنجاح", "package": new_pkg})

        # 15. توليد أو رفع كروت للمخزون
        elif path == '/api/network-owner/cards/bulk':
            if not user or (user.get('role') != 'network_owner' and user.get('role') != 'admin'):
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            body = self.read_json_body()
            net_id = user.get('network_id') or 1
            pkg_id = int(body.get('package_id', 1))
            method = body.get('method', 'auto')
            count = min(max(int(body.get('generate_count', 10)), 1), 200)
            prefix = body.get('prefix', 'SN')
            pins_text = body.get('pins_text', '')

            pkg = next((p for p in db.get('packages', []) if p['id'] == pkg_id), None)
            if not pkg:
                return self.send_json(400, {"success": False, "message": "الباقة غير موجودة"})

            next_card_id = max([c['id'] for c in db.get('cards', [])] or [0]) + 1
            added = []

            if method == 'auto':
                for i in range(count):
                    pin = f"{10000000 + (next_card_id * 31415 + i * 17) % 89999999}"
                    serial = f"{prefix}-{pkg['id']}-{10000 + next_card_id}"
                    card = {
                        "id": next_card_id,
                        "package_id": pkg['id'],
                        "network_id": net_id,
                        "pin_code": pin,
                        "serial_number": serial,
                        "status": "available",
                        "buyer_id": None,
                        "purchased_at": None,
                        "created_at": datetime.now().isoformat()
                    }
                    db.setdefault('cards', []).append(card)
                    added.append(card)
                    next_card_id += 1
            else:
                lines = [l.strip() for l in pins_text.split('\n') if l.strip()]
                for idx, line in enumerate(lines):
                    parts = line.split(',')
                    pin = parts[0].strip()
                    serial = parts[1].strip() if len(parts) > 1 else f"{prefix}-{pkg['id']}-{10000 + next_card_id}"
                    card = {
                        "id": next_card_id,
                        "package_id": pkg['id'],
                        "network_id": net_id,
                        "pin_code": pin,
                        "serial_number": serial,
                        "status": "available",
                        "buyer_id": None,
                        "purchased_at": None,
                        "created_at": datetime.now().isoformat()
                    }
                    db.setdefault('cards', []).append(card)
                    added.append(card)
                    next_card_id += 1

            save_db(db)
            return self.send_json(200, {
                "success": True,
                "message": f"تمت إضافة {len(added)} كرت بنجاح إلى مخزون ({pkg['name']}).",
                "count": len(added)
            })

        return self.send_json(404, {"success": False, "message": "مسار غير معروف"})

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        user, _ = self.get_session_user()
        db = load_db()

        if path.startswith('/api/network-owner/cards/'):
            if not user or (user.get('role') != 'network_owner' and user.get('role') != 'admin'):
                return self.send_json(403, {"success": False, "message": "صلاحية غير مصرح بها"})
            card_id = int(path.split('/')[4])
            card = next((c for c in db.get('cards', []) if c['id'] == card_id), None)
            if not card: return self.send_json(404, {"success": False, "message": "الكرت غير موجود"})
            if card.get('status') == 'sold': return self.send_json(400, {"success": False, "message": "لا يمكن حذف كرت مباع"})

            db['cards'] = [c for c in db['cards'] if c['id'] != card_id]
            save_db(db)
            return self.send_json(200, {"success": True, "message": "تم حذف الكرت من المخزون"})

        return self.send_json(404, {"success": False, "message": "مسار غير معروف"})

    def serve_static_files(self, path):
        clean_path = path.lstrip('/')
        file_path = None

        if path == '/' or not clean_path:
            for candidate in [os.path.join(PUBLIC_DIR, 'index.html'), os.path.join(BASE_DIR, 'index.html')]:
                if os.path.exists(candidate):
                    file_path = candidate
                    break
        else:
            for candidate in [os.path.join(PUBLIC_DIR, clean_path), os.path.join(BASE_DIR, clean_path)]:
                if os.path.exists(candidate) and not os.path.isdir(candidate):
                    file_path = candidate
                    break

        if not file_path or not os.path.exists(file_path):
            for candidate in [os.path.join(PUBLIC_DIR, 'index.html'), os.path.join(BASE_DIR, 'index.html')]:
                if os.path.exists(candidate):
                    file_path = candidate
                    break

        if not file_path or not os.path.exists(file_path):
            return self.send_json(404, {"error": "File not found"})

        ext = os.path.splitext(file_path)[1].lower()
        mime_types = {
            '.html': 'text/html; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.json': 'application/json; charset=utf-8',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.webp': 'image/webp',
            '.svg': 'image/svg+xml',
            '.ico': 'image/x-icon'
        }
        content_type = mime_types.get(ext, 'application/octet-stream')

        try:
            with open(file_path, 'rb') as f:
                content = f.read()

            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Server Error")

    def parse_multipart(self, body_bytes, boundary):
        fields = {}
        files = {}
        boundary_bytes = f"--{boundary}".encode('utf-8')
        parts = body_bytes.split(boundary_bytes)

        for part in parts:
            if not part or part == b'--\r\n' or part == b'--':
                continue
            if b'\r\n\r\n' not in part:
                continue
            
            header_part, content_part = part.split(b'\r\n\r\n', 1)
            if content_part.endswith(b'\r\n'):
                content_part = content_part[:-2]
            
            header_str = header_part.decode('utf-8', errors='ignore')
            name = None
            filename = None
            for line in header_str.split('\r\n'):
                if 'Content-Disposition:' in line:
                    for token in line.split(';'):
                        token = token.strip()
                        if token.startswith('name='):
                            name = token.split('=', 1)[1].strip('"\'')
                        elif token.startswith('filename='):
                            filename = token.split('=', 1)[1].strip('"\'')
            
            if name:
                if filename:
                    files[name] = {'filename': filename, 'content': content_part}
                else:
                    fields[name] = content_part.decode('utf-8', errors='ignore')

        return fields, files

def run():
    load_db()
    server_address = ('', PORT)
    httpd = ThreadedHTTPServer(server_address, RequestHandler)
    print("=" * 60)
    print(f"🚀 خادم محفظة كروت الواي فاي (اليمن) يعمل الآن بنجاح على المنفذ {PORT}")
    print(f"📍 الرابط المحلي: http://localhost:{PORT}")
    print(f"📱 البوت المربوط: @Croati_Bot (Chat ID: {CHAT_ID})")
    print(f"👑 حساب المدير العام: مشعل المريسي (Moshal380.)")
    print("=" * 60)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nتم إيقاف الخادم.")

if __name__ == '__main__':
    run()
