# Diseño: dominio `ai_skills` — skills de IA declarativas por agente

Fecha: 2026-09-02
Estado: aprobado (brainstorming), pendiente de plan de implementación

## Problema

Las skills/plugins de los agentes de IA (Claude Code, Codex, opencode…) se
instalan hoy a mano, una por máquina y por usuario, con un instalador distinto
cada una. No hay forma de decir "estas tres máquinas llevan estas skills en
estos agentes" ni de recuperar en un `sync` lo que una máquina ya tiene.

El objetivo es el de siempre en dasik: describir el estado en el JSON,
`plan`/`apply` converger, re-ejecutar no cambiar nada, y `sync` capturar la
realidad de vuelta. Con un requisito extra que fija el diseño: **la instalación
tiene que ser la oficial de cada agente**, para que actualizar siga siendo
`claude plugin update` / `npx skills update` y dasik no pelee con ello.

El uso principal es **día 2**: implantarlo en máquinas ya instaladas con dasik
(`dasik apply <config> --target /`).

## Realidad del ecosistema (medida, no supuesta)

Cada par (skill, agente) tiene su propio instalador oficial:

| skill | claude code | codex |
| --- | --- | --- |
| superpowers | `claude plugin marketplace add` + `claude plugin install superpowers@<mkt>` | `codex plugin add superpowers@openai-curated` |
| caveman | `claude plugin marketplace add JuliusBrussee/caveman` + `claude plugin install caveman@caveman` | `npx skills add JuliusBrussee/caveman -a codex` |
| impeccable | `npx skills add pbakaus/impeccable -a claude-code` (o su marketplace `impeccable`) | `npx skills add pbakaus/impeccable -a codex` |

Nombres de marketplace comprobados en los repos: `impeccable` (pbakaus/impeccable),
`caveman` (JuliusBrussee/caveman), `superpowers-dev` (obra/superpowers), y
`claude-plugins-official` (anthropics/claude-plugins-official), que es de donde
sale el superpowers instalado hoy. `openai-curated` viene de fábrica en Codex.

El CLI `skills` (npm `skills`, repo `vercel-labs/skills`) es el instalador
cross-agent: `add` / `remove` / `update` / `list`, escribe la copia canónica en
`~/.agents/skills/<n>` y enlaza desde el directorio de cada agente, con un lock
en `~/.agents/.skill-lock.json`. Acepta `-y` para modo no interactivo.

## Modelo (`dasik/lib/models/ai_skills_model.py`)

Bloque raíz opcional en `JsonModel`:

```json
"ai_skills": {
  "users": ["andres"],
  "failure_policy": "warn-and-continue",
  "entries": [
    { "name": "superpowers", "method": "claude-plugin",
      "marketplace": {"name": "claude-plugins-official",
                      "source": "anthropics/claude-plugins-official"} },
    { "name": "superpowers", "method": "codex-plugin",
      "marketplace": {"name": "openai-curated"} },
    { "name": "caveman", "method": "claude-plugin",
      "marketplace": {"name": "caveman", "source": "JuliusBrussee/caveman"} },
    { "name": "impeccable", "method": "skills",
      "source": "pbakaus/impeccable", "agents": ["claude-code", "codex"] }
  ]
}
```

- `users`: opcional. Por defecto, todos los usuarios declarados en `users` con
  uid >= 1000 (los humanos). Los plugins y las skills viven en `$HOME`, así que
  "para todo el sistema" significa "para todos los usuarios reales".
- `failure_policy`: `warn-and-continue` (default) | `abort`. Precedente:
  `package_policy.build_failure`.
- `method`: `claude-plugin` | `codex-plugin` | `skills`.
  - `claude-plugin` / `codex-plugin`: el agente está implícito en el método;
    `marketplace.name` es obligatorio, `marketplace.source` sólo si hay que
    registrarlo (los de fábrica, como `openai-curated`, no lo llevan).
  - `skills`: `source` (owner/repo, URL git o ruta) obligatorio y `agents` con
    los ids del CLI `skills` (`claude-code`, `codex`, `opencode`, `cursor`, …).
    Un agente nuevo no necesita código nuevo.
- **No se declara versión.** Igual que `packages`, se declara presencia; la
  versión la gobierna el instalador oficial. Así `claude plugin update` o
  `npx skills update` no producen deriva que el siguiente `plan` revierta.

