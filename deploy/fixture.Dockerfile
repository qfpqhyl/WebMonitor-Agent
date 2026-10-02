FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /fixture
COPY tests/fixtures/site.py /fixture/site.py
COPY tests/fixtures/static /fixture/static
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=5 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"]
CMD ["python", "/fixture/site.py"]
