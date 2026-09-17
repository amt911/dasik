# Diseño: repositorios pacman propios y claves de confianza (`pacman.repositories` / `pacman.keys`)

Fecha: 2026-09-16
Estado: aprobado (brainstorming), pendiente de plan de implementación

## Problema

El usuario publica un repositorio pacman firmado, `[amt911]`
(`https://amt911.github.io/arch-packages/$arch`), con los paquetes que no están
en ningún otro sitio: `dasik`, `config-saver` y las dos fuentes Atkinson Nerd.
Hoy dasik no sabe declararlo:

- `PacmanModel` solo conoce `options` (Parallel, Color, VerbosePkgLists) y
  `multilib`. No tiene `extra="forbid"`, así que un dasik viejo **ignora en
  silencio** un campo nuevo.
- Esos cuatro paquetes se compilan desde Git con `package_sources`, fijados a un
  commit. Cada release de dasik obliga a mover el pin en
  `dasik-personal-config` o el paquete no llega a las máquinas.
- Para confiar en el repo hay que seguir a mano los pasos de su README:
  descargar `amt911.gpg`, `pacman-key --add` y `pacman-key --lsign-key`.

La torre ya tiene `[amt911]` escrito a mano encima de `[core]`. El portátil y la
MSI no lo tienen, y ninguna reinstalación lo conservaría.

## Hechos medidos (no supuestos)

| Hecho | Cómo |
| --- | --- |
| `amt911.gpg` contiene una clave primaria `6C6568CE34894645A23ABC44B5BD6F8F9023E53B` y una subclave `5018DF89…A23DF1C8` | `gpg --show-keys --with-colons amt911.gpg` |
| La clave NO está en keys.openpgp.org | `GET /vks/v1/by-fingerprint/6C65…` → 404 |
| `x86_64/amt911.db` y `amt911.db.sig` existen: la base de datos va firmada, así que `SigLevel = Required` exige la clave **antes** del primer `-Sy` | `curl -I` → 200 los dos |
| `PackageResolver.resolve` da precedencia a **repo > grupo > package_sources > AUR** | `package_resolver.py`, docstring de `resolve` |
| `Reconciler.sync` combina fragmentos con `fragments.update(fragment)`: **dos acciones que devuelven la clave raíz `pacman` se pisan** | `reconciler.py` (bucle de `sync`), `ConfigWriter.merge` |
| En la máquina en marcha (`--target /`) dasik nunca hace `pacman -Sy` (solo `PacmanAction` en chroot, para multilib) | `pacman_action.py`, `apply` |

Pendiente de medir en la VM (contrato; ver *Pruebas*): el formato de
`gpg --with-colons` en el llavero de pacman tras `pacman-key --lsign-key`, si
`pacman -Sy --config <temporal>` refresca solo ese repo sin tocar las bases de
datos de core/extra, y si `arch-chroot` monta `/tmp` como tmpfs (de eso depende
dónde se escribe el fichero temporal).

## Modelo

Dos listas nuevas en `PacmanModel`, las dos opcionales, con `default_factory=list`:

```json
"pacman": {
  "options": { "Parallel": true, "Color": true, "VerbosePkgLists": false },
  "multilib": true,
  "repositories": [
    { "name": "amt911",
      "sig_level": "Required",
      "servers": ["https://amt911.github.io/arch-packages/$arch"] }
  ],
  "keys": [
    { "fingerprint": "6C6568CE34894645A23ABC44B5BD6F8F9023E53B",
      "url": "https://amt911.github.io/arch-packages/amt911.gpg" }
  ]
}
```

`PacmanRepositoryModel` (`extra="forbid"`):

- `name`: `^[A-Za-z0-9][A-Za-z0-9._-]*$`. Se rechazan `options` y los nombres
  oficiales (`core`, `extra`, `multilib`, `core-testing`, `extra-testing`,
  `multilib-testing`, `gnome-unstable`, `kde-unstable`, `testing`,
  `community`): dasik no los posee y `multilib` ya tiene su campo propio.
- `servers`: lista de URLs `https://` o `file://`. Sin credenciales en la URL,
  por la misma razón que `GitPackageSourceModel`: `sync` copia los valores tal cual.
- `include`: una ruta absoluta (`Include = /etc/pacman.d/…`). **Exactamente uno**
  de `servers` (no vacío) o `include`. Existe para que `sync` pueda capturar
  repos como chaotic-aur sin producir un config que `check` rechace.
- `sig_level`: opcional. Si se declara, cada palabra tiene que ser un token de
  `pacman.conf(5)` (`Never`, `Optional`, `Required`, `TrustedOnly`, `TrustAll`,
  y sus formas con prefijo `Package`/`Database`). Ausente = no se escribe la
  línea y se usa la de `[options]`.
