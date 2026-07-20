# PLAN — orden Users/Packages + streaming de progreso + instalador AUR híbrido

> Plan de implementación para Claude (u otro LLM ejecutor). Sustituye a
> `PLAN-fixes-vm-boot-packages-nm-wireguard.md` (ya ejecutado; puede borrarse).
> TDD obligatorio (red→green→refactor), coverage ≥80%, `mypy dasik` y `bandit`
> limpios. Tres PRs independientes en orden **T1 → T2 → T3** (T3 usa el
> streaming de T2).

## 1. Contexto

`dasik apply` con `test-config.json` falló (log `dasik-apply-20260718-083428.log`):

1. **AUR sin deps transitivas** — `asunder` (AUR) falla con makepkg **exit 8**
   porque su dependencia `gtk2` ya solo existe en AUR (verificado: no está en
   los sync repos). `_apply_aur_install()`
   (`dasik/lib/actions/packages_action.py:579-645`) hace clone +
   `makepkg -sri --noconfirm` paquete a paquete con `subprocess.run` **crudo**:
   no resuelve deps AUR transitivas, no usa helper aunque `yay` esté declarado,
   no tiene try/finally (un fallo deja el usuario `_aurbuilder` y
   `/etc/sudoers.d/_aurbuilder` NOPASSWD residuales) y nada de makepkg/clone
   queda en RunLogger.
2. **Users antes que Packages** —
   `useradd -m -s /bin/zsh -G docker,libvirt,wheel andres` sale **exit 6**
   (zsh y los grupos docker/libvirt aún no instalados) y el fallo **se ignora**
   (`Command.execute` sin `check=True` en
   `dasik/lib/actions/users_action.py:217-244`); el `usermod -p` posterior
   también falla en silencio → el usuario nunca se crea.
3. **Sin progreso visible** — incluso con `-v`, pacman/pacstrap/makepkg no
   muestran nada hasta terminar: `Command.execute` captura con PIPE y
   `RunLogger.record()` solo vuelca al final del comando.

Decisiones ya tomadas por el usuario: enfoque **híbrido** para deps AUR
(helper yay/paru si está declarado; resolución propia si no); verificación =
gates unit + su reinstalación real de la VM; arreglar el progreso en vivo.

## 2. Diagnóstico verificado (anclas de código)

- El Reconciler aplica acciones **en orden de registro** de `setup_actions()`
  (`dasik/lib/actions/actions_handler_v2.py:58-168`); `depends_on` NO ordena
  (solo valida claves, `action_registry.py:90-93`). UsersAction registrada en
  l.97-103 (fase 2), PackagesAction en l.106-112 (fase 3).
- `expand_kvm` (`dasik/lib/expand/toggles.py:51-64`) añade el paquete `libvirt`
  **y** el grupo `libvirt` a los usuarios; el grupo lo crea el paquete al
  instalarse → Users DEBE ir tras Packages. Verificado: ninguna acción
  posterior a Packages necesita que el usuario ya exista.
- `Command.execute` (`dasik/lib/command_worker/command_worker.py:21-88`):
  firma `execute(cmd, args, run_as_chroot=False, target=None, input=None,
  env=None, check=False)`; siempre `RunLogger.record(argv, rc, stdout,
  stderr)`; con `check=True` → error rojo + `CommandExecutionError`.
- La lista AUR de v3 viene de **`resolution.aur`** (resolver,
  `packages_action.py:521-549`), **NO** del legacy `self.aur_pkgs` (prefijo
  `aur-`, solo lo usa la ruta v2 muerta `execute()`). El resolver vive en
  `self._resolver = PackageResolver()` (`packages_action.py:83`).
- Patrón de referencia para el installer: `PkgbuildGitInstaller`
  (`dasik/lib/actions/pkgbuild_git_installer.py`) — `install()` con try/finally
  → `_cleanup(created_user, sudoers_path)` (borra sudoers siempre, `userdel`
  solo si lo creó este run), `_ensure_prerequisites()` devuelve created-flag,
  parseo `.SRCINFO` con fallback `makepkg --printsrcinfo` (l.157-182),
  `BUILD_USER = "_aurbuilder"` compartido con la ruta AUR.
- Reutilizables: `_su_argv(user, script, *args)` =
  `["su","-",user,"-c",script,"sh",*args]` (args posicionales `$1/$2`, seguro
  argv, `packages_action.py:195-205`); `_validate_pkg_name`/`_VALID_PKG_NAME`
  (l.48-62); `PackageResolver.repo_names()` (`pacman -Slq` batch),
  `aur_info(names)` (RPC v5 `info` batch, existencia exacta),
  `AurUnavailableError` (fallo de red ≠ unknown).
