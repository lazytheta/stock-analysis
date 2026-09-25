FROM python:3.13-slim

WORKDIR /app

COPY lazytheta-mcp-cloudrun/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Cloud Run handler files
COPY lazytheta-mcp-cloudrun/main.py \
     lazytheta-mcp-cloudrun/mcp_handler.py \
     /app/

# Shared modules from repo root, imported by mcp_server.py's _*_impl chain
COPY mcp_auth.py mcp_server.py valuation_lenses.py \
     config_store.py dcf_calculator.py gather_data.py \
     scorecard_utils.py robustness.py notifications.py \
     t212_api.py quotes.py fx.py aspirant.py moat_cards.py question_cards.py \
     risk_cards.py business_cards.py /app/

EXPOSE 8080
ENV PORT=8080

CMD ["python", "main.py"]
