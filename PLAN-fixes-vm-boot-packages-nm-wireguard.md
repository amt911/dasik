# PLAN v3 — paquetes desconocidos no bloqueantes y fuentes PKGBUILD en Git

> Plan de implementación para Claude. Este documento sustituye al plan anterior:
> la resolución automática repo/grupo/AUR y las correcciones de arranque ya están
> implementadas en la rama `fix/observability-packages-boot`. Aquí se describe
> únicamente el siguiente cambio pendiente.

## 1. Objetivo

Hacer que una entrada de `packages` que no exista en ningún repositorio pacman,
grupo ni en AUR **no cancele toda la instalación**.

El comportamiento final debe ser:

1. Los nombres de `packages` siguen siendo nombres reales y limpios. No se
   recupera el prefijo `aur-` ni se incrusta una URL en cada entrada.
2. Un paquete realmente desconocido se omite con un aviso visible, el resto se
   instala y `dasik apply` termina correctamente.
3. Un fallo de red al consultar AUR no se confunde con “no existe”: sigue siendo
   un error bloqueante y reintentable.
4. Los PKGBUILD públicos que viven fuera de AUR pueden declararse mediante un
   mapa separado `package_sources`.
5. Dasik nunca busca ni elige automáticamente un repositorio de GitHub a partir
   del nombre del paquete.

## 2. Diagnóstico confirmado

El error observado no procede de pacman. Lo lanza deliberadamente
`PackagesAction._abort_unresolved()` antes de instalar nada cuando
`PackageResolution.unknown` no está vacío.

En este caso los tres nombres son desconocidos para los repos configurados y
para AUR, pero sí tienen PKGBUILD públicos:

| Paquete declarado | Repositorio PKGBUILD correcto |
| --- | --- |
| `config-saver` | `https://github.com/amt911/config-saver-aur.git` |
| `ttf-atkinson-hyperlegible-next-nerd-git` | `https://github.com/amt911/ttf-atkinson-hyperlegible-nerd.git` |
| `ttf-atkinson-hyperlegible-next-nerd-mono-git` | `https://github.com/amt911/ttf-atkinson-hyperlegible-mono-nerd.git` |

No se debe deducir esta relación mediante una búsqueda. Los nombres de dos
repositorios no coinciden con los paquetes y `config-saver` tiene un repositorio
de código y otro de empaquetado. Una heurística podría instalar código ajeno o
el repositorio equivocado.

La batería actual del resolvedor está verde (`94 passed` en los tests de
resolver, acción y modelos seleccionados). Por tanto, el fallo es la política
estricta actual, no una regresión accidental.

## 3. Decisión de producto

### 3.1 Política por defecto

Usar `warn-and-skip` como comportamiento por defecto para un nombre confirmado
como inexistente:

- se muestra el nombre omitido en amarillo;
- se escribe el aviso en el log de ejecución;
- se instalan todos los nombres que sí pudieron resolverse;
- el proceso sale con código `0` si el resto termina bien;
- el nombre omitido no se registra como paquete administrado;
- se vuelve a intentar en el próximo `apply`, por si en el futuro aparece en un
  repo o en AUR.

Mantener un modo estricto optativo para CI o configuraciones que quieran detectar
erratas inmediatamente:

```json
{
  "package_policy": {
    "unknown": "error"
  }
}
```

Valores aceptados:

- `"warn-and-skip"` — valor por defecto.
- `"error"` — conserva el aborto anterior, antes de cualquier mutación de
  paquetes.

### 3.2 Fuentes Git explícitas

Los paquetes propios no deberían depender del mecanismo de omisión: queremos
instalarlos. Declarar su origen aparte mantiene limpia la lista principal:

