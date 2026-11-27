FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY retail_copilot ./retail_copilot
COPY warehouse ./warehouse
COPY eval ./eval
RUN python -m retail_copilot.build
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "retail_copilot.api:from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
