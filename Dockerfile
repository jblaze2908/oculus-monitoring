FROM python:3.12-alpine3.21
RUN adduser -D -H app
WORKDIR /app
COPY server.py index.html ./
USER app
EXPOSE 8080
CMD ["python", "server.py"]
