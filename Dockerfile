FROM python:3.10-slim

WORKDIR /app

# نسخ ملفات المشروع
COPY . /app

# تهيئة المنفذ
EXPOSE 3000

ENV PORT=3000
ENV PYTHONUNBUFFERED=1

# أمر تشغيل الخادم
CMD ["python", "server.py"]
