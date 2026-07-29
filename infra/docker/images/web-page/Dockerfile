FROM nginx:1.27-alpine

COPY web /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

RUN mkdir -p /release && printf '{"version":"local"}\n' > /release/release-manifest.json

EXPOSE 8080
