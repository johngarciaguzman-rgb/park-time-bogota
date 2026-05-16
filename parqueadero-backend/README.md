# Sistema de Parqueadero por Fichas

Flujo operativo confirmado: **no se imprime nada**. El conductor recibe una ficha física escaneable; el operador la escanea para registrar ingreso, consultar ubicación y registrar salida.

## Flujo de trabajo

1. El operador inicia sesión con usuario LMS/operador autorizado.
2. En **Ingreso con ficha**, escanea la ficha física.
3. La app valida la ficha/ruta contra la hoja diaria **CONTROL ARRIBOS** de Google Sheets.
4. Si la ficha existe, completa ruta, zona/ubicación, MLP, SPR, ola y EST WTD.
5. Registra placa y cédula.
6. El sistema asocia esa ficha al vehículo y la descuenta de **PENDIENTES 1 ola**.
7. Cada ingreso/salida se envía también a la hoja **Flash_Parking** como bitácora operativa.
8. Si otra persona recibe el vehículo, escanea la ficha y el sistema muestra ubicación + doka/dock.
9. Al salir, el conductor entrega la ficha.
10. En **Salida con ficha**, el operador escanea la ficha, confirma la salida y la ficha queda libre para reutilizarse.

## Datos que guarda

- Código de ficha
- Placa
- Cédula
- Ruta
- Ubicación
- Doka/Dock
- MLP, SPR y Ola WTD cuando viene de CONTROL ARRIBOS
- Conductor opcional
- Observaciones de ingreso/salida
- Hora de ingreso
- Hora de salida
- Duración
- Operador que registró ingreso y salida

## Reglas incluidas

- Una ficha no puede estar asociada a dos vehículos activos.
- Una placa no puede tener dos ingresos activos.
- Una ubicación activa no puede repetirse.
- Una doka/dock activa no puede repetirse.
- La ficha se libera automáticamente al registrar la salida.
- Una fila de CONTROL ARRIBOS solo aparece en pendientes mientras no tenga ingreso asignado.

## Google Sheets: CONTROL ARRIBOS

La app puede sincronizar la hoja diaria `CONTROL ARRIBOS` del documento:

```text
1GG6twSUKAn8LK_t4Q4WK3rMfJsdxRej2FD5pDI9wReU
```

Columnas esperadas:

- `EST WTD`
- `Ruta Sorting`
- `MLP`
- `Zona`
- `SPR`
- `Ola WTD`
- `Disponible` opcional

### Opción recomendada: Service Account

1. Crea una Google Service Account con permiso de lectura a Google Sheets.
2. Comparte el Google Sheet con el `client_email` de esa service account.
3. Exporta el JSON de credenciales y pásalo como variable:

```bash
export GOOGLE_SERVICE_ACCOUNT_JSON_B64="$(base64 -i service-account.json)"
```

### Opción alternativa: CSV público

Publica únicamente la hoja requerida como CSV y configura:

```bash
export GOOGLE_PUBLIC_CSV_URL="https://docs.google.com/spreadsheets/d/.../gviz/tq?tqx=out:csv&sheet=CONTROL%20ARRIBOS"
```

Si la hoja está privada y no hay service account, la app mostrará un error indicando que necesita acceso.

## Google Sheets: Flash_Parking

La hoja `Flash_Parking` funciona como bitácora de movimientos. La app agrega una fila por cada ingreso o salida con estas columnas:

- `ID`
- `Fecha`
- `Ciclo`
- `Dispositivo`
- `Lector QR`
- `Estacionamiento Asignado`
- `Tipo de Movimiento`
- `Hora de registro`

Para escribir en esta hoja, la misma Service Account debe tener permiso **Editor** sobre el archivo de Google Sheets. Si no hay credenciales, la app sigue guardando en la base de datos local/central y omite el envío a Sheets.

## Ejecutar localmente