```json
{
  "packages": [
    "firefox",
    "config-saver",
    "ttf-atkinson-hyperlegible-next-nerd-git",
    "ttf-atkinson-hyperlegible-next-nerd-mono-git"
  ],
  "package_sources": {
    "config-saver": {
      "type": "pkgbuild-git",
      "url": "https://github.com/amt911/config-saver-aur.git",
      "ref": "a520605367e13ec25db4c3c7e1c4bf46175ba8cd"
    },
    "ttf-atkinson-hyperlegible-next-nerd-git": {
      "type": "pkgbuild-git",
      "url": "https://github.com/amt911/ttf-atkinson-hyperlegible-nerd.git",
      "ref": "51d259d4fbee428a2b4eebb43caeea65079707b3"
    },
    "ttf-atkinson-hyperlegible-next-nerd-mono-git": {
      "type": "pkgbuild-git",
      "url": "https://github.com/amt911/ttf-atkinson-hyperlegible-mono-nerd.git",
      "ref": "79076f84339a7afb485b8bd11a92f0a5681b6394"
    }
  }
}
```

Los SHA anteriores fijan el estado actual comprobado. Cuando se quiera actualizar
un paquete se cambia conscientemente su `ref`. Se puede admitir una etiqueta o
rama por comodidad, pero debe mostrarse un aviso de que no es reproducible. Para
la primera implementación es preferible exigir un SHA completo de 40 caracteres.

### 3.3 Precedencia

Clasificar cada nombre en este orden exacto:

1. repositorio pacman configurado;
2. grupo pacman;
3. fuente `package_sources` explícita;
4. AUR por nombre exacto;
5. desconocido.

Un paquete que pase a un repositorio oficial seguirá migrando automáticamente a
él. Una fuente Git declarada gana frente a AUR para evitar que un paquete AUR con
el mismo nombre suplante accidentalmente la fuente elegida por el usuario.

## 4. Cambios de configuración y modelos

### Archivos

- `dasik/lib/models/package_model.py`
- `dasik/lib/models/json_model.py`
- tests de modelos/configuración correspondientes

### Implementación

1. Añadir `PackagePolicyModel`:

   - `unknown: Literal["warn-and-skip", "error"]`;
   - valor por defecto `"warn-and-skip"`.

2. Añadir `GitPackageSourceModel`:

   - `type: Literal["pkgbuild-git"]`;
   - `url: str`;
   - `ref: str`;
   - `subdir: str = "."` para un PKGBUILD en un subdirectorio.

3. Añadir a `JsonModel`:

   - `package_policy: PackagePolicyModel` con `default_factory`;
   - `package_sources: Dict[str, GitPackageSourceModel]` con mapa vacío por
     defecto.

4. Validar antes de ejecutar:

   - la clave del mapa cumple la gramática de nombre Arch existente;
   - cada clave también aparece en `packages` después de normalizar la forma
     `{name, reason}`;
   - solo se permiten URLs HTTPS;
   - en la primera versión, limitar URLs a `github.com` y exigir sufijo `.git`;
   - `ref` es un SHA hexadecimal completo de 40 caracteres;
   - `subdir` es relativo, normalizado, no contiene `..`, no empieza por `/` y
     no escapa de la raíz clonada;
   - los campos adicionales siguen la política Pydantic actual del proyecto.

5. Corregir la documentación obsoleta de `PackageSpec` y la descripción de
   `JsonModel.packages`: AUR ya no requiere `aur-`.

No añadir el origen dentro de la lista `packages`. Esta separación permite que
`sync` siga trabajando con nombres reales y que una misma entrada pueda cambiar
de origen sin reescribir la lista.

## 5. Hacer que `PackagesAction` reciba la configuración raíz

### Archivos

- `dasik/lib/actions/actions_handler_v2.py`
- `dasik/lib/actions/packages_action.py`
- `tests/lib/actions/test_action_registry.py`
- tests del reconciler que comprueban acciones `__root__`

### Implementación

Actualmente la acción está registrada con `config_key='packages'`; así no puede
leer `package_sources` ni `package_policy`.

1. Registrar `PackagesAction` con `config_key='__root__'`.
2. En su constructor, si recibe un diccionario raíz, extraer:

   - `config.get("packages", [])`;
   - `config.get("package_sources", {})`;
   - `config.get("package_policy", {})`.

3. Conservar temporalmente la aceptación de una lista directa para no romper los
   tests unitarios ni llamadas internas existentes.
4. Revisar `empty_config()` y las sondas de `managed_keys()` del reconciler para
   que una configuración sin `packages` continúe siendo un no-op seguro.
5. Añadir una prueba de integración que demuestre que la instancia creada por el
   `Reconciler` recibe las tres secciones.