- Semántica pacman **validada en vivo**: `pacman -T <dep>` → rc **0** =
  satisfecha, rc **127** = falta (acepta constraints de versión y provides de
  paquetes instalados); `pacman -Sp --print-format %n <name>` → rc 0 si es
  resoluble en los sync DBs (incluye virtuals/provides como `sh`), rc 1
  "target not found".
- Verbose vive en el singleton: `run_logger.configure(log_path, verbose)` desde
  `__main__`; `run_logger.get().verbose` es la vía limpia para que `Command`
  decida si ecoar.

---

## 3. T1 — PR `fix/users-after-packages-and-check`

**Archivos**: `dasik/lib/actions/actions_handler_v2.py`,
`dasik/lib/actions/users_action.py`,
`tests/lib/actions/test_setup_actions.py`,
`tests/lib/actions/test_users_action.py`.

1. En `setup_actions()`: mover el bloque
   `register_action(UsersAction, config_key='__root__', is_optional=True)`
   (l.97-103) a justo **después** del bloque de PackagesAction (tras l.112) y
   **antes** de SystemdAction. Comentario en el código: los grupos
   (docker/libvirt) y shells (zsh) que `useradd -G`/`-s` referencia los crean
   los paquetes al instalarse.
2. `check=True` en las **6** llamadas mutantes de `UsersAction.apply()`:
   `useradd` (l.232), `usermod -p` (l.233 y l.240), `usermod -s` (l.238),
   `usermod -G` (l.239), `userdel` (l.243). Con `check=True`, un `useradd`
   fallido lanza `CommandExecutionError` antes de intentar poner la contraseña.
   No tocar el `execute()` legacy (l.267-286): ruta muerta.

**Tests TDD** (mock existente:
`patch("dasik.lib.actions.users_action.Command.execute")`):

| Test | Assert |
| --- | --- |
| `test_setup_actions.py::test_users_registered_after_packages` | índices en `get_all_actions()`: Packages < Users |
| `test_setup_actions.py::test_users_registered_before_systemd` | Users < Systemd |
| `test_users_action.py::test_apply_mutations_pass_check_true` | apply con CREATE+MODIFY+DELETE: toda llamada lleva `check=True` |
| `test_users_action.py::test_apply_useradd_failure_aborts_before_password` | side_effect lanza en `useradd` → `pytest.raises(CommandExecutionError)`; ninguna llamada posterior con `-p` |
| `test_users_action.py::test_apply_userdel_failure_propagates` | ídem para userdel |

**Aceptación**: suite verde, mypy limpio; en la VM `id andres` → zsh +
docker/libvirt/wheel; un useradd fallido aborta el apply en rojo.

---

## 4. T2 — PR `feat/command-execute-streaming`

**Archivos**: `dasik/lib/command_worker/command_worker.py`,
`dasik/lib/logging/run_logger.py`, `dasik/lib/actions/packages_action.py`
(activación), `dasik/lib/actions/base_install_action.py` (activación),
`tests/lib/command_worker/test_command_streaming.py` (**nuevo**),
`tests/lib/logging/test_run_logger.py`.

1. `Command.execute(..., stream=False)` — nuevo parámetro opt-in. Con
   `stream=True`:
   - `input is not None` → `ValueError("stream=True does not support input=")`.
   - `subprocess.Popen(argv, stdout=PIPE, stderr=STDOUT, env=...)` — stderr
     **fusionado** a stdout para preservar el orden temporal (documentar en el
     docstring: en el log la sección stderr queda vacía).
   - `logger.stream_start(argv)`; bucle por líneas acumulando en un buffer y
     ecoando cada línea con `logger.stream_line(...)`.
   - Al terminar: devolver `CompletedProcess(argv, rc, stdout=buf, stderr=b"")`
     (contrato de retorno intacto) + `logger.record(argv, rc, buf, b"",
     echoed=True)` — el archivo de log recibe la salida completa **una** vez,
     con el formato actual (`$ cmd … [exit N]`).
   - `check=True`: misma lógica actual; el detalle del error usa la cola del
     buffer (~2000 chars) en lugar de stderr.
