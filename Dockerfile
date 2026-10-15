FROM python:3.11-slim

RUN apt-get update && apt-get install -y nginx supervisor curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install flask flask-cors requests pyyaml pdfplumber httpx

# App code
COPY api_server.py chat_prompt.md save_planner.py email_error_poll.py research_collector.py ./

# Static files
COPY *.html ./
COPY assets/ ./assets/   # #10 前端公共层（Docker 路径也要带上，否则页面 404）
COPY *.md ./

# Data
RUN mkdir -p /app/data

# Nginx
COPY nginx-docker.conf /etc/nginx/conf.d/default.conf
RUN rm -f /etc/nginx/sites-enabled/default

# Supervisor
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 80

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
