# llama.cpp Inline

Autocompletado de código en VS Code usando tu servidor llama.cpp. Escribes, aparece una sugerencia en gris y la aceptas con **Tab**. **Esc** la descarta.

La extensión consulta directamente al servidor configurado; no necesita Codex, Copilot ni el enrutador de `llama-codex`.

## Instalación del VSIX

1. Abre Extensiones en VS Code.
2. En el menú `…`, elige **Install from VSIX… / Instalar desde VSIX…**.
3. Selecciona `llama-cpp-inline-0.1.0.vsix`.
4. Si VS Code solicita recargar la ventana, hazlo.

## Empezar

1. Mantén llama.cpp escuchando en `http://127.0.0.1:9931` o configura la URL que uses.
2. Abre la paleta con **⌘⇧P** en macOS o **Ctrl+Shift+P** en Windows/Linux.
3. Ejecuta **llama.cpp: Comprobar conexión**.
4. Ejecuta **llama.cpp: Seleccionar modelo** y elige un modelo de código.
5. Abre un archivo de código y escribe. Pausa un momento y acepta la sugerencia con **Tab**.

Ejemplo en Python, con el cursor después del espacio que sigue a `return`:

```python
def sumar(a, b):
    return
```

Un modelo adecuado puede proponer `a + b`. La salida depende de sus pesos y del contexto; revísala como cualquier código generado.

También puedes solicitar una sugerencia con **llama.cpp: Completar código ahora**, **⌘⌥Espacio** en macOS o **Ctrl+Alt+Espacio**. Si el sistema captura ese atajo, reasígnalo desde Atajos de teclado o usa la paleta. La barra de estado `llama.cpp` permite abrir el selector de modelos.

## Cómo completa

- Toma hasta 4000 caracteres anteriores al cursor y 1500 posteriores, únicamente del documento o celda activos.
- Espera 450 ms sin escribir antes de solicitar la sugerencia automática.
- En modo `auto`, solicita `/infill` de llama.cpp con el prefijo y el sufijo.
- Si el servidor indica que ese modelo no admite FIM, utiliza `/v1/chat/completions` en el mismo servidor. Recuerda esa incompatibilidad durante la sesión.
- Recibe el fragmento generado y lo muestra mediante la API de sugerencias en línea de VS Code.
- Si sigues escribiendo o mueves el editor a otro documento, cancela la solicitud. Descarta respuestas de una versión antigua del archivo.

Por defecto completa **una línea**, con un máximo de 64 tokens. Activa `multiline` para bloques de hasta 12 líneas. Un modelo pequeño especializado en código suele dar una experiencia más rápida que uno grande orientado a conversaciones. No descarga pesos ni inicia servidores.

## Configuración

Busca **llama.cpp Inline** en Settings / Configuración. Ejemplo de ajustes de usuario:

```json
{
  "editor.inlineSuggest.enabled": true,
  "llamaCppInline.endpoint": "http://127.0.0.1:9931",
  "llamaCppInline.mode": "auto",
  "llamaCppInline.enabled": true,
  "llamaCppInline.automatic": true,
  "llamaCppInline.multiline": false,
  "llamaCppInline.debounceMs": 450,
  "llamaCppInline.maxTokens": 64,
  "llamaCppInline.timeoutMs": 15000
}
```

| Ajuste | Uso |
| --- | --- |
| `endpoint` | URL raíz o terminada en `/v1`. Por defecto usa el servidor local del puerto 9931. |
| `model` | ID exacto opcional. Vacío utiliza la selección guardada; en ausencia de selección, prioriza un modelo Coder cargado. |
| `mode` | `auto`, `infill` o `chat`. |
| `automatic` | `false` permite pedir sugerencias solo manualmente. |
| `multiline` | Permite insertar varias líneas. |
| `prefixChars` / `suffixChars` | Cuánto contexto del archivo activo se envía. |
| `timeoutMs` | Límite de espera por petición HTTP. Auméntalo si el modelo debe cargarse. |
| `excludedLanguages` | Lenguajes sin sugerencias. Por defecto excluye texto plano, Markdown y mensajes de Git. |

