# Sistema de Parqueadero por Fichas

Flujo operativo confirmado: **no se imprime nada**. El conductor recibe una ficha física escaneable; el operador la escanea para registrar ingreso, consultar ubicación y registrar salida.

## Flujo de trabajo

1. El operador inicia sesión con usuario LMS/operador autorizado.
2. En **Ingreso con ficha**, escanea la ficha física.
3. Registra placa, ruta, ubicación y doka/dock.
4. El sistema asocia esa ficha al vehículo y muestra en grande la ubicación para indicársela al conductor.
5. Si otra persona recibe el vehículo, entra a **Consultar ubicación**, escanea la ficha y el sistema muestra ubicación + doka/dock.
6. Al salir, el conductor entrega la ficha.
7. En **Salida con ficha**, el operador escanea la ficha, confirma la salida y la ficha queda libre para reutilizarse.

## Datos que guarda

- Código de ficha
- Placa
- Ruta
- Ubicación
- Doka/Dock
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

Mientras no exista `OIDC_CLIENT_ID`, el botón MELI queda deshabilitado y se puede usar el login local de desarrollo. En producción se recomienda `LOCAL_LOGIN_ENABLED=false` para que el acceso sea solo corporativo, igual que una app protegida por AppSheet.
