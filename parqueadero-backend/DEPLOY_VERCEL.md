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

APP_ROLE_DEFAULT_PASSWORD=clave_segura_para_roles
# O, si cada rol tiene clave diferente:
# APP_ROLE_PASSWORDS_JSON={"Conductor":"...","Coordinador MLP":"...","Operación MELI":"..."}

GOOGLE_SHEET_ID=1UM29RveA97jOkbKFifBNDhRsD4GTlltD2DHyF1xwMLA
GOOGLE_SHEET_NAME=CONTROL ARRIBOS
GOOGLE_ROLES_SHEET_NAME=Lista_Roles
GOOGLE_FLASH_SHEET_NAME=Flash_Parking
GOOGLE_FLASH_WEBHOOK_URL=
GOOGLE_FLASH_WEBHOOK_SECRET=
GOOGLE_FLASH_LOG_REQUIRED=false
GOOGLE_SERVICE_ACCOUNT_JSON_B64=base64_del_json_service_account
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

## Acceso a Google Sheets

El documento está privado por defecto. Para producción, crea una **Google Service Account**, comparte el Sheet con su `client_email` y guarda el JSON como `GOOGLE_SERVICE_ACCOUNT_JSON_B64` en Vercel.

- Para leer `CONTROL ARRIBOS`, la service account necesita acceso de lectura.
- Para escribir cada ingreso/salida en `Flash_Parking`, la service account necesita permiso **Editor**.
- `GOOGLE_FLASH_LOG_REQUIRED=false` permite que la app siga operando aunque Sheets no esté disponible. Cambia a `true` solo si quieres que la escritura en `Flash_Parking` sea obligatoria.

Ejemplo local para obtener el base64:

```bash
base64 -i service-account.json
```
