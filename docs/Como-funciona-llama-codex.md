# Cómo construimos llama-codex y cómo funciona

`llama-codex` es un script que configura Codex y puede ejecutar un pequeño servidor intermediario. No es un modelo entrenado ni toma decisiones sobre la dificultad de una pregunta: dirige cada solicitud según el identificador de modelo que eliges.

## El recorrido de una conversación

```text
Tú eliges un modelo en Codex
          │
          ▼
Proveedor codex-unified
http://127.0.0.1:9932
          │
          ├── Modelo local ──► llama.cpp: http://127.0.0.1:9931/v1
          │
          └── Modelo cloud ─► Servicio de Codex de OpenAI
```

`127.0.0.1` es la dirección del propio equipo. No es una IP de Tailscale.

## 1. Primero respaldamos y leímos la configuración

El script trabaja con `config.toml`, el catálogo de modelos y la base `state_N.sqlite` dentro del directorio de Codex. Antes de una migración respalda la configuración y utiliza la API de backup de SQLite, que incluye los datos que aún estén en el WAL.

Esto importa porque copiar solo el archivo SQLite mientras una aplicación escribe puede dejar fuera cambios recientes.

## 2. El primer switch cambiaba el proveedor

La primera solución alternaba entre `openai` y `llamacpp`, actualizando el modelo y los metadatos de los chats. Codex debía volver a cargar la configuración para aplicar el cambio. Por eso requería reabrir Desktop.

## 3. Para alternar desde el selector creamos un proveedor estable

En la solución unificada, Codex utiliza siempre `codex-unified`. Su URL apunta al intermediario de tu Mac. Lo que cambia al elegir un modelo es el destino al que el intermediario envía esa solicitud.

El script mantiene una tabla de rutas equivalente a:

```python
rutas = {
    "modelo-local": "local",
    "modelo-cloud": "cloud"
}

destino = rutas[solicitud["model"]]
```

Una solicitud local se envía a llama.cpp; una cloud se envía al servicio de Codex de OpenAI. No hay cambio automático a cloud si falla el servidor local.

La activación inicial requiere reabrir Codex una vez. Después, cambiar de modelo dentro del selector utiliza el mismo proveedor.

## 4. El catálogo le dice a Codex qué modelos puede ofrecer

Una lista de nombres no es suficiente. Codex necesita entradas con el identificador, nombre visible, contexto, capacidades de razonamiento, modalidades y otros campos de su esquema.

El script obtiene una plantilla de la instalación de Codex, crea entradas para los modelos de llama.cpp y las combina con las de OpenAI. Descubre los modelos locales mediante `/v1/models` y perfiles `llama-*.config.toml`. Valida el JSON con el lector real de la CLI instalada.

El gateway sirve el catálogo combinado y lo actualiza periódicamente. Codex decide cuándo vuelve a consultar ese catálogo; la caché de su interfaz puede demorar la aparición de un modelo recién añadido.

## 5. Conservamos los chats sincronizando su proveedor

Hay dos representaciones relevantes:

- SQLite contiene el índice de chats que la aplicación lista.
- Los archivos JSONL contienen los eventos y el encabezado `session_meta` de cada chat.

Si solo cambiamos `config.toml`, la aplicación puede listar un conjunto distinto de chats según el proveedor. Si cambiamos únicamente SQLite, Codex puede reconstruir ese dato desde el encabezado del historial.

Por eso la migración unificada actualiza la columna `threads.model_provider` y el proveedor del encabezado `session_meta`, con respaldo y rollback. En esta migración conserva los mensajes, los modelos y el resto de campos de cada chat. Deja pendientes los historiales que estén bloqueados por una instancia activa de Codex.

El script no crea una memoria nueva ni convierte los pesos de un modelo. Codex reenvía el contexto de conversación que corresponda al siguiente modelo. Sus límites de contexto, resúmenes y capacidades siguen aplicándose: conservar los archivos no significa que un modelo pueda procesar un historial ilimitado.

## 6. Adaptamos los formatos que llama.cpp no interpretaba

Codex puede enviar información adicional sobre herramientas, mensajes sin un `type` explícito, notificaciones sin `call_id` y razonamiento cifrado de un modelo cloud.

El intermediario adapta esas estructuras solo en la ruta local: declara los tipos necesarios, conserva las notificaciones como datos del usuario, convierte ciertas llamadas de herramientas y reúne instrucciones de sistema/desarrollador en un bloque inicial para las plantillas que lo requieren. El razonamiento cifrado de otro servicio no se puede interpretar localmente y se excluye de esa solicitud.

Estas adaptaciones resolvieron los casos probados del error `Cannot determine type of 'item'`. Las versiones de Codex, llama.cpp y las plantillas de cada modelo pueden introducir otros casos.

## 7. Separamos las credenciales por destino

Codex proporciona la autenticación de su sesión al gateway. Este reenvía las credenciales necesarias solo a OpenAI. En la ruta local construye nuevas cabeceras sin el token de ChatGPT, cookies ni identificador de cuenta. No necesita leer `auth.json`.

El gateway escucha en loopback, exige una clave local y no sigue redirecciones. Los logs contienen estado de rutas y tiempos; no guardan prompts ni respuestas. Al elegir cloud, el contexto que Codex incluya en la solicitud se envía a OpenAI, aunque haya mensajes anteriores generados localmente.

## La extensión de VS Code utiliza otro recorrido

Para autocompletar código no hace falta pasar por Codex:

```text
Código antes del cursor + código después
                 │
                 ▼
       Extensión llama.cpp Inline
                 │
                 ▼
       llama.cpp /infill o chat
                 │
                 ▼
     Fragmento sugerido en gris
                 │
                 ▼
       Tab lo inserta en el editor
```

FIM significa *Fill In the Middle*: el modelo recibe lo que hay antes y después del cursor y genera el fragmento intermedio. Esto permite que complete una expresión sin reescribir el archivo entero.

La extensión espera una breve pausa, solicita el fragmento y cancela si continúas escribiendo. La sugerencia solo se aplica cuando la aceptas. Utiliza el servidor llama.cpp configurado directamente y no cambia las conversaciones ni la configuración de Codex.
