# Desarrollo local de llama-codex

Abre `llama-codex.code-workspace` en VS Code. Contiene el proyecto completo y ajustes de búsqueda para mantener los archivos generados fuera de la navegación habitual.

## Estructura

```text
llama-codex.code-workspace
switch-codex.py           # Script portable de Codex
tests/                    # Pruebas del switch y gateway
vscode-extension/         # Código fuente y pruebas de llama.cpp Inline
docs/                     # Guías del proyecto
.vscode/                  # Tareas y configuración de F5
dist/                     # VSIX, ZIP de fuentes y verificación local
local-archive/            # Entregables y trabajo original; ignorado por Git
```

## Probar el script

```bash
python3 tests/run.py
```

Para gestionar tu instalación activa, sigue usando `python3 ~/.codex/switch-codex.py`. El código del proyecto es una copia de desarrollo; guardarlo aquí no cambia el servicio instalado en la Mac.

## Probar la extensión

Desde la raíz:

```bash
npm --prefix vscode-extension ci --ignore-scripts
npm --prefix vscode-extension test
npm --prefix vscode-extension run test:editor
```

La prueba del editor abre un perfil temporal y verifica que una sugerencia se acepta en VS Code. No cambia los ajustes de tu perfil habitual.

Para depurar, selecciona **Probar llama.cpp Inline (F5)** en Ejecutar y depurar. Se abre un Extension Development Host con el código de `vscode-extension/`. Usa un archivo de un proyecto de confianza para probar las sugerencias; en modo restringido la extensión permanece desactivada.

La paleta **Tasks: Run Task / Tareas: Ejecutar tarea** ofrece las mismas pruebas y el empaquetado.

## Empaquetar e instalar

```bash
npm --prefix vscode-extension run package
```

El script de empaquetado genera `llama-cpp-inline-0.1.0.vsix` en la raíz del proyecto. El paquete originalmente entregado se conserva en `dist/`. Puedes instalar cualquiera desde Extensiones → menú `…` → Install from VSIX.

## Git

El repositorio conserva el historial publicado y `origin` apunta a `https://github.com/sr3m/llama-codex.git`. Los archivos nuevos se guardan en un commit local. Guardar el proyecto no publica por sí mismo los cambios nuevos.

`local-archive/`, dependencias y paquetes generados están ignorados. Revisa `git status` y el diff antes de publicar. Las guías y capturas de la sesión original pueden contener rutas o información privada y permanecen en el archivo local.