No pasar estas opciones mediante variables globales ni duplicarlas en
`ActionContext`: pertenecen a la configuración declarativa de la acción.

## 6. Extender la resolución de origen

### Archivos

- `dasik/lib/actions/package_resolver.py`
- `tests/lib/actions/test_package_resolver.py`

### Implementación

1. Añadir a `PackageResolution` una colección para fuentes Git que conserve
   nombre y metadatos, por ejemplo:

   ```python
   @dataclass(frozen=True)
   class ResolvedGitPackage:
       name: str
       source: GitPackageSource
   ```

2. Hacer que `PackageResolver.resolve()` acepte el mapa ya validado de fuentes.
3. Tras consultar una sola vez los nombres y grupos pacman:

   - clasificar primero repo/grupo;
   - clasificar después los nombres con fuente Git explícita;
   - enviar al RPC de AUR únicamente los candidatos restantes.

4. No consultar AUR para un nombre que tenga fuente Git seleccionada.
5. Mantener el orden declarado y la deduplicación actual.
6. Mantener separados:

   - `unknown`: la consulta se completó y el nombre no existe;
   - `unavailable`: no se pudo saber si existe por fallo de red, DNS, HTTP o
     respuesta inválida.

`PackageResolver` solo clasifica. La decisión de omitir o abortar por
`unknown` pertenece a `PackagesAction`, porque depende de `package_policy`.

## 7. Aplicar `warn-and-skip` sin ocultar errores reales

### Archivos

- `dasik/lib/actions/packages_action.py`
- `dasik/lib/logging/run_logger.py`
- `tests/lib/actions/test_packages_action_v3.py`
- `tests/lib/logging/test_run_logger.py`

### Flujo de `apply`

1. Resolver **todo** el conjunto `INSTALL` antes de la primera mutación.
2. Si existe cualquier `unavailable`, abortar antes de llamar a pacman,
   `git clone` o `makepkg`. El mensaje debe decir que la existencia no pudo
   comprobarse y que se debe reintentar.
3. Si existen `unknown` y la política es `error`, abortar como hoy.
4. Si existen `unknown` y la política es `warn-and-skip`:

   - guardarlos en `self._skipped_unknown`;
   - emitir un único aviso estable y ordenado;
   - excluirlos de las listas que se pasan a pacman/AUR/Git;
   - continuar con todos los resolubles.

5. Un fallo de pacman, Git, validación de PKGBUILD o `makepkg` continúa siendo un
   error y debe devolver código distinto de cero. “Ignorar” solo afecta a un
   nombre confirmado como inexistente.

### Aviso esperado

```text
warning: packages skipped because no source was found: foo, bar
         They were not installed; dasik will retry them on the next apply.
```

Añadir `RunLogger.warning(message, detail="")`:

- amarillo en la consola cuando hay color;
- prefijo `[WARNING]` sin códigos ANSI en el fichero de log;
- siempre visible, incluso sin `--verbose`;
- mismo estilo de pruebas que `RunLogger.error()`.

### Estado administrado

`managed_keys()` debe devolver `desired - _skipped_unknown` después de `apply`.
Así el manifiesto no afirma que dasik administra algo que nunca llegó a instalar.
La entrada permanece en la configuración y por eso vuelve a intentarse en el
próximo `apply`.

No crear una operación `SKIP` en `Op`: no es una mutación del sistema. Es
aceptable que `dasik plan` siga mostrando `INSTALL` para el nombre pendiente;
durante `apply` se resuelve, se avisa y se omite. Esto además permite reintentar
automáticamente si aparece más adelante.

Cubrir el caso donde todos los `INSTALL` son desconocidos: el comando debe
terminar correctamente, guardar un manifiesto que no los marque como
administrados y no ejecutar ninguna orden de instalación.

## 8. Instalador para fuentes `pkgbuild-git`

### Archivos

- `dasik/lib/actions/packages_action.py`, o preferiblemente un nuevo
  `dasik/lib/actions/pkgbuild_git_installer.py`
- tests unitarios nuevos para el instalador

Separar este código del resolvedor: resolver decide **de dónde** viene el
paquete; el instalador se ocupa de construirlo.

### Flujo seguro mínimo

