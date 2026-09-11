# Diseño: dominio `mcp_servers` — servidores MCP declarativos por agente

Fecha: 2026-09-11
Estado: aprobado (brainstorming), pendiente de plan de implementación

## Problema

Un servidor MCP se registra hoy a mano, máquina por máquina, usuario por usuario
y agente por agente: `claude mcp add …` en una, `codex mcp add …` en otra, y en
la tercera nunca. No hay forma de decir "estas tres máquinas hablan con este
servidor MCP desde Claude Code y desde Codex", ni de recuperar en un `sync` los
que una máquina ya tiene.

El caso que lo dispara: `inkscape_mcp` (PyPI `inkscape_mcp` 1.3.1, se lanza con
`uvx inkscape_mcp`) estaba registrado solo en Claude Code de la torre. Las otras
dos máquinas no lo tenían, y Codex no lo tenía en ninguna.

El uso principal es **día 2**: máquinas ya instaladas, `dasik apply <config>
--target /`.

## Por qué no vale `home_files`

El registro no vive en un fichero que dasik pueda poseer:

| Agente | Dónde | Por qué no se puede poseer |
| --- | --- | --- |
| Claude Code (scope user) | `~/.claude.json`, clave raíz `mcpServers` | Fichero de estado mutable del programa (80 claves en la torre: histórico por proyecto, material de cuenta). El propio `claude.yaml` de config-saver lo excluye del backup por eso mismo. |
| Codex | `~/.codex/config.toml`, `[mcp_servers.<name>]` | También mutable: `[projects."…"] trust_level`, `[hooks.state]` con hashes. |

Escribirlos enteros desde el config borraría estado del programa. La vía es la
de `ai_skills`: **dasik pilota el CLI oficial y no escribe ninguno de los dos
ficheros**, solo los lee para decidir.

## Realidad de los CLI (medida en la torre, no supuesta)

`claude mcp add --help` (Claude Code) y `codex mcp add --help` (codex-cli):

| | claude-code | codex |
| --- | --- | --- |
| alta stdio | `claude mcp add <name> -s user [-e K=V …] -- <cmd> [args…]` | `codex mcp add <name> [--env K=V …] -- <cmd> [args…]` |
| alta http | `claude mcp add <name> -s user --transport http <url> [-H "K: v" …]` | `codex mcp add <name> --url <url> [--bearer-token-env-var VAR]` |
| baja | `claude mcp remove <name> -s user` | `codex mcp remove <name>` |
| lectura | `claude mcp list` (sin `--json`, y hace health-check) | `codex mcp list --json`, `codex mcp get <n> --json` |

El scope por defecto de `claude mcp add` es `local` (por proyecto): **`-s user`
es obligatorio**, o el registro no sería de la máquina sino del directorio desde
el que se ejecutó.

Estado escrito por esas altas, comprobado:

```jsonc
// ~/.claude.json
"mcpServers": { "inkscape_mcp": { "type": "stdio", "command": "uvx",
                                  "args": ["inkscape_mcp"], "env": {} } }
```

```toml
# ~/.codex/config.toml
[mcp_servers.inkscape_mcp]
command = "uvx"
args = ["inkscape_mcp"]
```

Las formas http, medidas igual (`claude mcp add … --transport http … -H
"X-Test: 1"` y `codex mcp add … --url … --bearer-token-env-var TOKEN`):

```jsonc
// ~/.claude.json
"probehttp": {"type": "http", "url": "https://example.invalid/mcp",
              "headers": {"X-Test": "1"}}
```

```toml
# ~/.codex/config.toml
[mcp_servers.probehttp]
url = "https://example.invalid/mcp"
bearer_token_env_var = "TOKEN"
```

Esos dos nombres de clave son los que busca el lector de estado, y el script del
invitado los vuelve a medir contra los binarios reales: adivinarlos mal dejaría
al `plan` mudo ante un cambio de credencial para siempre.

## Modelo (`dasik/lib/models/mcp_servers_model.py`)

Bloque raíz opcional en `JsonModel`:

```json
"mcp_servers": {
  "users": ["andres"],
  "failure_policy": "warn-and-continue",
  "entries": [
    { "name": "inkscape_mcp", "command": "uvx", "args": ["inkscape_mcp"],
      "agents": ["claude-code", "codex"] }
  ]
}
```