- Nombres duplicados en la lista: error.

`PacmanKeyModel` (`extra="forbid"`):

- `fingerprint`: 40 hex, normalizado a mayúsculas. Duplicados: error.
- `url`: opcional, `https://`. Ausente = `pacman-key --recv-keys <huella>`,
  el procedimiento de la wiki (*Pacman/Package signing#Adding unofficial keys*).
  Es opcional porque `sync` puede ver una clave en el llavero pero no sabe de
  dónde vino.

## Acción: `PacmanRepositoriesAction`

Fichero `dasik/lib/actions/pacman_repositories_action.py`, dominio
`pacman_repositories`, `config_key='pacman'`, `is_optional=True`. Se registra
**justo después de `PacmanAction`** y antes de `SnapperAction`/`PackagesAction`:
los paquetes del repo tienen que poder resolverse en la misma ejecución.

La lectura del estado va en un módulo aparte y puro,
`dasik/lib/actions/pacman_repos_state.py`: parsear las secciones de
`pacman.conf` y parsear la salida de `gpg --with-colons`. Lo usan la acción y
`PacmanAction.import_state` (ver *sync*), para que `plan` y `sync` no puedan
discrepar sobre la misma máquina.

### Ítems

| Ítem | Presente cuando |
| --- | --- |
| `key:<HUELLA>` | La clave está en el llavero de pacman del target **y es de confianza** (firmada localmente por la clave maestra del llavero). |
| `repo:<nombre>` | Existe una sección `[<nombre>]`. |

`plan()` sigue la forma de `McpServersAction.plan`: `compute_changes` con
`op_install=Op.CREATE` / `op_remove=Op.DELETE`, y además un `Op.MODIFY` para
cada `repo:` que esté en lo declarado y en lo real y cuyo estado difiera. Motivos
posibles del MODIFY:

- `section drift`: `SigLevel`, `Server`/`Include` distintos.
- `below [core]`: la sección está después de `[core]`. Los repos declarados
  van **siempre encima de `[core]`**, en el orden declarado; es la política del
  README del repo y no tiene campo.
- `database not synced`: no existe `/var/lib/pacman/sync/<nombre>.db`. Sin ella
  el resolver clasifica sus paquetes como AUR, que es exactamente el fallo
  silencioso que `multilib_synced` ya tuvo que tapar (2026-08-18).

Ítems reales que nadie declara ni posee (`A \ D \ M`) son deriva: se informan y
no se tocan. Una clave de un keyring empaquetado no es ni siquiera deriva: se
excluye de `actual()` cualquier huella listada en
`/usr/share/pacman/keyrings/*-trusted` (archlinux-keyring y los demás
`*-keyring`), porque la instaló un paquete, no una persona.

Con el bloque `pacman` ausente, el reconciler pasa la config vacía. Declarado
vacío = nada deseado, así que solo se planifica el DELETE de lo que el manifiesto
posee. Esa es la dirección de "apagar" y es intencionada.

### apply

Orden fijo, independiente del orden de `changes`:

1. **Claves CREATE.** Con `url`: descargar a un fichero temporal dentro del
   target (`curl -fsSL`), `gpg --show-keys --with-colons` sobre él y **abortar
   si el conjunto de huellas primarias no es exactamente `{huella declarada}`**.
   Una clave equivocada o un fichero con claves de más nunca llegan al llavero.
   Después `pacman-key --add <fichero>` y `pacman-key --lsign-key <huella>`. Sin
   `url`: `pacman-key --recv-keys <huella>` y `--lsign-key`. El temporal se borra
   siempre (`finally`).
2. **Repos CREATE/MODIFY/DELETE.** Una sola lectura y una sola escritura de
   `pacman.conf`: se quita cada sección dasik-declarada o a borrar, y se reinsertan
   las declaradas justo encima de `[core]`, con la forma
   `[nombre]` / `SigLevel = …` / `Server = …` (una línea por servidor) o
   `Include = …`. Las secciones que dasik no declara ni posee no se tocan.
3. **Sincronizar** cada repo CREATE/MODIFY: `pacman -Sy --config <temporal>`,
   donde el temporal es el bloque `[options]` real del target más la sección de
   ese repo, y nada más. Así se refresca solo su base de datos, en chroot y en
   `--target /`, y un día 2 no provoca una actualización parcial de core/extra.
   Si la VM demuestra que pacman borra o refresca otras bases de datos con esa
   forma, se para y se rediseña; no se degrada a un `-Sy` global.
4. **Claves DELETE**: `pacman-key --delete <huella>`.

Todas las órdenes van por `Command.execute(..., target=t, check=True)`. Los
valores de config viajan como argumentos, nunca interpolados en una shell.

### sync

`PacmanAction._import_fragment` pasa a devolver también `repositories` y `keys`,
leídos con `pacman_repos_state`. `PacmanRepositoriesAction.import_state` devuelve
`{}`, con un docstring que explica por qué: `fragments.update` reemplaza por clave
raíz, y dos fragmentos `pacman` se pisarían.

- Repos: toda sección que no sea `[options]` ni un nombre oficial, en el orden
  del fichero, con `servers`/`include` y `sig_level` tal como están.
- Claves: huellas de confianza que no estén en ningún `*-trusted`. La `url` se
  recupera del config de partida si declara esa huella; si no, se omite.
- Reglas del repo: máquina sin repo ⇒ no se inventa nada, y una lista declarada
  se **vacía** (sync informa de la realidad). El config capturado valida con
  `check` y vuelve a planificar a silencio.

## Personal config (fuera de este repo, dos PRs)

1. **Ya, con dasik 0.17.0**: skills, MCP, `android-cli`, `/etc/codex/config.toml`
   y el documento de config-saver para la clave de 21st. No depende de esta spec.
2. **Con dasik 0.18.0 instalado en la máquina**: `pacman.repositories` +
   `pacman.keys` para `[amt911]`, y se quitan las `package_sources` de `dasik`,
   `config-saver`, `ttf-atkinson-hyperlegible-next-nerd-git` y
   `ttf-atkinson-hyperlegible-next-nerd-mono-git` (y `config_saver.source`).
   Mantener las dos cosas a la vez sería una trampa: el resolver elige el
   repo, pero `PackagesAction.plan` sigue comparando el `ref` de Git de un
   paquete instalado y podría planificar una recompilación.

## Pruebas

**Unitarias (TDD):**

- Modelo: nombres oficiales rechazados, `servers` XOR `include`, tokens de
  `SigLevel`, huellas, duplicados, URLs con credenciales o http.
- `pacman_repos_state`: parseo de secciones (orden, `[core]`, comentarios,
  `Include`, repos múltiples) y de `gpg --with-colons` (confianza, subclaves que
  no cuentan como primarias, exclusión de `*-trusted`). Con salidas **reales
  capturadas en la VM** como fixtures, no inventadas.
- Acción: matriz de detectabilidad (falta ⇒ CREATE; presente ⇒ silencio; poseído
  y no declarado ⇒ DELETE; deriva, debajo de core o sin base de datos ⇒ MODIFY;
  deriva sin poseer ⇒ nada), orden de apply, huella que no coincide ⇒ aborta sin
  `pacman-key --add`, temporal borrado incluso si falla.
- `test_feature_detectability.py` y `test_feature_sync_capture.py` ampliados.
- Hypothesis: escribir secciones y volver a leerlas es idempotente
  (`render(parse(x))` estable, y `plan` tras `apply` simulado vacío).

**VM (una sola a la vez, `DASIK_VM_RAM=4096`; el host tiene ~10 GiB disponibles
con swap en uso, así que se comprueba `MemAvailable` antes de cada arranque y
nada pesado corre en paralelo):**

- `config/vm-pacman-repo.json` con `[amt911]`, su clave y `config-saver` en
  `packages`. `install-driven` prueba el camino chroot: clave, sección, base de
  datos y paquete sacado del repo durante la instalación.
- `scripts/vmtest/guest-pacman-repo.sh` sobre la máquina instalada
  (`--target /`), con marcas `PACREPO-…=rc`:
  - `check`, `plan` silencioso, `sync` → `check` → `plan` silencioso,
    `generations`;
  - quitar la sección y la clave a mano ⇒ `plan` las propone ⇒ `apply` ⇒ `plan`
    silencioso;
  - bloque quitado ⇒ DELETE ⇒ `apply` ⇒ sección y clave fuera;
  - `rollback` a la generación con el repo ⇒ vuelven ⇒ `plan` silencioso;
  - huella falsa ⇒ `apply` falla y la clave NO está en el llavero;
  - el `-Sy` de un solo repo no cambia el mtime de `core.db`/`extra.db`;
  - `sync` desde `{}` captura el repo y la clave.
- Probar que cada comprobación puede fallar: revertir la verificación de huella y
  el `-Sy` de un solo repo, y ver las marcas en rojo.

## Fuera de alcance

- Repos por debajo de `[core]` o posición configurable.
- `DBPath` distinto de `/var/lib/pacman`.
- `Usage =` y demás opciones por repo que no sean `SigLevel`/`Server`/`Include`.
- Arreglar el `-Sy` de multilib en `--target /` (mismo mecanismo; se propone
  aparte si hace falta).