Por cada `ResolvedGitPackage`:

1. Instalar una sola vez los prerrequisitos `base-devel` y `git` en el target.
2. Crear/reutilizar el usuario de compilación temporal existente
   `_aurbuilder`; nunca ejecutar `makepkg` como root.
3. Limpiar únicamente su directorio de trabajo explícito.
4. Clonar la URL declarada sin interpolar valores en una cadena shell.
5. Resolver `ref`, comprobar que el commit obtenido coincide exactamente con el
   SHA configurado y hacer checkout detached.
6. Resolver y validar `subdir`; comprobar que dentro existe `PKGBUILD`.
7. Obtener metadatos:

   - usar `.SRCINFO` si existe y es válido;
   - si falta, ejecutar `makepkg --printsrcinfo` como `_aurbuilder`;
   - comprobar que la lista `pkgname` incluye exactamente el nombre configurado.

8. Si la identidad no coincide, abortar antes de instalar con un error como:

   ```text
   PKGBUILD source for config-saver produces other-package; refusing install
   ```

9. Construir como usuario sin privilegios.
10. Instalar los artefactos `.pkg.tar.*` con pacman y verificar finalmente
    `pacman -Q <nombre esperado>`.
11. En un bloque `finally`, eliminar el directorio temporal, el usuario temporal
    si lo creó esta ejecución y cualquier fragmento sudoers temporal.

### Dependencias y privilegios

Evitar ampliar el `NOPASSWD: ALL` actual para la nueva ruta. Preferencia:

1. leer `depends`, `makedepends` y `checkdepends` desde `.SRCINFO`;
2. instalar las dependencias de repositorio con pacman como root;
3. ejecutar `makepkg --cleanbuild --nodeps --noconfirm` como `_aurbuilder`;
4. ejecutar `pacman -U --noconfirm --needed <artefactos>` como root.

En esta primera versión, si una dependencia de compilación no está instalada ni
existe en repos pacman, fallar con un mensaje que pida declararla en `packages`.
No implementar resolución recursiva arbitraria de PKGBUILDs en este cambio.

Reutilizar `_su_argv()`/argv posicionales y `Target` para no introducir shell
injection. Nunca concatenar URL, SHA, ruta o nombre dentro de un script shell.

### Repositorios externos

Como mantenimiento independiente, conviene:

- generar y versionar `.SRCINFO` en los tres repositorios PKGBUILD;
- corregir el fichero `.SCRINFO` de
  `ttf-atkinson-hyperlegible-nerd` para que se llame `.SRCINFO`;
- añadir etiquetas/releases si se prefiere actualizar por versión.

Dasik debe conservar el fallback `makepkg --printsrcinfo`, por lo que esas
mejoras no son requisito para completar este PR.

## 9. Integración con el flujo existente

En `PackagesAction.apply()` separar las salidas de la resolución:

```text
repo + groups -> una transacción pacman -S
pkgbuild-git  -> instalador Git explícito
AUR           -> ruta AUR existente
unknown       -> warning+skip o error según política
unavailable   -> error siempre
```

Orden de mutación recomendado:

1. validar por completo la resolución y todas las fuentes configuradas;
2. pacman repo/grupos;
3. fuentes Git explícitas;
4. AUR;
5. cambios de razón (`pacman -D`);
6. eliminaciones declarativas existentes.

No cambiar el comportamiento de las eliminaciones en este trabajo. Aplicar la
razón `explicit`/`dep` también a un paquete instalado mediante fuente Git,
porque una vez instalado es un paquete pacman normal.

## 10. `sync` e idempotencia

### Requisitos

1. `sync` sigue escribiendo nombres reales, nunca `aur-` ni URLs dentro de
   `packages`.
2. `sync` conserva `package_sources` y `package_policy` ya presentes mediante la
   fusión normal de configuración.
3. No inferir una fuente Git desde `pacman -Qi URL`: puede apuntar al proyecto y
   no al repositorio que contiene el PKGBUILD, como ocurre con `config-saver`.
4. Un paquete Git ya instalado satisface `actual()` mediante `pacman -Qq` y no se
   recompila en el siguiente `apply`.
5. Cambiar solamente `ref` debe provocar una actualización del paquete aunque
   el nombre ya esté instalado.

