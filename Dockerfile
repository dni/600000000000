FROM nginx:alpine

COPY ["404.html", "business-cards.html", "favicon.ico", "index.html", "inscriptions.html", "liquid.html", "lore.html", "matrix.html", "members.json", "onchain.html", "ord.html", "ordinals.html", "qrcode-sticker.svg", "signal.html", "sticker.html", "style.css", "/usr/share/nginx/html/"]
COPY .well-known /usr/share/nginx/html/.well-known
COPY blocks /usr/share/nginx/html/blocks
COPY img /usr/share/nginx/html/img
COPY inscriptions /usr/share/nginx/html/inscriptions
COPY vendor /usr/share/nginx/html/vendor

RUN cat > /etc/nginx/conf.d/default.conf <<'EOF'
server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    location ^~ /.well-known {
        add_header Access-Control-Allow-Origin "*" always;
    }

    location ^~ /.well-known/lnurlp {
        default_type "application/json; charset=utf-8";
        add_header Access-Control-Allow-Origin "*" always;
    }

    location / {
        try_files $uri $uri/ =404;
    }
}
EOF

EXPOSE 80