2. `RunLogger`:
   - `record(..., echoed: bool = False)`: si `echoed`, omite el eco a consola
     (ya salió en vivo) pero escribe al archivo exactamente igual.
   - Nuevos `stream_start(argv)` y `stream_line(line)`: **solo consola, solo
     con verbose**, no escriben al archivo (el archivo lo escribe `record()`).
3. Activar `stream=True` en los comandos largos de esta PR:
   `pacman -S` (`packages_action.py:532`) y `pacman -Rns` (l.562);
   `pacman -Sy archlinux-keyring` (`base_install_action.py:124`) y `pacstrap`
   (l.127 — mantiene su chequeo manual de rc). makepkg/clone/yay se activan
   en T3.

**Tests TDD** (mock de `Popen` con fake proc cuyo `stdout` es
`iter([b"a\n", b"b\n"])` y `wait()` → rc; logger inyectado con
`RunLogger(stream=io.StringIO(), verbose=...)` monkeypatcheando
`run_logger.get`):

| Test | Assert |
| --- | --- |
| `test_stream_uses_popen_and_records_once` | Popen sí y `subprocess.run` no; `record` 1 vez con `stdout == b"a\nb\n"` y `echoed=True` |
| `test_stream_popen_merges_stderr` | kwargs de Popen: `stderr is subprocess.STDOUT` |
| `test_stream_echoes_lines_only_when_verbose` | verbose → líneas en el StringIO durante el bucle; sin verbose → consola vacía |
| `test_stream_check_true_raises_with_output_tail` | rc 1 + check → `CommandExecutionError` con cola de salida; `logger.error` invocado |
| `test_stream_rejects_input` | `stream=True, input=b"x"` → `ValueError` |
| `test_default_nonstream_path_unchanged` | sin `stream` → `subprocess.run` como hoy |
| `test_run_logger.py::test_record_echoed_skips_console_but_writes_file` | sin duplicado en consola, archivo completo |
| `test_run_logger.py::test_stream_line_silent_without_verbose` | nada en consola ni en archivo |
| call-sites | mocks de `Command.execute` reciben `stream=True` en `pacman -S`/`-Rns`/pacstrap |

**Aceptación**: con `-v`, la salida de pacman/pacstrap aparece línea a línea en
vivo; sin `-v`, consola en silencio y log completo; formato del archivo de log
sin cambios; `check=True` sigue abortando; mypy/bandit limpios.

---

## 5. T3 — PR `feat/aur-hybrid-installer`

**Archivos**: `dasik/lib/actions/srcinfo.py` (**nuevo**),
`dasik/lib/actions/aur_installer.py` (**nuevo**),
`dasik/lib/actions/packages_action.py` (`_apply_aur_install` → delegación fina;
borrar el cuerpo l.579-645),
`dasik/lib/actions/pkgbuild_git_installer.py` (delegar parseo a `srcinfo` +
migrar `_run` a `Command.execute` con stream),
`tests/lib/actions/test_srcinfo.py` y `tests/lib/actions/test_aur_installer.py`
(**nuevos**), actualizar `tests/lib/actions/test_pkgbuild_git_installer.py` y
`tests/lib/actions/test_packages_action_v3.py`.

**Contención de alcance**: NO extraer una clase base común entre `AurInstaller`
y `PkgbuildGitInstaller` en esta PR (el git installer tiene tests estables y
semántica propia de SHA-pinning; la duplicación es ~30 líneas). Se comparte
solo `srcinfo.py`. Los helpers legacy v2 de packages_action
(`_ensure_aur_prerequisites`, `_install_single_aur_pkg`,
`_install_aur_with_helper`, `_cleanup_aur_user`, l.165-250) se dejan intactos;
abrir issue para borrar la ruta v2 completa.

### 5.1 `srcinfo.py` — funciones puras

```python
_DEP_KEYS = ("depends", "makedepends", "checkdepends")

def parse_pkgnames(text: str) -> set[str]      # movido de PkgbuildGitInstaller
def parse_depends(text: str) -> set[str]
    # claves exactas o con sufijo de arquitectura (p.ej. depends_x86_64):
    # key == d or key.startswith(d + "_") para d en _DEP_KEYS; excluye optdepends
def strip_version_constraint(dep: str) -> str
    # re.split(r"[<>=]", dep, 1)[0].strip()  →  "gtk2>=2.24" → "gtk2"
```

### 5.2 `aur_installer.py`