El punto 5 necesita estado adicional. Implementarlo explícitamente:

1. Añadir a `Manifest` un diccionario top-level `action_state` y subir la versión
   del esquema. `from_dict()` debe aceptar manifiestos anteriores sin ese campo.
2. Guardar en `action_state["packages"]["source_refs"]` el mapa
   `{nombre: sha_aplicado}`. No mezclarlo con `managed.packages`, que debe seguir
   siendo una lista de nombres.
3. Añadir a `AbstractAction` un método vacío `state_metadata()` y hacer que el
   `Reconciler` fusione el estado devuelto por cada acción al construir el nuevo
   manifiesto. Evitar lógica especial para `PackagesAction` dentro del
   reconciler.
4. `PackagesAction.plan()` lee el SHA aplicado desde `ActionContext.manifest` y
   emite `MODIFY` con razón `source ref changed` cuando no coincide con el SHA
   deseado, aunque el paquete ya esté instalado.
5. `PackagesAction.apply()` distingue ese `MODIFY` del cambio de razón de
   instalación, reconstruye el paquete Git y solo actualiza `state_metadata()`
   después de verificar una instalación correcta.
6. Eliminar del estado las refs de paquetes que ya no están declarados o ya no
   tienen una fuente Git explícita.
7. `sync` conserva ese estado para fuentes declaradas e instaladas; un sync no
   debe fingir que aplicó un SHA que no conoce.

Archivos adicionales: `dasik/lib/state/state_store.py`,
`dasik/lib/actions/abstract_action.py`, `dasik/lib/reconciler/reconciler.py` y sus
tests. No usar solo la versión instalada: dos commits pueden producir el mismo
`pkgver`.

## 11. Matriz de pruebas obligatoria

### Modelos y validación

- configuración sin los nuevos campos usa `warn-and-skip` y mapa vacío;
- mapa válido con los tres ejemplos;
- clave no declarada en `packages` rechazada;
- URL HTTP, host no permitido, SHA corto/malicioso y `subdir` con traversal
  rechazados;
- formas string y `{name, reason}` se relacionan correctamente con el mapa.

### Resolver

- precedencia repo > grupo > Git explícito > AUR > unknown;
- un nombre con fuente Git no se envía al RPC AUR;
- orden y deduplicación estables;
- respuesta AUR vacía produce `unknown`;
- excepción de red produce `unavailable`, nunca `unknown`.

### Política de aplicación

- `[repo válido, desconocido]` instala el válido, avisa por el otro y sale `0`;
- `[AUR válido, desconocido]` construye el válido y omite el otro;
- solo desconocidos no ejecuta pacman/Git/makepkg y sale `0`;
- modo `error` aborta antes de cualquier mutación;
- AUR no disponible aborta antes de cualquier mutación incluso en
  `warn-and-skip`;
- error real de pacman/makepkg/Git continúa propagándose;
- el manifiesto no incluye los nombres omitidos;
- el log contiene `[WARNING]` y la consola amarilla no deja ANSI en el fichero.

### Instalador Git

- clona URL y checkout del SHA configurado con argv seguro;
- rechaza un commit diferente al esperado;
- acepta `.SRCINFO` correcto y usa `makepkg --printsrcinfo` si falta;
- rechaza PKGBUILD cuya identidad no coincide;
- instala y verifica el paquete esperado;
- detecta cambio de `ref` y emite actualización;
- limpia usuario, sudoers y directorio en éxito y en cada punto de fallo;
- no ejecuta `makepkg` como root;
- entradas maliciosas no llegan a comandos.

### Reconciler y `sync`

- el registro `__root__` entrega las tres secciones a `PackagesAction`;
- ausencia de `packages` es no-op;
- round-trip conserva `package_sources` y `package_policy`;
- `sync` no inventa fuentes desde metadatos del paquete;
- segundo `apply` con mismo SHA no produce cambios.

### VM real

Crear una configuración pequeña que contenga:

- un paquete oficial conocido;
- uno de los tres repositorios Git fijado por SHA;
- un nombre imposible, por ejemplo `dasik-package-does-not-exist-12345`.

Verificar en una VM Arch limpia:

