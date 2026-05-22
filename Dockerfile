FROM debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    g++ make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY WDL/ ./WDL/
COPY ninjam/ ./ninjam/

WORKDIR /build/ninjam/server
RUN make clean 2>/dev/null; make

# --- Runtime stage ---
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    coreutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ninjam

COPY --from=builder /build/ninjam/server/ninjamsrv /usr/local/bin/ninjamsrv
COPY configs/default.cfg /opt/ninjam/server.cfg.tpl
COPY configs/default-2050.cfg /opt/ninjam/server-2050.cfg.tpl
COPY configs/private-2090.cfg.example /opt/ninjam/private-2090.cfg.tpl
COPY configs/cclicense.txt /opt/ninjam/cclicense.txt
COPY configs/motd.txt /opt/ninjam/motd.txt
COPY configs/motd-private.txt /opt/ninjam/motd-private.txt
COPY configs/entrypoint.sh /opt/ninjam/entrypoint.sh

RUN chmod +x /usr/local/bin/ninjamsrv /opt/ninjam/entrypoint.sh

EXPOSE 2049 2050 2090

CMD ["/opt/ninjam/entrypoint.sh"]
