ARG BASE_TAG=latest
FROM ghcr.io/repotrial/nedrexdb_v2d-base:${BASE_TAG}
RUN apt-get update && apt-get upgrade -y && apt-get autoclean -y && apt-get autoremove -y && apt-get clean -y
RUN mamba install -c conda-forge openjdk=17 -y

WORKDIR /data/nedrex_files/
RUN mkdir -p nedrex_api/static
RUN mkdir -p nedrex_api/data
RUN mkdir -p nedrex_data/downloads

WORKDIR /app/nedrexdb
COPY cron/cron.prod /etc/cron.d/cron-nedrex
RUN chmod 0644 /etc/cron.d/cron-nedrex
RUN crontab /etc/cron.d/cron-nedrex
RUN touch /var/log/nedrexdb.log

COPY . ./
RUN rm -rf cron
RUN pip install .[dependencies]

CMD cron && bash build.sh >> /var/log/nedrexdb.log 2>&1 & tail -f /var/log/nedrexdb.log