```bash
cd parqueadero-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
ADMIN_USERNAME=admin@empresa.com \
ADMIN_PASSWORD='cambia-esta-clave' \
JWT_SECRET='coloca-un-valor-secreto-largo' \
uvicorn main:app --reload
```

Abrir en el navegador:

```text
http://127.0.0.1:8000
```

## Variables de entorno

Copiar `.env.example` como referencia:

- `APP_TIMEZONE=America/Bogota`
- `JWT_SECRET`: secreto largo para firmar sesiones.
- `ADMIN_USERNAME`: usuario administrador inicial.
- `ADMIN_PASSWORD`: contraseña inicial segura.
- `DATABASE_URL`: opcional; si no existe, usa SQLite local. En nube se recomienda PostgreSQL.
- `LOCAL_LOGIN_ENABLED`: `true` para pruebas locales, `false` para obligar login MELI/Okta.
- `APP_ROLE_DEFAULT_PASSWORD`: clave única opcional para crear usuarios iniciales por rol.
- `APP_ROLE_PASSWORDS_JSON`: claves por rol en formato JSON, recomendado si cada rol tendrá una clave diferente.
- `APP_ROLE_USERNAMES_JSON`: usernames por rol si quieres usar usuarios corporativos específicos.
- `GOOGLE_SHEET_ID`: ID del Google Sheet diario.
- `GOOGLE_SHEET_NAME`: nombre de hoja; por defecto `CONTROL ARRIBOS`.
- `GOOGLE_FLASH_SHEET_NAME`: hoja de bitácora de movimientos; por defecto `Flash_Parking`.
- `GOOGLE_FLASH_LOG_REQUIRED`: `true` si quieres bloquear ingresos/salidas cuando no se pueda escribir en `Flash_Parking`.
- `GOOGLE_SERVICE_ACCOUNT_JSON_B64`: credenciales de service account en base64.
- `GOOGLE_PUBLIC_CSV_URL`: alternativa CSV público.

## Para varios puestos de trabajo

Todos los puestos deben abrir la misma URL del servidor. La base de datos queda centralizada; por eso todos ven los mismos ingresos, salidas, fichas ocupadas e historial diario.

## LMS/MELI real

La URL `https://auth-meli.adminml.com` responde como proveedor Okta/OpenID Connect. No debe validarse enviando usuario y contraseña a una tabla; el flujo correcto es SSO por navegador.

Configuración requerida:

- `OIDC_ISSUER=https://auth-meli.adminml.com`
- `OIDC_CLIENT_ID`: entregado por el administrador de Okta/MELI.
- `OIDC_CLIENT_SECRET`: solo si la aplicación Okta fue registrada como confidential/web app.
- `OIDC_ADMIN_USERS`: usuarios MELI que deben entrar como admin, separados por coma.
- `OIDC_ADMIN_GROUPS`: grupos Okta/MELI que deben entrar como admin, separados por coma.
- Redirect URI autorizado en Okta para desarrollo local:

```text
http://127.0.0.1:8000/api/auth/oidc/callback
```

## Menú por rol estilo AppSheet

La pantalla inicial permite seleccionar rol y escribir la **Clave**. Cada rol se autentica contra un usuario local/semilla:

- `Conductor` → `conductor@park.local`
- `Coordinador MLP` → `coordinador.mlp@park.local`
- `Operación MELI` → `operacion.meli@park.local`
- `Operador Estacionamiento` → `operador.estacionamiento@park.local`
- `Monitor MLP` → `monitor.mlp@park.local`
- `Torre de Control` → `torre.control@park.local`

Para crearlos automáticamente, configura una clave por entorno con `APP_ROLE_DEFAULT_PASSWORD` o usa `APP_ROLE_PASSWORDS_JSON`. No dejes claves reales escritas en el código ni en el repositorio.

Mientras no exista `OIDC_CLIENT_ID`, el botón MELI queda deshabilitado y se puede usar el login local de desarrollo. En producción se recomienda `LOCAL_LOGIN_ENABLED=false` para que el acceso sea solo corporativo, igual que una app protegida por AppSheet.
