# Despliegue de Park Time Bogotá en Vercel

Esta carpeta (`parqueadero-backend`) ya quedó preparada para desplegar FastAPI en Vercel.

## Importante antes de producción

Vercel ejecuta el backend como funciones serverless. Por eso **no uses SQLite local en producción**: el archivo `parqueadero.db` no debe desplegarse ni se debe confiar en él para persistencia.

Usa PostgreSQL externo, por ejemplo:

- Vercel Postgres / marketplace
- Neon
- Supabase
- Railway PostgreSQL
- Cualquier PostgreSQL administrado

## Archivos agregados

- `api/index.py`: entrada para Vercel Functions.
- `vercel.json`: enruta todo a FastAPI y excluye archivos locales como `.venv` y `.db`.
- `.python-version`: fija Python 3.12.

## Variables de entorno requeridas en Vercel

Configura estas variables en Project Settings → Environment Variables:

```text
APP_TIMEZONE=America/Bogota
JWT_SECRET=un_valor_largo_y_seguro
DATABASE_URL=postgresql://usuario:password@host:puerto/db
PUBLIC_BASE_URL=https://TU-PROYECTO.vercel.app

OIDC_ISSUER=https://auth-meli.adminml.com
OIDC_CLIENT_ID=client_id_entregado_por_okta
OIDC_CLIENT_SECRET=si_okta_lo_entrega
OIDC_SCOPES=openid profile email groups

LOCAL_LOGIN_ENABLED=false
OIDC_ADMIN_USERS=usuario.admin@empresa.com
OIDC_ADMIN_GROUPS=
```

Para pruebas temporales puedes usar:

```text
LOCAL_LOGIN_ENABLED=true
ADMIN_USERNAME=admin@empresa.com
ADMIN_PASSWORD=UnaClaveTemporalSegura
```

## Redirect URI para Okta/MELI

Cuando tengas el dominio de Vercel, registra en Okta/MELI esta Redirect URI:

```text
https://TU-PROYECTO.vercel.app/api/auth/oidc/callback
```

También puedes registrar el ambiente local si lo necesitas:

```text
http://127.0.0.1:8000/api/auth/oidc/callback
```

## Despliegue con Vercel CLI

Desde esta carpeta:

```bash
cd parqueadero-backend
vercel login
vercel
vercel --prod
```

Si prefieres GitHub:

1. Sube el proyecto a un repositorio.
2. En Vercel, importa el repo.
3. Configura como root directory: `parqueadero-backend`.
4. Agrega las variables de entorno.
5. Deploy.

## Verificación rápida

Después del deploy abre:

```text
https://TU-PROYECTO.vercel.app/api/auth/settings
```

Debe mostrar:

```json
{
  "provider": "MELI/Okta",
  "oidc_enabled": true,
  "callback_url": "https://TU-PROYECTO.vercel.app/api/auth/oidc/callback"
}
```

Si `oidc_enabled` sale `false`, falta `OIDC_CLIENT_ID`.
