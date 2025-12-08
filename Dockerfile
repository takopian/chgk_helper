FROM python:3.10-slim


COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt
# Install locales
RUN apt-get update && \
    apt-get install -y locales locales-all && \
    rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV LANG ru_RU.UTF-8
ENV LANGUAGE ru_RU:ru
ENV LC_ALL ru_RU.UTF-8

COPY src /app
WORKDIR /app

COPY src .

EXPOSE 3000/tcp

CMD ["python3", "-m", "main"]