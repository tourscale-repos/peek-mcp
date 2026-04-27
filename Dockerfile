FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV MCP_TRANSPORT=http
ENV MCP_PORT=8000
ENV FASTMCP_HOST=0.0.0.0

EXPOSE 8000

CMD ["python", "server.py"]
