FROM python:3.12-slim
RUN pip install --no-cache-dir mitmproxy
COPY trace_addon.py /app/trace_addon.py
EXPOSE 8081 8888
ENTRYPOINT ["mitmweb"]
CMD ["--web-host", "0.0.0.0", "--web-port", "8081", "--listen-host", "0.0.0.0", "--listen-port", "8888"]
