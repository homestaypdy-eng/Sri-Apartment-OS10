FROM python:3.12-slim

WORKDIR /app

# Sri Apartment Homestay operates in India; every unqualified date.today()/
# date-boundary computation in this codebase (overdue checks, "arrivals
# today", daily closing, report date ranges, promo/quotation expiry, staff
# attendance) implicitly means "today in Asia/Kolkata", not "today in
# whatever timezone the container happens to boot in". This container's
# base image defaults to UTC, which silently shifts every one of those
# calendar-day boundaries by 5.5 hours (00:00-05:30 IST is still "yesterday"
# in UTC) — verified via a fresh production-readiness audit. Setting TZ here
# (with tzdata installed below, required for glibc to resolve it) fixes
# every existing date.today()/datetime.now() call site correctly without
# touching any of that business logic: only the explicitly UTC-aware
# datetime.now(timezone.utc) calls used for stored timestamps are
# unaffected by this, by design.
ENV TZ=Asia/Kolkata

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc poppler-utils tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
