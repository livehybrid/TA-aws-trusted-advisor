# Dante SOCKS server for tests/test_socks.py only; never part of the add-on.
# 1080 takes SOCKS4 and SOCKS5 without authentication, 1081 SOCKS5 with a user
# name and password (e2euser, a system user here: Dante checks it against the
# password database). Each connection it relays is logged to stdout, read by
# the tests with `docker logs`.
FROM alpine:3.21@sha256:bc21487101ae47172509130ee1092cc273a831968d64b617776d1f69c81e42ab
RUN apk add --no-cache dante-server~=1.4.3 \
 && adduser -D -H -s /sbin/nologin e2euser \
 && echo 'e2euser:e2e-p@ss:word' | chpasswd
COPY sockd-open.conf sockd-auth.conf /etc/
EXPOSE 1080 1081
HEALTHCHECK --interval=10s --timeout=3s --retries=20 CMD nc -z 127.0.0.1 1080 && nc -z 127.0.0.1 1081
# Two servers, one per authentication method (the healthcheck watches both).
CMD ["sh", "-c", "sockd -f /etc/sockd-open.conf -p /tmp/open.pid & exec sockd -f /etc/sockd-auth.conf -p /tmp/auth.pid"]
