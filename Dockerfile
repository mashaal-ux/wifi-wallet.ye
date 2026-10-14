FROM python:3.10-slim

WORKDIR /app

# نسخ ملف المتطلبات وتثبيتها أولاً
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# نسخ جميع ملفات المشروع
COPY . /app

# تهيئة المنفذ
EXPOSE 3000

ENV PORT=3000
ENV PYTHONUNBUFFERED=1

# أمر تشغيل الخادم
CMD ["python", "server.py"]
