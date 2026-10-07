# Tor как прокси для запросов к Transfermarkt.
#   8118 - HTTP-прокси (HTTPTunnelPort): его понимает requests без доп. библиотек
#   9050 - SOCKS5
#   9051 - управляющий порт: парсер шлёт SIGNAL NEWNYM, чтобы сменить выходной IP
# Порты открыты только внутри сети docker compose, наружу не публикуются.
FROM alpine:3.20
RUN apk add --no-cache tor
COPY torrc /etc/tor/torrc
COPY entrypoint.sh healthcheck.sh /
RUN sed -i 's/\r$//' /entrypoint.sh /healthcheck.sh /etc/tor/torrc \
 && chmod +x /entrypoint.sh /healthcheck.sh \
 && mkdir -p /var/lib/tor && chown -R tor /var/lib/tor && chmod 700 /var/lib/tor
EXPOSE 8118 9050 9051
ENTRYPOINT ["/entrypoint.sh"]