```python
class AurInstaller:
    """Instala resolution.aur: ruta helper (yay/paru) o resolución propia."""
    BUILD_USER = "_aurbuilder"
    BUILD_ROOT = "/home/_aurbuilder/dasik-aur"
    HELPERS = ("yay", "paru")

    def __init__(self, target, resolver: PackageResolver) -> None: ...
    def install(self, pkgs: list[str]) -> None                    # entry point
    def _run(self, cmd, args, check=True, stream=False)           # → Command.execute(..., target=...)
    def _ensure_prerequisites(self) -> bool                       # created-flag
    def _cleanup(self, created_user: bool, sudoers_path: str) -> None
    def _install_with_helper(self, helper: str, rest: list[str]) -> None
    def _clone(self, pkg: str) -> str                             # rm -rf + git clone; devuelve build_dir
    def _read_deps(self, pkg_dir: str) -> set[str]                # .SRCINFO o makepkg --printsrcinfo
    def _classify_dep(self, dep: str, repo: set[str]) -> tuple[str, str]
    def _resolve_build_order(self, pkgs: list[str]) -> tuple[list[str], set[str]]
        # (orden topológico, deps_descubiertas) — clona y clasifica TODO antes del primer build
    def _build_one(self, pkg: str) -> None                        # makepkg -sri --noconfirm, stream=True
    def _verify_installed(self, pkgs: list[str]) -> None          # pacman -Q por declarado
```

Flujo de `install(pkgs)`:

1. `_validate_pkg_name` sobre cada nombre (importar la de `packages_action`;
   no crear una tercera copia del regex).
2. `helper = next((h for h in self.HELPERS if h in pkgs), None)`.
3. `created = self._ensure_prerequisites()`:
   `pacman --noconfirm --needed -S base-devel git` **check=True** (hoy va sin
   check); sonda `id _aurbuilder` vía `Command.execute` check=False →
   `created = rc != 0`; si created, `useradd -m -r -s /bin/bash _aurbuilder`
   check=True; escribir sudoers en
   `target.path("/etc/sudoers.d/_aurbuilder")` con NOPASSWD.
4. `try:`
   - **Ruta A (helper declarado)**: `_build_one(helper)` (clone + makepkg
     endurecidos; las deps de yay/paru son todas de repo → `makepkg -s` las
     cubre); `rest = [p for p in pkgs if p != helper]`; si `rest`:
     `su - _aurbuilder -c 'exec "$@"' sh <helper> -S --noconfirm --needed
     <rest...>` check=True stream=True (argv posicional, **nunca**
     interpolación; el NOPASSWD del sudoers permite al helper elevar). El
     helper resuelve deps AUR transitivas, provides y split packages solo.
   - **Ruta B (sin helper)**:
     `orden, deps_descubiertas = _resolve_build_order(pkgs)`; construir en
     orden con re-check `pacman -T` por nodo (skip si un build previo ya la
     satisfizo); tras instalar cada dep descubierta →
     `pacman -D --asdeps <dep>` check=True (`makepkg -i` instala **explícito**
     vía `pacman -U`). Los declarados no reciben `-D` (quedan explícitos).
   - Común: `_verify_installed(pkgs)` — `pacman -Q <pkg>` por declarado,
     rc≠0 → `CommandExecutionError`.
5. `finally: self._cleanup(created, sudoers_path)`: sudoers `os.remove`
   siempre (si existe); `userdel -r _aurbuilder` **solo si created**
   (check=False, rc ignorado); `rm -rf BUILD_ROOT` best-effort.

### 5.3 Delegación en `packages_action.py`

```python
def _apply_aur_install(self, pkgs: list[str]) -> None:
    if self.context is None or self.context.target is None:
        raise CommandExecutionError("AUR install requires an action context with a target.")
    from .aur_installer import AurInstaller
    AurInstaller(self.context.target, resolver=self._resolver).install(pkgs)
```

### 5.4 `pkgbuild_git_installer.py`

(a) `_parse_pkgnames` delega en `srcinfo.parse_pkgnames` (wrapper fino →
sus tests puros siguen pasando); (b) `_run` delega en
`Command.execute(..., target=..., check=..., stream=True en
clone/checkout/makepkg)` → los builds git-source también quedan en RunLogger y
muestran progreso (hoy son `subprocess.run` crudos, misma laguna). Actualizar
el `_Harness` de sus tests a un único patch target (`Command.execute`).

### 5.5 Detalles espinosos (clavarlos)