`McpServerEntry`:

| campo | tipo | regla |
| --- | --- | --- |
| `name` | str | como lo conoce el agente. |
| `agents` | list[str] | ≥1, sin duplicados, solo `claude-code` \| `codex` — un agente sin CLI que pilotar no es declarable. |
| `command` | str? | stdio. Excluyente con `url`. |
| `args` | list[str] | solo con `command`. |
| `env` | dict[str,str] | solo con `command`. |
| `url` | str? | http. Excluyente con `command`. |
| `headers` | dict[str,str] | solo con `url`, y **solo claude-code** lo sabe poner. |
| `bearer_token_env_var` | str? | solo con `url`, y **solo codex** lo sabe poner. |
| `users` | list[str] | estrecha la lista del bloque, nunca la ensancha (igual que `ai_skills`). |

Validadores cruzados: exactamente uno de `command`/`url`; `args`/`env` con `url`
es error; `headers`/`bearer_token_env_var` con `command` es error. Y un campo que
**alguno** de los agentes declarados no sabe poner es error en el modelo, no una
promesa que el `apply` incumple: `headers` exige `agents: ["claude-code"]`
exactamente y `bearer_token_env_var` exige `agents: ["codex"]`. Un servidor http
con ambos se declara como dos entradas, cada una con su campo.

`users` vacío = todos los humanos declarados (uid ≥ 1000), igual que `ai_skills`.
`failure_policy` idéntico: `warn-and-continue` por defecto, porque un servidor
MCP no puede abortar la instalación de un sistema.

### Presencia, no versión

No hay campo de versión: el config nombra el servidor y quien manda la versión
es `uvx`/`npx`/el propio binario, igual que `packages` deja las versiones a
pacman.

### `env` verbatim en `sync`

`sync` captura `env` tal cual, con el mismo criterio que la `PrivateKey` de
WireGuard: la captura describe la máquina, y un valor a medias no es
reproducible. Consecuencia documentada en el bloque y en el reference: **un
config capturado con `env` es privado** — `dasik save` de una máquina con un MCP
con API key mete esa key en el repo. `inkscape_mcp` no usa `env` (solo el
opcional `INKSCAPE_BIN`), así que el caso que motiva el dominio no lo toca.

## Lector de estado (`dasik/lib/actions/mcp_servers_state.py`)

Lee ficheros, nunca procesos — como `ai_skills_state`, y por el mismo motivo: el
`plan` tiene que poder correr sobre `/mnt` de una instalación donde el binario
del agente aún no existe.

- `claude_mcp(home)` → `{name: spec}` desde `$HOME/.claude.json`, clave **raíz**
  `mcpServers`. Los `projects.<path>.mcpServers` se ignoran a propósito: son del
  repositorio, no de la máquina, y dasik declara la máquina.
- `codex_mcp(home)` → `{name: spec}` desde `$HOME/.codex/config.toml`,
  `[mcp_servers.<name>]`.

Ausente, truncado o inesperado = "no hay nada registrado", igual que
`ai_skills_state`: un alta redundante cuesta un comando, mientras que inventar
que algo está registrado dejaría el `plan` mudo sobre un MCP que no existe.

**Reutilización obligada:** `ai_skills_state._load_toml` ya resuelve
tomllib-3.11-con-fallback para `~/.codex/config.toml`. Se promueve a
`dasik/lib/actions/toml_reader.py` y se migra la llamada de `ai_skills_state` en
la misma PR. Una segunda copia del lector es exactamente cómo `plan` y `sync`
acaban discrepando sobre la misma máquina (regla "reuse before you write").

## Acción (`dasik/lib/actions/mcp_servers_action.py`)

Item: `<user>:<agent>:<name>`. Un triple por registro, para que el plan pueda
decir cuál de ellos falta.

- `plan(managed)` → `compute_changes` (CREATE/DELETE) más MODIFY propio: el
  servidor existe con ese nombre pero el spec difiere (`command`, `args`, `env`,
  `url`). Comparación normalizada: `env` ausente y `{}` son lo mismo; `args`
  ausente y `[]` también. Sin esa normalización sale un MODIFY eterno —
  exactamente el defecto que CLAUDE.md describe (plan → apply → el mismo plan).