Validación pydantic: `name` no vacío; `method` en el Literal; `skills` exige
`source` y al menos un agente; `claude-plugin`/`codex-plugin` exigen
`marketplace.name` y rechazan `agents`; `users` sin duplicados.

## Ítems del dominio

Dominio `ai_skills`. Un ítem por (usuario, agente, artefacto), y el registro del
marketplace es un ítem propio porque es estado que dasik crea y puede retirar:

```
<user>:<agent>:marketplace:<mkt-name>=<source>
<user>:<agent>:plugin:<plugin>@<mkt-name>
<user>:<agent>:skill:<name>
```

Ejemplo de `plan`:

```
+ [ai_skills] andres:claude-code:marketplace:caveman=JuliusBrussee/caveman
+ [ai_skills] andres:claude-code:plugin:caveman@caveman
+ [ai_skills] andres:codex:skill:impeccable
```

`managed_keys()` devuelve exactamente esos ítems, de modo que quitar una entrada
del JSON produce el REMOVE correspondiente (y una skill que nadie declaró ni
posee el manifiesto se deja en paz).

## `actual()` — realidad leída sin red

| método | fuente |
| --- | --- |
| `claude-plugin` | `~/.claude/plugins/installed_plugins.json` (claves `plugin@marketplace`) y `~/.claude/plugins/known_marketplaces.json` (nombre → `source.repo`) |
| `codex-plugin` | `~/.codex/config.toml`: secciones `[plugins."<p>@<mkt>"]` con `enabled = true`; marketplaces configurados en el mismo fichero |
| `skills` | depende del agente (medido en la VM, ver nota abajo): para **codex/cursor/opencode** basta `~/.agents/skills/<n>/SKILL.md`; **claude-code** además tiene `~/.claude/skills/<n>`. La procedencia sale de `~/.agents/.skill-lock.json` |

> **Corrección tras la primera VM (2026-09-02).** El diseño suponía un
> directorio por agente para todos. Falso: el CLI `skills` llama *universal* a
> todo agente cuyo `skillsDir` es `.agents/skills` — codex, cursor y opencode lo
> son — y para ellos instala **sólo** la copia canónica, sin enlace propio.
> Leer `~/.codex/skills/<n>` hacía que `apply` dijera éxito, no convergiera
> nunca y el siguiente `plan` volviera a pedir lo mismo. La suite estaba verde;
> lo vio el invitado.

Todas las rutas se resuelven bajo el target (`Target.path`) y el home real sale
de `/etc/passwd` del target, con el mismo fallback `/home/<user>` que usa
`HomeFilesAction` cuando el usuario aún no existe.

TOML: `tomllib` (stdlib, 3.11+) y, si no está, un lector mínimo de cabeceras
`[plugins."…"]`. Sin dependencias nuevas — dasik escribe TOML nunca; lo escribe
el CLI oficial.

## `apply()` — sólo comandos oficiales, como el usuario

Patrón ya usado por `packages_action` y `pkgbuild_git_installer`:
`su - <user> -c '<script>' -- sh <args…>`, con los valores como parámetros
posicionales (nunca interpolados en la cadena del shell).

| ítem | CREATE | DELETE |
| --- | --- | --- |
| marketplace (claude) | `claude plugin marketplace add <source>` | `claude plugin marketplace remove <name>` |
| plugin (claude) | `claude plugin install <p>@<mkt> -y --scope user` | `claude plugin uninstall <p>@<mkt>` |
| marketplace (codex) | `codex plugin marketplace add <source>` | `codex plugin marketplace remove <name>` |
| plugin (codex) | `codex plugin add <p>@<mkt>` | `codex plugin remove <p>` |
| skill | `npx -y skills add <source> --skill <n> -g -a <agent> -y` | `npx -y skills remove --global --agent <agent> <n>` |

Orden dentro del apply: primero los marketplaces, después los plugins que los
usan (un `install` contra un marketplace no registrado falla).

`failure_policy`:

- `warn-and-continue` (default): se registra el fallo en rojo con el comando y
  su salida, el apply sigue, y el ítem **no** entra en el manifiesto, de modo
  que el siguiente `plan` lo vuelve a pedir. Una skill que no baja no puede
  tumbar la instalación de un sistema.
- `abort`: `CommandExecutionError` y se acabó.

