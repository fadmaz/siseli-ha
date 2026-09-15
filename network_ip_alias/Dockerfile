FROM ghcr.io/home-assistant/base:latest

LABEL \
	io.hass.type="app" \
	io.hass.arch="aarch64|amd64"

RUN apk add --no-cache iproute2

COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]