1. **Clasificación por dep** (orden estricto;
   `repo = resolver.repo_names(target)` UNA vez en variable local — el método
   no cachea):
   - (a) `pacman -T <dep_completa_con_constraint>` con `target=target`,
     check=False: rc 0 → satisfecha, skip; rc 127 → falta; rc fuera de
     {0,127} → error real.
   - (b) `bare = strip_version_constraint(dep)` + `_validate_pkg_name(bare)`.
   - (c) `bare in repo` → dejar a `makepkg -s`.
   - (d) `pacman -Sp --print-format %n <bare>` rc 0 → virtual/provides en
     repos → dejar a `makepkg -s` (usar el nombre pelado, no la constraint).
   - (e) `resolver.aur_info([...])` — **batchear** todas las deps no
     clasificadas del paquete en una llamada, no una RPC por dep; encontrada →
     recursión (clone + parseo).
   - (f) nada → `CommandExecutionError("AUR dependency '<dep>' required by
     '<parent>' not found in repos, AUR or installed system")` **antes de
     cualquier build/install**.
2. **Topo + ciclos**: DFS tricolor (blanco/gris/negro) sobre
   `{pkg: deps_aur}`; reentrada en gris → error listando el ciclo; post-orden
   = orden de build (deps antes que dependientes). Los clones durante la
   resolución son aceptables ("pre-mutación" = antes de instalar paquetes; los
   prerequisitos son lo único previo y el cleanup los revierte) — documentarlo
   en el docstring del módulo.
3. **Helper**: nunca en su propio `rest`; `rest` vacío → sin invocación del
   helper; ambos helpers declarados → construir el primero según `HELPERS` e
   instalar el otro vía él.
4. **Red**: `AurUnavailableError` durante la resolución de deps → abortar con
   mensaje reintentable (espejo de `_abort_unavailable`,
   `packages_action.py:456-466`); JAMÁS degradarla a "unknown".
5. **RunLogger**: prohibido `subprocess` crudo en `aur_installer.py` — todo
   vía `Command.execute` (check=True en mutaciones, stream=True en
   clone/makepkg/helper) para que el log contenga makepkg completo.
6. **Nombres**: `_validate_pkg_name` sobre declarados Y cada dep descubierta
   en `.SRCINFO` antes de tocar un argv o la URL
   `https://aur.archlinux.org/<name>.git`.
7. **Idempotencia**: `plan()` intacto (nombres instalados vía `pacman -Qq`);
   segundo apply no-op; las deps `--asdeps` no contaminan `pacman -Qqe` ni el
   `sync`.

### 5.6 Tests TDD

Patrón: clase `_Harness` estilo
`tests/lib/actions/test_pkgbuild_git_installer.py` —
`patch("dasik.lib.actions.aur_installer.Command.execute",
side_effect=harness.command_execute)` despachando por `cmd`/`args`
(`pacman -T` → rc según dict de satisfechos; `pacman -Sp` → rc según dict;
`makepkg --printsrcinfo` → srcinfo por pkg); resolver stub inyectado
(`repo_names`, `aur_info`) — **nunca red, nunca subprocess real**.

| Test | Escenario / asserts |
| --- | --- |
| `test_srcinfo.py::test_parse_depends_includes_make_and_check` | depends+makedepends+checkdepends unidos |
| `test_srcinfo.py::test_parse_depends_arch_suffixed_and_ignores_optdepends` | `depends_x86_64` entra; `optdepends` no |
| `test_srcinfo.py::test_strip_version_constraint_variants` | `foo>=2`, `foo<=1`, `foo=3`, `foo>1`, `foo<2`, `foo` → `foo` |
| `test_aur_installer.py::test_single_pkg_no_deps_builds_with_makepkg` | 1 clone + 1 `makepkg -sri` + verify `pacman -Q` |
| `::test_aur_dep_built_before_dependent_and_marked_asdeps` | **asunder→gtk2**: gtk2 AUR, no repo, no instalada → makepkg de gtk2 ANTES que asunder; `pacman -D --asdeps gtk2` tras su build; asunder sin `-D` |
| `::test_dep_satisfied_skipped` | `pacman -T` rc 0 → ni clone ni build de la dep |
| `::test_repo_dep_left_to_makepkg` | dep en `-Slq` → sin clone; makepkg -s la cubre |
| `::test_virtual_dep_resolved_via_sp` | dep no en -Slq pero `pacman -Sp` rc 0 → dejada a makepkg |
| `::test_unknown_dep_aborts_before_any_build` | error con dep + padre; **cero** makepkg/pacman -S/-U |
| `::test_dependency_cycle_raises` | A→B→A → error nombrando el ciclo, sin builds |
| `::test_aur_unavailable_aborts_retryable` | `aur_info` lanza `AurUnavailableError` → mensaje "retry", sin builds |
| `::test_helper_built_via_makepkg_then_rest_via_helper` | `["yay","asunder"]` → yay por clone+makepkg; asunder vía `yay -S --noconfirm --needed asunder` |
| `::test_helper_not_passed_to_itself` | rest sin yay |
| `::test_only_helper_declared_skips_helper_invocation` | rest vacío → ninguna invocación del helper |
| `::test_helper_invocation_safe_argv` | pkgs como argv posicionales (`exec "$@"`), no interpolados |
| `::test_cleanup_on_build_failure` | makepkg lanza → sudoers borrado + userdel llamado; excepción re-lanzada |
| `::test_preexisting_build_user_not_deleted` | `id` rc 0 → created False → sin userdel |
| `::test_prereq_pacman_failure_raises` | base-devel/git con check=True lanza |
| `::test_malicious_dep_name_rejected_before_argv` | dep `foo;rm -rf /` en .SRCINFO → rechazo antes de cualquier clone/argv |
| `::test_no_raw_subprocess_used` | `subprocess` del módulo parcheado para reventar si se toca |
| `::test_long_commands_stream` | clone/makepkg/helper reciben `stream=True` |
| `test_packages_action_v3.py::test_apply_aur_install_delegates_to_aur_installer` | patch de `AurInstaller`; recibe `resolution.aur` y el resolver |
| `test_pkgbuild_git_installer.py` (actualización) | mismos asserts sobre el nuevo patch target único |

