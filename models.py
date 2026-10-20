#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
نماذج قاعدة البيانات (Database Models) باستخدام SQLAlchemy
متوافقة بنسبة 100% مع PostgreSQL و SQLite
"""

from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(60), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=True, index=True)
    phone = db.Column(db.String(30), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), default='client', nullable=False) # 'admin', 'network_owner', 'client'/'customer'
    balance = db.Column(db.Float, default=0.0, nullable=False)
    network_id = db.Column(db.Integer, db.ForeignKey('networks.id', ondelete='SET NULL'), nullable=True)
    is_verified = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    orders = db.relationship('Order', backref='buyer', cascade='all, delete-orphan', lazy=True)
    deposits = db.relationship('Deposit', backref='user', cascade='all, delete-orphan', lazy=True)
    owned_networks = db.relationship('Network', foreign_keys='Network.owner_id', backref='owner', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        # Support both hashed passwords and legacy plain text gracefully
        if self.password_hash.startswith(('scrypt:', 'pbkdf2:')):
            return check_password_hash(self.password_hash, password)
        return self.password_hash == password

    def to_dict(self):
        # Standardize role for frontend compatibility ('client' or 'customer')
        norm_role = 'customer' if self.role in ['client', 'customer'] else self.role
        network_data = None
        if self.network_id and hasattr(self, 'network') and self.network:
            network_data = {"id": self.network.id, "name": self.network.name, "code": self.network.code}

        # Calculate financial summary safely
        try:
            approved_deps = [d for d in (self.deposits or []) if d.status == 'approved']
            total_dep_amt = round(sum(float(d.approved_amount if d.approved_amount is not None else (d.amount or 0)) for d in approved_deps), 2)
            total_bonus = round(sum(float(d.bonus_amount or 0) for d in approved_deps), 2)
            user_orders = self.orders or []
            cards_bought = len(user_orders)
            total_spent = round(sum(float(o.amount or 0) for o in user_orders), 2)
        except Exception:
            approved_deps = []
            total_dep_amt = 0.0
            total_bonus = 0.0
            cards_bought = 0
            total_spent = 0.0

        return {
            "id": self.id,
            "full_name": self.full_name,
            "username": self.username,
            "email": self.email or "",
            "phone": self.phone or "",
            "role": norm_role,
            "balance": round(float(self.balance or 0), 2),
            "network_id": self.network_id,
            "network": network_data,
            "is_verified": self.is_verified,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "financial_summary": {
                "total_deposits_approved_amount": total_dep_amt,
                "deposits_count": len(approved_deps),
                "total_bonus_received": total_bonus,
                "cards_bought_count": cards_bought,
                "total_cards_spent": total_spent
            }
        }

class Network(db.Model):
    __tablename__ = 'networks'

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(50), unique=True, nullable=False)
    logo_icon = db.Column(db.String(50), default='fa-wifi')
    description = db.Column(db.Text, nullable=True)
    login_url = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    packages = db.relationship('CardPackage', backref='network', cascade='all, delete-orphan', lazy=True)
    cards = db.relationship('Card', backref='network', cascade='all, delete-orphan', lazy=True)
    assigned_users = db.relationship('User', foreign_keys='User.network_id', backref='network', lazy=True)

    def to_dict(self, include_packages=True, current_user=None, include_inactive_packages=False):
        data = {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "owner_id": self.owner_id,
            "logo_icon": self.logo_icon or 'fa-wifi',
            "description": self.description or '',
            "login_url": self.login_url or '',
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else ''
        }
        if include_packages:
            if include_inactive_packages:
                data["packages"] = [p.to_dict(current_user=current_user) for p in self.packages]
            else:
                data["packages"] = [p.to_dict(current_user=current_user) for p in self.packages if p.is_active]
        return data

class CardPackage(db.Model):
    __tablename__ = 'card_packages'

    id = db.Column(db.Integer, primary_key=True)
    network_id = db.Column(db.Integer, db.ForeignKey('networks.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    cost_price = db.Column(db.Float, default=0.0, nullable=True)
    duration = db.Column(db.String(50), nullable=True)
    data_limit = db.Column(db.String(50), nullable=True)
    description = db.Column(db.String(255), nullable=True)
    has_free_card = db.Column(db.Boolean, default=False, nullable=True)
    free_card_duration = db.Column(db.String(100), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationship to cards
    cards = db.relationship('Card', backref='package', cascade='all, delete-orphan', lazy=True)

    def to_dict(self, current_user=None):
        avail_count = Card.query.filter_by(package_id=self.id, status='available').count()
        sold_count = Card.query.filter_by(package_id=self.id, status='sold').count()
        is_priv = current_user and current_user.role in ['admin', 'network_owner']
        price_val = round(float(self.price or 0), 2)
        raw_cost = getattr(self, 'cost_price', None)
        cost_val = round(float(raw_cost if raw_cost is not None and raw_cost > 0 else price_val * 0.85), 2)
        has_free = bool(getattr(self, 'has_free_card', False))
        free_dur = getattr(self, 'free_card_duration', '') or ''
        return {
            "id": self.id,
            "network_id": self.network_id,
            "name": self.name,
            "price": price_val,
            "cost_price": cost_val,
            "duration": self.duration or '',
            "data_limit": self.data_limit or '',
            "description": self.description or '',
            "has_free_card": has_free,
            "free_card_duration": free_dur,
            "is_active": self.is_active,
            "is_available": avail_count > 0,
            "available_count": avail_count if is_priv else (1 if avail_count > 0 else 0),
            "sold_count": sold_count if is_priv else 0
        }

class Card(db.Model):
    __tablename__ = 'cards'

    id = db.Column(db.Integer, primary_key=True)
    network_id = db.Column(db.Integer, db.ForeignKey('networks.id', ondelete='CASCADE'), nullable=False, index=True)
    package_id = db.Column(db.Integer, db.ForeignKey('card_packages.id', ondelete='CASCADE'), nullable=False, index=True)
    pin_code = db.Column(db.String(100), unique=True, nullable=False, index=True) # Unique card PIN
    serial_number = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default='available', nullable=False, index=True) # 'available', 'sold'
    buyer_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    purchased_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        pkg = self.package
        net = self.network
        price_val = round(float(pkg.price or 0), 2) if pkg else 0.0
        raw_cost = getattr(pkg, 'cost_price', None) if pkg else None
        cost_val = round(float(raw_cost if raw_cost is not None and raw_cost > 0 else price_val * 0.85), 2)
        profit_val = round(price_val - cost_val, 2)
        buyer_user = User.query.get(self.buyer_id) if self.buyer_id else None
        has_free = bool(getattr(pkg, 'has_free_card', False)) if pkg else False
        free_dur = (getattr(pkg, 'free_card_duration', '') or '') if pkg else ''
        return {
            "id": self.id,
            "network_id": self.network_id,
            "package_id": self.package_id,
            "network_name": net.name if net else '',
            "login_url": net.login_url if net else '',
            "package_name": pkg.name if pkg else '',
            "duration": (pkg.duration or '') if pkg else '',
            "data_limit": (pkg.data_limit or '') if pkg else '',
            "has_free_card": has_free,
            "free_card_duration": free_dur,
            "price": price_val,
            "cost_price": cost_val,
            "profit": profit_val,
            "pin_code": self.pin_code,
            "serial_number": self.serial_number or f"SN-{self.id}",
            "status": self.status,
            "buyer_id": self.buyer_id,
            "buyer_name": buyer_user.full_name if buyer_user else '',
            "purchased_at": self.purchased_at.isoformat() if self.purchased_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else ''
        }

class Order(db.Model):
    __tablename__ = 'orders'

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(60), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    card_id = db.Column(db.Integer, db.ForeignKey('cards.id', ondelete='CASCADE'), nullable=False)
    network_id = db.Column(db.Integer, db.ForeignKey('networks.id', ondelete='CASCADE'), nullable=False)
    package_id = db.Column(db.Integer, db.ForeignKey('card_packages.id', ondelete='CASCADE'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='completed', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "order_number": self.order_number,
            "user_id": self.user_id,
            "card_id": self.card_id,
            "network_id": self.network_id,
            "package_id": self.package_id,
            "amount": round(float(self.amount or 0), 2),
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else ''
        }

class Deposit(db.Model):
    __tablename__ = 'deposits'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    customer_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    bank_name = db.Column(db.String(100), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    receipt_img = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default='pending', nullable=False, index=True) # 'pending', 'approved', 'rejected'
    approved_amount = db.Column(db.Float, nullable=True)
    bonus_amount = db.Column(db.Float, nullable=True)
    telegram_message_id = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        u = self.user
        return {
            "id": self.id,
            "user_id": self.user_id,
            "user_username": u.username if u else self.customer_name,
            "customer_name": self.customer_name,
            "phone": self.phone or (u.phone if u else '') or '',
            "bank_name": self.bank_name or '',
            "amount": round(float(self.amount or 0), 2),
            "receipt_image": self.receipt_img or '',
            "receipt_img": self.receipt_img or '',
            "status": self.status,
            "approved_amount": round(float(self.approved_amount), 2) if self.approved_amount is not None else None,
            "bonus_amount": round(float(self.bonus_amount), 2) if self.bonus_amount is not None else None,
            "created_at": self.created_at.isoformat() if self.created_at else '',
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None
        }

class BankAccount(db.Model):
    __tablename__ = 'bank_accounts'

    id = db.Column(db.Integer, primary_key=True)
    bank_name = db.Column(db.String(100), nullable=False)
    account_number = db.Column(db.String(50), nullable=False)
    account_name = db.Column(db.String(100), nullable=False)
    logo_icon = db.Column(db.String(50), default='fa-landmark')
    instructions = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "bank_name": self.bank_name,
            "account_number": self.account_number,
            "account_name": self.account_name,
            "logo_icon": self.logo_icon or 'fa-landmark',
            "instructions": self.instructions or '',
            "is_active": self.is_active
        }

class SessionToken(db.Model):
    __tablename__ = 'session_tokens'

    session_id = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class OtpRecord(db.Model):
    __tablename__ = 'otp_records'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    code = db.Column(db.String(10), nullable=False)
    expires_at = db.Column(db.Float, nullable=False)
