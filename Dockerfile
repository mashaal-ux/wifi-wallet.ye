FROM python:3.10-slim

WORKDIR /app

# نسخ ملفات المشروع
COPY . /app

# تثبيت الحزم المطلوبة
RUN pip install --no-cache-dir -r requirements.txt

# تهيئة المنفذ
EXPOSE 3000

ENV PORT=3000
ENV PYTHONUNBUFFERED=1

# أمر تشغيل الخادم
CMD ["python", "server.py"]