**Aceptación**: asunder (con gtk2 AUR) se instala por ambas rutas; con `yay`
declarado, yay se construye por makepkg y el resto va por yay; dep desconocida
o AUR caída abortan **antes** del primer build con mensaje claro; un fallo a
mitad no deja `_aurbuilder` ni sudoers; todo makepkg/clone/yay en el log y en
vivo con `-v`; segundo apply no-op.

---

## 6. Verificación

**Gates por PR**: `pytest --cov=dasik` (≥80, no bajar el gate), `mypy dasik`,
`bandit -r dasik` (sin `# nosec` nuevos), pre-push hook activo
(`git config core.hooksPath .githooks`).

**Smoke no destructivo**: `dasik --help`, `python -m dasik --help`,
`dasik check config/install-megamix.json`,
`dasik plan config/install-megamix.json -v` (solo lectura; en máquina no-Arch
el fallo rápido `CommandNotFoundException` es esperado).

**Checklist de la reinstalación VM real** (test-config del usuario, tras
merge de las 3 PRs):

- `id andres` → grupos `docker libvirt wheel`; `getent passwd andres` →
  `/bin/zsh`.
- `pacman -Q asunder` presente; `pacman -Qi gtk2` →
  `Install Reason: Installed as a dependency…`.
- `id _aurbuilder` → "no such user"; `/etc/sudoers.d/_aurbuilder` no existe.
- Durante `dasik apply -v`: progreso de pacman/pacstrap/makepkg visible en
  vivo.
- El log `dasik-apply-*.log` contiene la salida completa de git clone y
  makepkg (hoy ausente).
- Segundo `dasik apply` → no-op (plan vacío en users y packages).
- Si el config declara `yay`: `pacman -Q yay` y el resto de AUR instalado vía
  helper (visible en el log).

## 7. Convenciones del repo (recordatorio al ejecutor)

- Una rama por PR (`fix/users-after-packages-and-check`,
  `feat/command-execute-streaming`, `feat/aur-hybrid-installer`); nunca push a
  `main`, nunca force-push, **nunca mergear** (ni PRs propias ni ajenas sin
  permiso explícito del usuario).
- Cada PR con sección **"How to test manually"** (invocaciones `dasik`
  exactas, flags, config con flags destructivos OFF, resultado esperado,
  re-run no-op) + **comentario de verificación agéntica** obligatorio
  (`gh pr comment`: build en venv scratch `pip install -e .[dev]`, smoke de
  verbos no destructivos, salida adjunta; advisory, nunca mergea).
- **NUNCA `git add -A`** — stage explícito; `config/mysystem.json*` y
  `test-config.json` son scratch locales que no se commitean.
- TDD estricto para toda la lógica nueva (`actions/`, `command_worker/`,
  `logging/`): test rojo primero. Jamás ejecutar `apply()`/`execute()` contra
  hardware real — `Command.execute`/`Popen` siempre mockeados.
