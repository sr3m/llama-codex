# switch-codex

Un solo script de Python para usar modelos de OpenAI y llama.cpp en Codex, con un menú de Terminal y un proveedor unificado para el selector de modelos.

**Proyecto comunitario y experimental.** No está afiliado con OpenAI ni con llama.cpp. La integración del catálogo y del escritorio depende de la versión instalada de Codex. El script comprueba el catálogo con el lector de tu propia instalación antes de activarlo; una actualización de Codex puede requerir adaptar el script.

## Qué hace

- Menú interactivo para elegir modelos, actualizar el catálogo y gestionar el servidor local.
- Enrutador HTTP en `127.0.0.1:9932` que mantiene un único proveedor, `codex-unified`.
- Modelos locales descubiertos en llama.cpp (`127.0.0.1:9931/v1/models`) y en perfiles `llama-*.config.toml`.
- Catálogo combinado que el enrutador actualiza cada 10 segundos. La interfaz de Codex controla cuándo vuelve a consultarlo.
- Tras activar el proveedor y reabrir Codex una vez, permite elegir modelos locales y de OpenAI desde el selector del mismo chat, sin reiniciar por cada cambio.
- Copias de seguridad de la configuración y SQLite antes de migrar los chats existentes. La migración unificada cambia el proveedor del índice y del encabezado del historial; conserva los modelos de cada chat y los mensajes.
- Adaptación de mensajes y notificaciones de herramientas de Desktop al formato Responses de llama.cpp.

La continuidad conserva el historial visible que Codex reenvía. Cada modelo tiene su límite de contexto, capacidad de herramientas y calidad de respuesta; no se transfiere estado interno, caché KV ni razonamiento cifrado de otro servicio.

## Requisitos

- macOS para el servicio automático mediante LaunchAgent.
- **Python 3.14 recomendado para Desktop:** incluye el descompresor `zstd` usado por algunas solicitudes. Python 3.11–3.13 permite ejecutar el menú y las pruebas, pero no acepta solicitudes comprimidas con zstd.
- Codex con sesión iniciada en ChatGPT y una CLI instalada que admita `debug models --bundled` y `debug models`. El script busca la CLI en las apps instaladas y en `PATH`; puedes indicar una ruta con `CODEX_CLI_BIN`.
- Para modelos locales, una versión de llama.cpp con `/v1/responses`, servida en `127.0.0.1:9931`. No basta con que ofrezca `/v1/chat/completions`.

La ruta cloud usa la sesión que Codex entrega al proveedor y el servicio de Codex de ChatGPT. Esta versión no implementa autenticación mediante una API key de OpenAI ni un proveedor cloud alternativo.

## Instalación

Clona el repositorio y revisa el script antes de instalarlo:

```bash
git clone https://github.com/sr3m/switch-codex.git
cd switch-codex
python3 switch-codex.py --help
python3 switch-codex.py install
```

`install` copia únicamente el script a `~/.codex/switch-codex.py`, respalda una copia anterior y crea un `config.toml` vacío solo si no existe. No migra conversaciones. Respeta `CODEX_HOME` si usas otro directorio.

Inicia llama.cpp con tus modelos y el puerto indicado. Si utilizas el ejecutable `llama` con subcomando `serve`, el menú puede iniciarlo automáticamente. También acepta un preset opcional en `~/.codex/llamacpp-codex.ini`. Con `llama-server`, inicia el servidor manualmente con tus argumentos de modelo habituales y `--host 127.0.0.1 --port 9931`.

## Activar el selector combinado

Termina los turnos en curso y cierra Codex para completar la migración inicial de todos los chats:

```bash
python3 ~/.codex/switch-codex.py unified
```

Después abre Codex. Elige los modelos desde su selector: los locales llevan el sufijo `· Local`. **No hace falta reiniciar la app por cada cambio de modelo.** La primera activación sí requiere reabrirla. Si había chats bloqueados por una instancia de Codex, el script los deja pendientes; cierra esa instancia y repite `unified`.

El gateway queda registrado como LaunchAgent del usuario y se inicia al entrar en macOS. Si no hay servidor ni perfiles locales, puedes activar solo el catálogo cloud y añadir los modelos locales después.

## Uso cotidiano

```bash
# Menú interactivo
python3 ~/.codex/switch-codex.py

# Actualizar y validar el catálogo combinado
python3 ~/.codex/switch-codex.py hybrid-catalog

# Ver estado sin mostrar claves
python3 ~/.codex/switch-codex.py hybrid-status

# Cambiar el modelo predeterminado para sesiones futuras
python3 ~/.codex/switch-codex.py local --model 'ID-DEL-MODELO-LOCAL'
python3 ~/.codex/switch-codex.py cloud --model 'ID-DEL-MODELO-CLOUD'

# CLI: usar el mismo proveedor configurado con un modelo explícito
codex -m 'ID-DEL-MODELO'
```

