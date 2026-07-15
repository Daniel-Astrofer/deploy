FROM nginx:1.27-alpine

COPY frontend/build/web /usr/share/nginx/html
COPY infra/runtime/web/nginx.k8s.conf /etc/nginx/conf.d/default.conf

RUN mkdir -p /release && printf '{"version":"local"}\n' > /release/release-manifest.json

EXPOSE 8080
