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
COPY configs/default.cfg /opt/ninjam/server.cfg
COPY configs/cclicense.txt /opt/ninjam/cclicense.txt

RUN chmod +x /usr/local/bin/ninjamsrv

EXPOSE 2049

CMD ["stdbuf", "-oL", "ninjamsrv", "server.cfg"]