En un chat abierto, cambia el modelo en el selector de Codex; cambiar el predeterminado por Terminal no obliga a ese chat a cambiar de modelo.

Al encender llama.cpp, el gateway consulta los modelos cada 10 segundos. Para forzar una actualización, usa `hybrid-catalog` y abre de nuevo el selector. Codex puede mantener una caché propia del listado; el script no fuerza una actualización de la interfaz de la app.

También puedes declarar modelos sin mantener el servidor encendido. Ejemplo de `~/.codex/llama-mi-modelo.config.toml`:

```toml
model_provider = "llamacpp"
model = "mi-modelo"
model_context_window = 32768
```

Usa el identificador exacto que expone llama.cpp y un contexto que tu servidor y modelo puedan atender. Los perfiles anuncian modelos; no descargan ni cargan sus pesos por sí solos.

## Configuración y privacidad

El repositorio contiene solo código, pruebas sintéticas y documentación. Durante el uso, todos los datos de funcionamiento se crean fuera del repositorio, en `CODEX_HOME` (por defecto `~/.codex`):

| Archivo | Uso |
| --- | --- |
| `config.toml` | Proveedor, modelo predeterminado y clave privada del gateway |
| `unified/settings.json` | Clave aleatoria del gateway y estado de activación |
| `unified/catalog.json` | Catálogo combinado y rutas por modelo |
| `unified/cloud-models.json` | Catálogo cloud obtenido en tu instalación |
| `unified/routes.jsonl` | Estado HTTP, ruta local/cloud, modelo, tiempos y tipos de herramientas |
| `backups/` | Respaldos locales de configuración, SQLite e historiales modificados |

El gateway escucha solo en loopback, exige una clave para acceder a los modelos y rechaza solicitudes con cabecera `Origin`. Envía las credenciales de ChatGPT únicamente al upstream cloud fijo; no las envía a llama.cpp. No lee `auth.json`. No registra prompts, respuestas, tokens de autenticación ni cabeceras. El registro sí contiene identificadores de modelos; trátalo como información local.

No hay cambio automático entre local y cloud si falla un servicio. Elegir cloud envía a OpenAI el contexto de conversación que Codex incluya en la solicitud, aunque parte de ese chat se haya producido con un modelo local.

No subas tu directorio `.codex`, configuraciones, catálogos generados, bases de datos, respaldos ni logs a GitHub. El `.gitignore` incluido reduce errores comunes; no sustituye una revisión de lo que publicas. El proyecto no incluye direcciones de redes privadas ni requiere Tailscale.

## Volver al acceso directo a OpenAI

```bash
python3 ~/.codex/switch-codex.py restore
```

Este comando migra los chats elegibles al proveedor directo de OpenAI y cambia la configuración, sin reemplazar SQLite por una copia antigua. Reabre Codex para aplicarlo. Conserva los respaldos y deja el servicio instalado por si vuelves a activarlo. Para descargarlo de launchd:

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.github.switch-codex.gateway.plist"
```

Después puedes eliminar ese archivo `.plist` para que no se inicie en el próximo inicio de sesión. No elimines tus historiales ni tu base de datos.

## Modo directo anterior

Se conservan los comandos `local`, `cloud`, `catalog`, `watch`, `start`, `stop` y `status`. Si el proveedor activo no es `codex-unified`, `local`/`cloud` realizan el switch anterior, que sincroniza proveedor y modelo en los chats y requiere reabrir Desktop. Para ese modo, el proveedor `llamacpp` debe estar definido con `base_url = "http://127.0.0.1:9931/v1"` y `wire_api = "responses"`; para cloud, indica `--model` o crea `openai-cloud.config.toml`.

Para alternar en el selector sin reinicios, activa `unified` primero.

## Pruebas

```bash
python3 tests/run.py
```

Las pruebas usan directorios temporales, una base SQLite sintética y servidores HTTP simulados en loopback. No leen tu autenticación, no cambian tus conversaciones y no envían solicitudes a OpenAI ni a un modelo real. Comprueban el límite de credenciales, transmisión SSE, errores upstream, normalización de mensajes, rollback y conservación de datos de los chats.

La compatibilidad real depende de las versiones de Codex, llama.cpp y del modelo. Si informas un error, incluye las versiones y un ejemplo sintético. Retira IPs, nombres de host, rutas personales y credenciales de cualquier archivo o captura.

## Licencia

MIT. Consulta [LICENSE](LICENSE).