`endpoint` y `model` son ajustes de máquina: un repositorio abierto no puede cambiarlos mediante su configuración de workspace. La selección de modelo se conserva por servidor en el almacenamiento de la extensión. La extensión corre en el lado de la interfaz (`extensionKind: ui`), por lo que en Remote SSH/WSL `127.0.0.1` se refiere al equipo donde está abierta la interfaz de VS Code.

Si llama.cpp utiliza autenticación, ejecuta **llama.cpp: Configurar clave del servidor**. Se guarda en **SecretStorage** de VS Code, separada por endpoint. Un valor vacío elimina la clave.

## Si no aparecen sugerencias

- Comprueba la conexión y el modelo desde la paleta.
- Espera a que cargue el modelo; la primera solicitud puede ser más lenta. Puedes aumentar `timeoutMs`.
- Verifica que `editor.inlineSuggest.enabled` esté activado y que el archivo tenga el lenguaje correcto.
- Si aparece el menú habitual de IntelliSense, ciérralo con **Esc** y pide la sugerencia otra vez. Esta primera versión evita competir con una opción seleccionada de ese menú.
- Consulta la barra de estado; al pasar el cursor muestra el modelo, modo o motivo del error.
- En Output / Salida → **llama.cpp Inline**, los errores muestran un código y estado HTTP, sin el cuerpo de las solicitudes.
- Si un modelo instruct no completa bien en FIM, configura el modo `chat` o elige un modelo base de código compatible con FIM.
- Usa **llama.cpp: Activar/desactivar autocompletado** para pausarlo.

## Datos y alcance

No hay telemetría ni fallback a un proveedor cloud. La extensión envía contexto al **endpoint que configures**; con el valor por defecto, permanece en el servidor de tu Mac. Si apuntas a otro equipo, ese equipo recibirá el fragmento de código.

No recorre el proyecto ni lee otros archivos para formar el contexto. No utiliza claves de ChatGPT ni modifica la configuración o las conversaciones de Codex. No escribe código en el archivo hasta que aceptas la sugerencia. No sigue redirecciones HTTP, para evitar que una clave o el código se reenvíen a otro destino.

Los workspaces no confiables y archivos como `.env`, claves privadas y `auth.json` no reciben sugerencias. Esto reduce exposiciones accidentales; cualquier archivo de código puede contener información sensible y el usuario decide qué servidor utiliza.

## Desarrollo y pruebas

Requiere Node.js compatible con las herramientas de empaquetado:

```bash
npm ci --ignore-scripts
npm test
npm run test:editor
npm run package
```

`npm test` usa servidores HTTP simulados y prueba cancelación, errores, redirecciones, contexto limitado y respuestas atrasadas. `test:editor` abre una instancia aislada de VS Code con un perfil temporal, registra el proveedor y acepta una sugerencia en el editor real. En macOS intenta usar la aplicación instalada; puedes señalar otro ejecutable mediante `VSCODE_EXECUTABLE_PATH`.

La prueba opcional contra un modelo real requiere `LLAMA_INLINE_REAL_SERVER=1` y `LLAMA_INLINE_TEST_MODEL` con el ID de un modelo local FIM. Solo envía un ejemplo sintético de suma.

Esta versión se distribuye como VSIX local. No requiere publicarse en el Marketplace. Licencia MIT.

## Referencias

- [API de sugerencias en línea de VS Code](https://code.visualstudio.com/api/language-extensions/programmatic-language-features#show-inline-completions)
- [Servidor y API /infill de llama.cpp](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
- [Empaquetado VSIX](https://code.visualstudio.com/api/working-with-extensions/publishing-extension#packaging-extensions)