```bash
dasik -v apply config/vm-unknown-git.json --yes
pacman -Q <paquete-oficial>
pacman -Q config-saver
pacman -Q dasik-package-does-not-exist-12345  # debe fallar: no instalado
dasik plan config/vm-unknown-git.json
```

Resultado esperado: `apply` termina `0`, los dos resolubles están instalados, el
falso aparece en un warning y el segundo `apply` no recompila `config-saver`.

## 12. Documentación

Actualizar:

- `docs/config-reference.md`;
- `docs/vm-testing.md` con el escenario anterior;
- ejemplos de configuración que aún documenten `aur-`;
- docstrings de `package_resolver.py`, `packages_action.py`, `package_model.py` y
  `json_model.py`.

La referencia debe explicar claramente:

- los nombres de `packages` no codifican el origen;
- repo/grupo/AUR se resuelven automáticamente;
- `package_sources` solo es necesario para PKGBUILDs externos;
- un desconocido se avisa y omite por defecto;
- una caída de AUR es un error, no una omisión;
- fijar un SHA protege la reproducibilidad, pero un PKGBUILD sigue siendo código
  de terceros y debe considerarse de confianza.

## 13. Orden de implementación sugerido

1. **Modelos y root config**
   - modelos nuevos, validación, registro `__root__`, compatibilidad con lista.
2. **Resolver**
   - nuevo bucket Git, precedencia, pruebas sin red.
3. **Política unknown**
   - warning, skip, estado administrado y modo estricto.
4. **Instalador Git**
   - checkout fijado, identidad PKGBUILD, build sin root, cleanup.
5. **Estado de refs e idempotencia**
   - detectar actualización de SHA.
6. **Integración y documentación**
   - sync, config de VM, docs y prueba real.

Mantener los commits pequeños y verdes. No mezclar en este PR las correcciones
de LUKS/dracut/NetworkManager/WireGuard ya realizadas en la rama.

## 14. Puertas de calidad

Ejecutar al menos:

```bash
.venv/bin/pytest -q \
  tests/lib/actions/test_package_resolver.py \
  tests/lib/actions/test_packages_action_v3.py \
  tests/lib/actions/test_packages_action_validation.py \
  tests/lib/logging/test_run_logger.py \
  tests/lib/reconciler \
  tests/lib/models

.venv/bin/pytest -q
ruff check dasik tests
```

Si el proyecto usa otro comando canónico de lint/typecheck en CI, usar el de la
configuración del repositorio y documentar cualquier fallo previo no relacionado.

## 15. Criterios de aceptación

- [ ] Un paquete confirmado como inexistente ya no impide instalar los demás.
- [ ] La omisión siempre es visible en consola y log.
- [ ] `unknown` y `unavailable` conservan semánticas diferentes.
- [ ] El modo estricto sigue disponible.
- [ ] `packages` usa únicamente nombres reales, sin `aur-` ni URLs.
- [ ] Los tres paquetes propios pueden instalarse desde sus repos Git explícitos.
- [ ] No existe búsqueda heurística ni selección automática de repositorios
      GitHub.
- [ ] El PKGBUILD se valida contra el nombre esperado antes de instalarlo.
- [ ] `makepkg` nunca se ejecuta como root y el cleanup funciona ante fallos.
- [ ] Los paquetes omitidos no se registran como administrados.
- [ ] Un SHA sin cambios no recompila; un SHA nuevo genera actualización.
- [ ] `sync` conserva nombres limpios y el mapa de fuentes.
- [ ] Tests unitarios, integración, suite completa y VM Arch pasan.

## 16. Recomendación a largo plazo

Para tres paquetes, `package_sources` es el mejor equilibrio: explícito, seguro,
reproducible y sin obligar a publicar en AUR.

Si el número de paquetes propios crece, la solución más limpia será crear un
repositorio pacman firmado, publicarlo por ejemplo mediante GitHub Pages/Releases
y configurarlo en `pacman.conf`. Entonces dasik los detectará automáticamente
como paquetes de repositorio y no hará falta un mapa por paquete. Publicarlos en
AUR también es válido cuando sean útiles para la comunidad y cumplan sus normas,
pero no debería ser un requisito para que una configuración personal funcione.
