FROM odoo:18.0

USER root

RUN apt-get update && apt-get install -y \
    python3-ldap \
    libldap2-dev \
    libsasl2-dev \
    libssl-dev \
    libmagic1 \
    fonts-liberation \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages html2text python-magic

RUN printf '#!/bin/bash\nexec "$@"\n' > /entrypoint.sh && chmod +x /entrypoint.sh

RUN sed -i "s/'postgres'/'nobody_dummy'/g" /usr/lib/python3/dist-packages/odoo/cli/server.py 2>/dev/null || true
RUN sed -i 's/"postgres"/"nobody_dummy"/g' /usr/lib/python3/dist-packages/odoo/cli/server.py 2>/dev/null || true
RUN sed -i "s/'postgres'/'nobody_dummy'/g" /usr/lib/python3/dist-packages/odoo/tools/config.py 2>/dev/null || true
RUN sed -i 's/"postgres"/"nobody_dummy"/g' /usr/lib/python3/dist-packages/odoo/tools/config.py 2>/dev/null || true
RUN grep -l -r "security risk" /usr/ /etc/ 2>/dev/null | xargs -r sed -i "s/'postgres'/'nobody_dummy'/g" 2>/dev/null || true

RUN mkdir -p /var/lib/odoo && chown -R odoo:odoo /var/lib/odoo

COPY custom_addons /mnt/extra-addons
COPY odoo.conf /etc/odoo/odoo.conf

ENV PORT=8069
EXPOSE 8069

USER odoo

CMD ["sh", "-c", "odoo --config=/etc/odoo/odoo.conf --db_host=\"$PGHOST\" --db_port=\"$PGPORT\" --db_user=\"$PGUSER\" --db_password=\"$PGPASSWORD\" --http-interface=0.0.0.0 --http-port=8069 -d odoomain -u base,muk_web_theme,muk_web_appsbar"]