- `apply(changes)` → la tabla de CLI de arriba, siempre por
  `su - <user> -c '<script>' -- sh <args>` con cada valor viajando como `$N`
  (un nombre con metacaracteres llega como dato inerte). MODIFY = `remove` y
  luego `add`: `mcp add` sobre un nombre existente no reescribe.
- `actual()` → los items presentes. Sin él, cada `sync` desposee el dominio y
  apagar el bloque deja de quitar nada.
- `managed_keys()` → lo deseado menos lo que falló bajo `warn-and-continue`, para
  que el manifiesto no reclame un registro que dasik no consiguió hacer.
- `import_state()` → agrupa por (transporte, command+args+env | url+extras) y
  emite una entrada por grupo con sus `agents`; `users` solo cuando difiere del
  conjunto del bloque. Un `sync` desde `{}` lee los humanos del `/etc/passwd` del
  target, como `ai_skills`.
- `verify()` → `not self.plan(managed=[])`.

## Registro

`register_action(McpServersAction, config_key='__root__', is_optional=True)`
justo **después** de `AiSkillsAction`: mismo requisito (usuarios creados, binario
del agente instalado por `Packages`), y ninguno de los dos depende del otro.

## Pruebas

- TDD en modelo (aceptar/rechazar cada combinación de campos) y en la acción
  (decisión de `plan`, argv exacto de `apply` con `Command.execute` mockeado,
  `import_state` desde ficheros de ejemplo).
- Filas nuevas en `tests/lib/test_feature_detectability.py` (falta ⇒ CREATE;
  presente ⇒ silencio; poseído y no declarado ⇒ DELETE; ajeno ⇒ intacto) y en
  `tests/lib/test_feature_sync_capture.py` (la máquina lo tiene ⇒ se captura; no
  lo tiene ⇒ no se inventa; `sync` → `check` → `plan` en silencio).

## VM (`config/vm-mcp.json` + `scripts/vmtest/guest-mcp.sh`)

Patrón de `vm-ai-skills.json`: los CLI de los agentes se instalan con **npm**
(`@anthropic-ai/claude-code`, `@openai/codex`) porque son paquetes AUR y una
compilación rust dentro de una VM de prueba no vale los minutos. Hace falta red.

Verbos ejercitados de verdad en el invitado, con marcadores `MCP-…=rc`:

1. `check` del config.
2. `plan` con el MCP ausente ⇒ CREATE por agente.
3. `apply` ⇒ registrado en `~/.claude.json` y en `~/.codex/config.toml`.
4. `plan` otra vez ⇒ silencio (convergencia).
5. `sync` ⇒ el bloque vuelve como `mcp_servers`; `check` lo acepta; `plan` del
   capturado ⇒ silencio.
6. `generations` ⇒ el dominio aparece; `rollback` ⇒ re-planifica a nada.
7. **Bloque quitado**: el dominio poseído ⇒ DELETE, y un MCP registrado a mano
   que nadie declara ⇒ intacto.
8. Prueba de que cada check puede fallar: revertir el arreglo, ver el marcador en
   rojo, restaurar. Un verde que nunca se ha visto rojo no es evidencia.

Además, prueba funcional del propio servidor en el invitado (no del dominio):
`uvx inkscape_mcp` arrancado por stdio, `initialize` + `tools/call` por JSON-RPC
desde un cliente mínimo, generando un SVG y comprobando que el fichero existe y
es SVG válido. Es la diferencia entre "dasik registró el MCP" y "el MCP hace
algo".

## Fuera de alcance

- Otros agentes (opencode, cursor): no hay CLI de MCP que pilotar hoy.
- Scope `local`/`project` de Claude Code: eso es del repositorio, no de la
  máquina.
- Los conectores de claude.ai (Gmail, Drive, Canva): son de la cuenta, no de la
  configuración local; dasik no los ve ni los toca.
- Habilitar/deshabilitar herramientas sueltas de un servidor
  (`enabled_tools`/`disabled_tools` de codex): presencia sí, política de
  herramientas no.