## `import_state()` — `sync` captura la máquina

Para cada usuario humano del target:

- plugins de Claude desde `installed_plugins.json`, con el marketplace y su
  `source` desde `known_marketplaces.json`;
- plugins de Codex desde `config.toml`;
- skills desde `~/.agents/skills`, con los agentes que las enlazan y el `source`
  del lock.

Exclusiones:

- lo que el agente trae de fábrica (`~/.codex/skills/.system/*`) — no lo instaló
  nadie y reinstalarlo no tiene sentido;
- una skill sin origen conocido en el lock (p. ej. una carpeta local hecha a
  mano): se omite **con aviso**, porque capturarla produciría una config que
  ninguna otra máquina puede reproducir.

Invariante: `sync` → `check` → `plan` en silencio.

## Registro y validación previa

`register_action(AiSkillsAction, config_key='__root__', is_optional=True)` en la
fase 4, después de `UsersAction` (hace falta el home) y de `PackagesAction`
(hace falta el binario del agente, y `nodejs`/`npm` para `npx`), antes de la
fase de arranque.

`preflight()` (avisos, no errores — el usuario puede tener los binarios por
otra vía):

- entradas `skills` sin `nodejs`/`npm` entre los paquetes declarados;
- `claude-plugin` sin un paquete que provea `claude`, `codex-plugin` sin `codex`;
- una entrada `skills` cuyo agente no es ninguno conocido por el CLI.

## Pruebas

Unitarias (TDD, mocks de `Command.execute` y un `/mnt` de mentira):

- modelo: acepta lo válido, rechaza método desconocido, `skills` sin `source`,
  `claude-plugin` con `agents`;
- `actual()`: lee los tres formatos de estado, incluido un `config.toml` con
  `enabled = false` (= no instalado);
- `plan()`: falta ⇒ CREATE, presente ⇒ silencio, declarado-fuera-pero-en-manifiesto
  ⇒ DELETE, ajeno ⇒ intacto; marketplace antes que plugin;
- `apply()`: los comandos exactos, con `su - <user>` y argumentos posicionales;
- `failure_policy`: warn deja el ítem fuera del manifiesto; abort lanza;
- `import_state()`: captura los tres métodos, omite `.system` y las skills sin
  origen; el config capturado valida y re-planifica a nada.
- matrices existentes: `tests/lib/test_feature_detectability.py` y
  `tests/lib/test_feature_sync_capture.py`.

VM (`config/vm-ai-skills.json` + `scripts/vmtest/guest-ai-skills.sh`), con red en
el invitado y **todos los verbos**:

1. `check` de la config;
2. `plan` ⇒ aparecen los ítems;
3. `apply` ⇒ instala de verdad con los CLI oficiales;
4. `plan` ⇒ silencio (idempotencia);
5. comprobación por fuera: `claude plugin list`, `codex plugin list`,
   `npx skills list`, y los ficheros de estado;
6. `apply` otra vez ⇒ no ejecuta ningún comando de instalación;
7. `sync` ⇒ el bloque vuelve; `check` del capturado; `plan` en silencio;
8. `generations` / `rollback` ⇒ la generación restaurada re-planifica a nada;
9. la config **sin** el bloque ⇒ REMOVE de lo que el manifiesto poseía, y una
   skill instalada a mano por fuera queda intacta;
10. día 2 sobre el sistema ya instalado (`--target /`), que es el caso real.

Cada aserción tiene que verse fallar antes de darla por buena (revertir el
arreglo, ver la línea `AISKILLS-…=rc` en no-cero, restaurar).

## Riesgos

- **El chroot recién instalado puede no poder ejecutar los CLI**: red, ausencia
  de TTY, o un `npx` sin caché. Es exactamente lo que la VM tiene que medir; el
  default `warn-and-continue` existe para que un fallo así no arruine una
  instalación. Si resulta que sólo funciona en día 2, el diseño no cambia: el
  dominio queda no convergido y el primer `apply --target /` lo completa.
- **`claude plugin install` sin sesión iniciada**: instalar un plugin no debería
  requerir login, pero hay que comprobarlo en la VM.
- **Formatos de estado de terceros**: `installed_plugins.json` y el lock de
  `skills` son ficheros internos de otras herramientas y pueden cambiar. Se leen
  de forma defensiva (ausente o ilegible = "no instalado"), nunca se escriben.
