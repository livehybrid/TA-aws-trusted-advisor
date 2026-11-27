# Squid forward proxy for the proxy tests only; never part of the add-on.
FROM alpine:3.21@sha256:bc21487101ae47172509130ee1092cc273a831968d64b617776d1f69c81e42ab
RUN apk add --no-cache squid~=6.12
COPY squid.conf /etc/squid/squid.conf
COPY e2e-passwd /etc/squid/e2e-passwd
USER squid
EXPOSE 3128 3129
HEALTHCHECK --interval=10s --timeout=3s --retries=20 CMD nc -z 127.0.0.1 3128 && nc -z 127.0.0.1 3129
CMD ["squid", "-N", "-d1", "-f", "/etc/squid/squid.conf"]
