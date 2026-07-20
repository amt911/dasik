# Informe forense integral del intento de instalación de Dasik del 19 de julio de 2026

- **Estado del informe:** diagnóstico y traspaso técnico; no contiene correcciones aplicadas.
- **Repositorio examinado:** `dasik`, rama `main`, commit `b9523f3952adef126b086dc3204dbb5423fa101d`.
- **Intento examinado:** `log-llm.log`, configuración `config/test-config.json`.
- **Fecha de contraste externo:** 19 de julio de 2026, zona horaria Europe/Madrid.

**Conclusión operativa:** no repetir todavía `dasik apply`; el destino parcial no debe considerarse arrancable ni convergido.

> [!CAUTION]
> Dasik particiona discos, crea sistemas de archivos, configura LUKS y ejecuta `pacman` contra `/mnt`. Durante esta investigación no se ejecutó `apply`, `rollback`, `execute()` ni ninguna instalación real. Tampoco se modificaron código, configuración o destino. Cualquier continuación debe mantener esta separación entre análisis seguro y ejecución destructiva.

> [!IMPORTANT]
> `config/test-config.json` contiene hashes de contraseña y una credencial LUKS. Es un archivo ignorado por Git y esos valores se han omitido deliberadamente de este informe. No deben copiarse a incidencias, prompts, commits, PR, salidas de CI ni servicios externos. Las credenciales utilizadas en una máquina real deben considerarse sensibles y rotarse si se han compartido fuera de un entorno controlado.

## Índice

1. [Resumen ejecutivo](#1-resumen-ejecutivo)
2. [Alcance, método y límites](#2-alcance-método-y-límites)
3. [Perfil de la instalación examinada](#3-perfil-de-la-instalación-examinada)
4. [Cronología reconstruida](#4-cronología-reconstruida)
5. [Causa inmediata del aborto](#5-causa-inmediata-del-aborto)
6. [Qué se instaló y qué ocurriría al reintentar](#6-qué-se-instaló-y-qué-ocurriría-al-reintentar)
7. [Estado real del destino después del aborto](#7-estado-real-del-destino-después-del-aborto)
8. [Inventario priorizado de hallazgos](#8-inventario-priorizado-de-hallazgos)
9. [Análisis detallado por subsistema](#9-análisis-detallado-por-subsistema)
10. [Anomalías secundarias y causas descartadas](#10-anomalías-secundarias-y-causas-descartadas)
11. [Por qué los tests no detectaron estos problemas](#11-por-qué-los-tests-no-detectaron-estos-problemas)
12. [Orden recomendado de corrección](#12-orden-recomendado-de-corrección)
13. [Criterios de aceptación](#13-criterios-de-aceptación)
14. [Matriz de evidencias](#14-matriz-de-evidencias)
15. [Decisiones funcionales todavía necesarias](#15-decisiones-funcionales-todavía-necesarias)
16. [Prompt maestro para otro LLM](#16-prompt-maestro-para-otro-llm)
17. [Respuesta anterior, reproducida íntegramente](#17-respuesta-anterior-reproducida-íntegramente)

## 1. Resumen ejecutivo

El intento avanzó mucho más que los anteriores y llegó a instalar la mayoría del sistema base, los paquetes oficiales y numerosos paquetes AUR. El error final **no fue una compilación fallida de `llama.cpp-cuda` ni de `rpcs3-git`**: ambos terminaron de compilar y fueron instalados. El lote AUR devolvió código 1 al final porque tres paquetes distintos fallaron:

| Paquete | Fallo observado | Causa más probable y contrastada | Naturaleza |
| --- | --- | --- | --- |
| `sunshine` | CMake no puede importar `pkg_resources`; después no encuentra `pip` ni puede usar `ensurepip` | Sunshine comprueba `import jinja2; import pkg_resources`; setuptools 82 eliminó `pkg_resources` y Arch lo separó en `python-pkg_resources`; la receta usada no cerró esa dependencia | Incompatibilidad temporal upstream/AUR/Arch |
| `epson-inkjet-printer-escpr` | HTTP 403 descargando el RPM de Epson | El endpoint exacto de `download-center.epson.com` rechaza la descarga, también al repetirla con un agente de navegador | Fuente externa no disponible para el método del PKGBUILD |
| `epsonscan2` | HTTP 403 descargando el tarball de Epson | El segundo endpoint exacto de Epson también devuelve 403 | Fuente externa no disponible para el método del PKGBUILD |

El defecto sistémico que convirtió tres paquetes periféricos en el fracaso de toda la instalación es que `PackagesAction` entrega un lote AUR grande a `yay` con `check=True`. `yay` continúa compilando e instala los éxitos, pero al final devuelve un código distinto de cero por los tres fallos. La excepción detiene el reconciliador antes de `Users`, `Systemd`, `Firewall`, `Snapper`, `DropFiles`, `Zram`, el initramfs definitivo y el bootloader. La mutación parcial no se registra como nueva generación porque la generación y el manifiesto sólo se guardan después de que todas las acciones terminen.

El resultado es una instalación parcial con una cantidad importante de paquetes ya presentes, pero sin cierre transaccional ni garantía de arranque. En concreto:

- la partición EFI y la raíz LUKS/Btrfs fueron recreadas;
- los paquetes oficiales y 39 artefactos del último lote AUR se instalaron;
- `rpcs3-git` y `llama.cpp-cuda` están entre esos éxitos;
- no se crearon los usuarios declarados por la acción de Dasik;
- no se habilitaron de forma declarativa los servicios posteriores;
- no se aplicó la regla final de firewall;
- no se configuró Snapper;
- no se escribieron a tiempo los hooks que impiden que mkinitcpio pise a Dracut;
- no se ejecutó el backend final de Dracut;
- no se ejecutó la acción final de systemd-boot ni la de parámetros del kernel;
- no se guardó una generación que represente fielmente el estado parcial.

La siguiente ejecución no debería recompilar los paquetes que `pacman -Qq` ya vea instalados, por lo que el trabajo largo de `llama.cpp-cuda` y `rpcs3-git` debería conservarse. Sin embargo, volvería a intentar los tres fallos y los tres nombres desconocidos; con las mismas fuentes actuales, no alcanzaría las fases de arranque.

La investigación encontró, además, varios fallos que todavía no habían podido manifestarse porque `PackagesAction` abortó antes:

1. el usuario `andres` exige el grupo `docker`, pero el conjunto de paquetes sólo instala la emulación de CLI `podman-docker` y `docker-buildx`, no el paquete que crea el grupo;
2. la configuración intenta habilitar `sddm.service`, mientras el Plasma actual instalado por `plasma-meta` usa `plasmalogin.service`;
3. `SystemdAction` no comprueba el código de salida de `systemctl`, de modo que ese fallo podría presentarse como éxito;
4. la conversión de reglas ricas de firewalld elimina silenciosamente el límite SSH de `2/m`;
5. los hooks neutralizadores de mkinitcpio se escriben después de instalar paquetes, demasiado tarde para las transacciones iniciales;
6. el último initramfs del log fue generado por mkinitcpio sin `sd-encrypt`, después del hook de Dracut, y sobrescribió el nombre que cargaría el bootloader;
7. el backend de Dracut decide convergencia mirando archivos de configuración, no la existencia o validez de la imagen generada;
8. la detección usada por `sync` considera mkinitcpio activo siempre que esté instalado, aunque el diseño de Dasik lo mantiene instalado y lo neutraliza cuando Dracut es el generador real;
9. `/etc/crypttab` contiene una entrada para un `LABEL=cryptswap` inexistente y la opción inválida `size512`;
10. Snapper y `snap-pac` están instalados, pero falta la sección declarativa `snapper`; además, la configuración de Snapper se ejecuta después de las transacciones que deberían protegerse;
11. `plan` y `apply` no atraviesan `JsonModel.model_validate()`; sólo lo hace el verbo separado `check`;
12. la política predeterminada `warn-and-skip` permite terminar sin paquetes declarados y repetir indefinidamente esa divergencia;
13. varios comandos críticos de systemd y bootloader usan el valor predeterminado `check=False`;
14. `/var/tmp` fue creado con modo 0755 antes de pacstrap, no 1777;
15. un paquete `lib32-gstreamer` fue empaquetado pese a cinco suites fallidas y la indexación de depuración de `btdu` fue terminada con `Killed`.

Los hallazgos no justifican afirmar que Dasik sea irrecuperable. Sí justifican detener los reintentos completos hasta corregir la secuencia de boot, los falsos éxitos y las incoherencias de la configuración. El objetivo de este documento es permitir que un desarrollador o un LLM continúe desde evidencia concreta, sin repetir la autopsia ni confundir síntomas ruidosos con la causa real.

## 2. Alcance, método y límites

### 2.1 Pregunta investigada

Se investigó por qué el intento más reciente falló después de aproximadamente una hora de compilación y qué otros fallos previsibles existen antes de volver a ejecutar el instalador o modificar el sistema.

### 2.2 Artefactos examinados

- `log-llm.log`: 70.426 líneas, 6.744.556 bytes, mtime `2026-07-19 22:30:26 +0200`; es el log del intento más reciente. SHA-256: `647602c39410243144e84d062a93280b6df369f59d8b1f2fa83dafff0829bb0e`.
- `config/test-config.json`: configuración exacta utilizada. Está ignorada por Git y contiene secretos que no se reproducen.
- `dasik/__main__.py`: entrada de `plan`, `apply`, `sync`, `check`, generaciones y rollback.
- `dasik/lib/reconciler/reconciler.py`: orden de aplicación y persistencia del manifiesto.
- `dasik/lib/actions/actions_handler_v2.py`: registro y orden de acciones.
- Acciones de paquetes/AUR, usuarios, systemd, firewall, Snapper, archivos, initramfs, bootloader, discos y base install.
- Tests correspondientes bajo `tests/`.
- Historial reciente de Git, especialmente `b9523f3`, que ya corrige el problema anterior de separación de opciones de `su` y reutilización del helper AUR.
- Fuentes oficiales vigentes de Sunshine, setuptools, Arch Linux, firewalld y systemd/crypttab.

El repositorio inspeccionado estaba en `b9523f3952adef126b086dc3204dbb5423fa101d` y alineado con `origin/main`. El log no incorpora el SHA del ejecutable que lo generó. Su forma —incluido el terminador `--` del comando `su` añadido por `b9523f3`— es compatible con ese HEAD, pero no constituye una prueba criptográfica de que cada línea proceda exactamente de esa revisión. Un futuro run debe registrar el SHA al inicio.

No se publica un hash de la configuración original dentro de este informe compartible: contiene secretos y no se creó una copia saneada canónica. Si se necesita cadena de custodia, debe calcularse y guardarse el hash original en un anexo privado, junto con el hash de una copia redactada destinada a revisión.

No se enumeró ni se leyó en masa `resources/`. Sólo se usaron fuentes externas y archivos concretos del paquete activo.

### 2.3 Acciones realizadas

- búsquedas y lecturas locales dirigidas;
- ejecución de funciones puras para comprobar transformaciones, como la conversión de la regla de firewall;
- peticiones HTTP de rango cero a los dos endpoints de Epson, sin descargar los paquetes;
- `dasik check config/test-config.json`, sólo validación;
- suite completa con caché y bytecode desactivados: `1098 passed, 17 warnings`;
- comprobación de `git status` para confirmar que la investigación previa no modificó archivos versionados.

### 2.4 Acciones deliberadamente no realizadas

- no se ejecutó `dasik apply` ni `dasik rollback`;
- no se ejecutó ninguna acción `execute()` contra hardware;
- no se montó, formateó ni modificó `/dev/vda`;
- no se entró en el target con comandos mutantes;
- no se instalaron dependencias ni herramientas;
- no se modificó la configuración para “probar suerte”;
- no se hizo push, merge ni PR;
- no se divulgó ninguna contraseña, passphrase o hash.

### 2.5 Límites de certeza

El target `/mnt` ya no estaba montado durante el cierre de la investigación, por lo que no se pudo realizar una inspección viva de su `/etc/group`, su ESP o sus imágenes de boot. El estado se reconstruye a partir del log completo, del orden determinista del registro y del código. Por ello este informe distingue:

- **Hecho probado:** aparece directamente en log, configuración o código.
- **Inferencia fuerte:** consecuencia determinista de hechos probados, pero no verificada leyendo ahora el destino.
- **Hipótesis:** explicación plausible sin evidencia suficiente; no debe implementarse como si fuera una causa confirmada.

Las recetas AUR y los repositorios de Arch son móviles. Las afirmaciones externas fueron contrastadas el 19 de julio de 2026 y deben verificarse de nuevo antes de implementar una corrección permanente.

## 3. Perfil de la instalación examinada

La configuración representa una estación de trabajo amplia, no una instalación mínima:

| Rasgo | Valor declarado u observado |
| --- | --- |
| Destino | `/dev/vda`, dispositivo Virtio de 80 GiB según el log |
| Tabla | GPT, borrado completo solicitado |
| Particiones | ESP FAT32 de aproximadamente 1 GiB y raíz con el resto |
| Raíz | LUKS2 `cryptroot`, Btrfs con subvolúmenes |
| Subvolúmenes | `@`, `@snapshots`, `@home`, `@srv`, `@var_abs`, `@var_cache`, `@var_lib_libvirt`, `@var_log`, `@var_tmp` |
| Initramfs deseado | Dracut |
| Bootloader deseado | systemd-boot |
| Microcódigo | habilitado; AMD declarado |
| Paquetes declarados | 311 |
| Usuarios declarados | 2 |
| Unidades systemd | 27 unidades y 13 sockets a habilitar |
| Escritorio | KDE Plasma mediante `plasma-meta` |
| Contenedores | Podman, `podman-docker`, Buildx; no Docker Engine |
| Snapshots | paquetes `snapper` y `snap-pac`, timers y subvolumen; sin sección `snapper` |
| Swap | ZRAM declarada; además existe una línea heredada de cryptswap sin dispositivo correspondiente |
| Firewall | firewalld; SSH retirado del servicio por defecto y reañadido mediante regla rica con límite `2/m` |

La amplitud de la configuración importa: un lote de 311 paquetes mezcla componentes esenciales de arranque, escritorio, controladores, herramientas de desarrollo, aplicaciones grandes y paquetes AUR opcionales. La política de fallo no distingue actualmente qué bloquea la capacidad mínima de arrancar y qué podría instalarse después.

## 4. Cronología reconstruida

### 4.1 Plan inicial y probes antes del montaje

Las primeras llamadas fallidas a `arch-chroot /mnt` aparecen cuando `/mnt` todavía no es un punto de montaje. Son probes de estado realizados durante la planificación, no la causa del aborto. El log continúa inmediatamente con la detección del disco y las operaciones de particionado.

### 4.2 Particionado y cifrado

El log muestra, todos con salida 0:

1. `wipefs --all --force /dev/vda`;
2. `sgdisk --zap-all /dev/vda`;
3. creación de GPT;
4. creación y marcado de la ESP;
5. creación de la partición raíz;
6. `mkfs.fat` sobre `/dev/vda1`;
7. `cryptsetup luksFormat` sobre `/dev/vda2` mediante entrada estándar;
8. apertura como `cryptroot`;
9. `mkfs.btrfs` y creación/montaje de subvolúmenes.

Esto prueba que el disco de la VM fue realmente recreado. También explica por qué no debe darse por supuesto que queda un bootloader anterior reutilizable.

### 4.3 Instalación base y primer initramfs incompleto

Durante pacstrap, el hook interno de mkinitcpio informó:

- ausencia de `/etc/vconsole.conf`;
- ausencia de helpers de `fsck`;
- “the image may not be complete”;
- “command failed to execute correctly”.

El proceso exterior de pacstrap devolvió 0 y `BaseInstallAction` sólo recibió ese éxito exterior. Más tarde otro mkinitcpio terminó correctamente, por lo que este primer fallo no es la causa final; sí es evidencia de que un fallo interno de un hook puede quedar oculto por el código de salida del comando envolvente.

`genfstab` generó entradas Btrfs para la raíz y sus subvolúmenes. No generó swap en disco, coherente con que la tabla sólo contiene ESP y raíz.

### 4.4 Resolución de paquetes

Antes de la instalación oficial, el resolver confirmó que no encontraba fuente para:

- `config-saver`;
- `ttf-atkinson-hyperlegible-next-nerd-git`;
- `ttf-atkinson-hyperlegible-next-nerd-mono-git`.

Al no existir `package_policy` explícita, se aplicó el valor predeterminado `warn-and-skip`. Se instalaron los nombres resolubles y los tres quedaron declarados pero ausentes.

### 4.5 Instalación de repositorios oficiales

La transacción oficial instaló el sistema grande de dependencias y paquetes. Entre otras cosas:

- creó el grupo `libvirt` mediante sysusers;
- instaló `docker-buildx`, `podman`, `podman-compose` y `podman-docker`, pero no `docker`;
- instaló `plasma-login-manager` como dependencia de `plasma-meta`;
- instaló Dracut, mkinitcpio, Snapper y `snap-pac`;
- disparó repetidamente hooks de initramfs y Snapper.

### 4.6 Lote AUR

El helper `yay` recibió en una única invocación todos los AUR pendientes. En el transcurso del lote:

- `sunshine` falló temprano durante la configuración CMake;
- el helper siguió construyendo otros paquetes;
- `rpcs3-git` terminó a las 22:11:38;
- los tests de `lib32-gstreamer` registraron cinco suites fallidas, pero el PKGBUILD continuó empaquetando;
- los dos paquetes Epson fallaron casi inmediatamente con HTTP 403;
- `llama.cpp-cuda` terminó a las 22:23:27;
- la creación del índice de depuración de `btdu` fue terminada con `Killed`, pero el paquete se creó;
- se construyó `systemd-boot-pacman-hook`;
- se abrió una transacción de 39 paquetes/paquetes divididos, 10.155,53 MiB instalados;
- `rpcs3-git` y `llama.cpp-cuda` fueron instalados explícitamente;
- tras la transacción se ejecutó primero un hook de Dracut y después mkinitcpio;
- mkinitcpio escribió `/boot/initramfs-linux.img` sin hook `sd-encrypt`;
- `yay` imprimió el resumen de tres fallos y devolvió 1.

### 4.7 Propagación del error

`AurInstaller._install_with_helper()` ejecuta el helper con `check=True`. `Command.execute()` transforma el código 1 en `CommandExecutionError`. `Reconciler.apply()` itera acciones sin capturar la excepción por acción, así que no sigue con `UsersAction` ni las fases posteriores. La construcción y persistencia del manifiesto están después del bucle; por tanto, no se registra una nueva generación parcial.

## 5. Causa inmediata del aborto

### 5.1 `sunshine`: transición de `pkg_resources`

#### Evidencia local

En `log-llm.log:62704-62724` aparece la secuencia:

1. se selecciona `/usr/bin/python3.14`;
2. la comprobación anuncia que falta “jinja2 or setuptools”;
3. intenta encontrar `pip`;
4. intenta `ensurepip`;
5. CMake termina con error fatal.

El sistema ya había instalado `python-jinja` y `python-setuptools` 83.0.0. Por tanto, el mensaje genérico no significa que ambos paquetes falten.

#### Evidencia upstream y de Arch

El archivo fijado de Sunshine [`cmake/dependencies/glad.cmake`](https://github.com/LizardByte/Sunshine/blob/14ffa6fdaa53f7b51512be2b3d24f3939695403c/cmake/dependencies/glad.cmake#L106-L149) ejecuta realmente `import jinja2; import pkg_resources`. Si ese import falla, trata de instalar dependencias con pip y, si no hay pip, con ensurepip.

[Setuptools 82.0.0](https://setuptools.pypa.io/en/stable/history.html#v82-0-0) retiró `pkg_resources`. Arch publica desde febrero de 2026 [`python-pkg_resources`](https://archlinux.org/packages/extra/any/python-pkg_resources/) como stub temporal independiente. El fallo es por tanto coherente con que `python-setuptools` esté instalado pero ya no proporcione el módulo que Sunshine importa.

#### Diagnóstico

- **Hecho probado:** falta un módulo requerido por el script de build y las dos vías de instalación automática fallan.
- **Inferencia fuerte:** el módulo ausente es `pkg_resources`, no `jinja2`, por la transición documentada y los paquetes presentes.
- **Responsabilidad primaria:** cierre incompleto de dependencias de la receta AUR/upstream para la versión actual de Arch.
- **Responsabilidad de Dasik:** no prevalidar el lote ni aislar un paquete AUR opcional de la ruta crítica de boot.

No debe “arreglarse” instalando pip globalmente sin estudiar la receta. Las alternativas legítimas incluyen declarar `python-pkg_resources`, aplicar la opción upstream que impide el pip interno si corresponde, actualizar a una revisión upstream corregida, o escoger una variante binaria. La decisión requiere verificar el PKGBUILD vigente en el momento de implementar.

### 5.2 `epson-inkjet-printer-escpr`: fuente HTTP 403

El log registra en `log-llm.log:65449-65460` un 403 al descargar:

`epson-inkjet-printer-escpr-1.8.8-1.src.rpm`.

Una sonda de un byte realizada el 19 de julio devolvió 403 tanto con el agente predeterminado como con `Mozilla/5.0`. No se descargó el contenido. La receta AUR puede cambiar; la evidencia sólo permite concluir que la URL exacta usada por este intento no era recuperable por curl en ese momento.

No hay evidencia de un error de DNS, TLS, espacio o permisos locales. Tampoco se puede asegurar si Epson exige cookies, un flujo de licencia, geolocalización o simplemente retiró el artefacto. Cualquiera de esas explicaciones más concretas sería especulación.

### 5.3 `epsonscan2`: segunda fuente HTTP 403

El log registra en `log-llm.log:65461-65472` un 403 separado al descargar:

`epsonscan2-6.7.91.1-1.src.tar.gz`.

La sonda independiente también devolvió 403 con ambos agentes. Son dos URLs y dos paquetes distintos, aunque comparten proveedor. El diagnóstico prudente es “fuente externa no accesible mediante el PKGBUILD actual”, no “fallo de Dasik al ejecutar curl”.

### 5.4 Por qué el error sólo apareció después de una hora

`yay` no se detuvo en el primer PKGBUILD fallido. Continuó construyendo el grafo, reunió paquetes exitosos, ejecutó una transacción y sólo al final devolvió un estado global fallido. Dasik esperó correctamente al proceso en streaming; la espera larga no indica bloqueo ni pérdida del proceso. El usuario recibió la causa agregada cuando finalizó el lote completo.

El problema de producto es que el instalador no ofrece todavía una frontera explícita entre:

- paquetes requeridos para un sistema arrancable;
- paquetes de escritorio requeridos;
- hardware específico;
- aplicaciones AUR grandes u opcionales que podrían instalarse después.

Una única indisponibilidad externa puede impedir que se creen usuarios o el bootloader, aunque esos componentes no dependan de la aplicación fallida.

## 6. Qué se instaló y qué ocurriría al reintentar

### 6.1 Éxitos relevantes

Los siguientes hechos están probados por el log, no inferidos a partir de que el proceso avanzase:

- `rpcs3-git` terminó de construirse en `log-llm.log:64745` y fue instalado en `log-llm.log:70121`.
- `llama.cpp-cuda` terminó de construirse en `log-llm.log:67109` y fue instalado en `log-llm.log:70150`.
- El último lote instaló 39 artefactos, algunos de ellos paquetes `-debug` o productos de un split package, con 10.155,53 MiB de tamaño total (`log-llm.log:70037-70039`).
- `systemd-boot-pacman-hook`, `xpadneo-dkms`, `lib32-gst-plugins-*`, aplicaciones y utilidades AUR también llegaron a la transacción de instalación.
- El helper terminó la transacción antes de devolver el resumen global de fallos.

Por tanto, no debe eliminarse ni reconstruirse automáticamente todo ese trabajo como primera reacción. La presencia de paquetes instalados es estado útil, aunque el sistema global no haya convergido.

### 6.2 Lógica de reintento existente

`PackagesAction.plan()` consulta:

- `pacman -Qq` para todos los paquetes instalados (`packages_action.py:321-330`);
- `pacman -Qqe` para los explícitos (`packages_action.py:310-319`).

Sólo crea cambios `INSTALL` para los nombres deseados ausentes (`packages_action.py:345-351`). Esto implica que un segundo plan debería excluir `rpcs3-git`, `llama.cpp-cuda` y el resto de artefactos cuyo nombre declarado satisfaga el estado de pacman.

La corrección `b9523f3` también permite reutilizar el helper AUR declarado si ya se instaló en un intento parcial. El error anterior `su: invalid option -- 'S'` no es el que aparece aquí: el comando final usa `su ... -- sh yay`, con terminación de opciones correcta.

### 6.3 Qué no se conserva

`AurInstaller.install()` ejecuta su limpieza dentro de `finally` (`aur_installer.py:90-97`):

- elimina el fragmento sudoers temporal;
- elimina el árbol de build AUR;
- elimina el usuario temporal si lo creó esa ejecución.

Esta limpieza es correcta desde el punto de vista de privilegios y residuos, pero descarta el árbol de compilación fallido de Sunshine. No obliga a recompilar paquetes ya instalados; sí elimina una caché que podría haber ayudado a depurar o reanudar un paquete aún ausente.

### 6.4 Resultado esperado de un reintento sin cambios

Con el mismo estado de fuentes y configuración:

1. los paquetes instalados deberían omitirse;
2. `sunshine`, `epson-inkjet-printer-escpr` y `epsonscan2` seguirían ausentes y volverían al lote;
3. los tres nombres confirmados como desconocidos volverían a advertirse y omitirse;
4. si los tres AUR repiten sus fallos, `PackagesAction` volvería a lanzar excepción;
5. `UsersAction` y las fases de boot volverían a quedar sin ejecutar.

Esta predicción depende de que el target parcial sea montado correctamente y pacman conserve su base de datos. No se comprobó montando el destino porque hacerlo quedaba fuera del análisis no destructivo.

## 7. Estado real del destino después del aborto

### 7.1 Orden de acciones

El registro activo fija el siguiente orden (`actions_handler_v2.py:58-171`):

| Fase | Acción | Estado reconstruido en este intento |
| --- | --- | --- |
| 1 | `DiskPartitionAction` | Ejecutada; disco recreado |
| 1 | `BaseInstallAction` | Ejecutada; base y fstab creados |
| 2 | Zona horaria | Ejecutada antes del fallo |
| 2 | Locale | Ejecutada antes del fallo |
| 2 | Red | Ejecutada antes del fallo |
| 2 | Pacman | Ejecutada antes del fallo |
| 3 | `PackagesAction` | Parcialmente mutó y después lanzó excepción |
| 3 | `UsersAction` | No alcanzada |
| 4 | `SystemdAction` | No alcanzada |
| 4 | `FirewallAction` | No alcanzada |
| 4 | `SnapperAction` | No alcanzada; además no está configurada en JSON |
| 4 | `DropFilesAction` | No alcanzada |
| 4 | Microsoft fonts | No alcanzada |
| 4 | ZRAM | No alcanzada |
| 5 | `InitramfsAction` | No alcanzada |
| 5 | `BootloaderAction` | No alcanzada |
| 5 | `KernelCmdlineAction` | No alcanzada |

`Reconciler.apply()` no ofrece reanudación por acción ni persiste un checkpoint después de cada éxito. La excepción en el bucle de `results` evita `_build_new_manifest()`, `GenerationStore.new()` y `StateStore.save()` (`reconciler.py:199-207`). La realidad parcial de pacman queda por delante del manifiesto declarativo.

### 7.2 Arranque

La configuración declara raíz LUKS/Btrfs, Dracut y systemd-boot. El estado observado no satisface ese contrato:

- la ESP fue formateada durante esta ejecución;
- no aparece una llamada final de Dasik a `bootctl install`;
- `BootloaderAction` está después de la acción fallida;
- no aparece la creación final de `loader/entries/arch.conf` por esa acción;
- el backend final de Dracut tampoco se ejecutó;
- un hook de Dracut corrió durante pacman, pero inmediatamente después corrió mkinitcpio;
- mkinitcpio escribió el mismo `/boot/initramfs-linux.img` que la entrada de boot espera;
- sus hooks fueron `base systemd autodetect microcode modconf kms keyboard sd-vconsole block filesystems fsck`, sin `sd-encrypt`.

Para una raíz LUKS que debe abrir `cryptroot`, esa imagen no contiene la ruta declarada de desbloqueo. Aunque no se inspeccionó la ESP desmontada, la combinación de ESP recreada, bootloader final no alcanzado e initramfs final sin cifrado permite una **inferencia fuerte**: el destino no debe considerarse arrancable. La formulación correcta no es “se demostró en una VM que no arranca”, porque no se intentó boot; es “no existe evidencia de una cadena de boot completa y la última imagen registrada carece de un requisito determinista”.

### 7.3 Usuarios, servicios y seguridad

Al no alcanzar `UsersAction`, los dos usuarios declarados no fueron creados por Dasik. Los paquetes pueden haber creado cuentas de sistema mediante sysusers, pero eso no sustituye a los usuarios de configuración.

Tampoco se aplicaron declarativamente las 27 unidades, 13 sockets, la zona de firewalld, ZRAM o los archivos finales. Algunos paquetes activaron hooks o presets durante su propia instalación; esos efectos laterales no equivalen a que las acciones de Dasik verificasen el estado deseado.

### 7.4 Snapshots y capacidad de rollback

El texto `fatal library error, lookup self` apareció en hooks de `snap-pac`, pero las transacciones de pacman que lo rodean terminaron. El script de `snap-pac` consulta el comando padre con `ps -p <PPID> -o args=` antes de enumerar configuraciones. Dentro de este chroot ese `ps` emite el mensaje. No aparece la salida `==> root: <número>` que indicaría una snapshot creada.

Además, la configuración no contiene una sección superior `snapper`, por lo que `SnapperAction` se omite. No debe asumirse que las mutaciones parciales sean reversibles mediante generaciones de Dasik o snapshots de Snapper.

## 8. Inventario priorizado de hallazgos

### 8.1 Escala usada

- **P0:** posible pérdida de datos, sistema no arrancable, bypass de una protección destructiva o exposición de seguridad crítica.
- **P1:** bloqueo determinista de instalación, falsa convergencia, desviación de seguridad o idempotencia rota con impacto alto.
- **P2:** funcionalidad parcial, recuperación/diagnóstico insuficiente o incoherencia relevante que no es por sí sola destructiva.
- **P3:** calidad, rendimiento, mantenibilidad, advertencia o riesgo todavía no demostrado.

La prioridad representa el impacto potencial en Dasik como instalador, no afirma que todos los defectos causaran este aborto concreto.

### 8.2 Tabla maestra

| ID | Prioridad | Hallazgo | Estado de evidencia | Propiedad principal |
| --- | --- | --- | --- | --- |
| F-01 | P0 | El aborto de `PackagesAction` deja un disco ya formateado pero bloquea initramfs y bootloader | Confirmado por log y orden de acciones | Dasik |
| F-02 | P1 | Sunshine falla por la transición `pkg_resources`/setuptools y el fallback pip/ensurepip | Confirmado, con causa externa fuerte | AUR/upstream/Arch |
| F-03 | P1 | Los dos paquetes Epson dependen de fuentes que devuelven HTTP 403 | Confirmado | Epson/AUR |
| F-04 | P1 | El lote AUR no separa paquetes esenciales de opcionales; tres fallos periféricos abortan todo | Confirmado | Dasik/política |
| F-05 | P1 | El usuario exige el grupo `docker`, pero ninguna dependencia instalada lo crea | Inferencia fuerte y reproducible con cierre de paquetes | Configuración/Dasik |
| F-06 | P1 | `SystemdAction` ignora códigos de salida al habilitar/deshabilitar unidades | Confirmado en código | Dasik |
| F-07 | P1 | La configuración usa `sddm.service`, pero Plasma instaló `plasmalogin.service` | Confirmado; impacto aún no ejecutado | Configuración/Arch |
| F-08 | P1 | La conversión del firewall elimina `limit value="2/m"`, ampliando acceso SSH | Confirmado con reproducción pura | Dasik |
| F-09 | P0 | Dracut puede aparecer convergido aunque su imagen no exista o haya fallado | Confirmado en código; escenario no ejecutado | Dasik |
| F-10 | P0 | Los hooks neutralizadores de mkinitcpio llegan después de `Packages`; mkinitcpio pisa a Dracut | Confirmado en código y log | Dasik/orden |
| F-11 | P1 | `sync` detecta mkinitcpio cuando Dracut y mkinitcpio están ambos instalados, aunque ése es el diseño normal | Confirmado, incluso codificado por un test | Dasik |
| F-12 | P1 | `crypttab` declara un cryptswap inexistente y usa `size512` en vez de `size=512` | Confirmado en configuración y esquema de disco | Configuración |
| F-13 | P1 | Snapper no está configurado y se registra después de las transacciones iniciales | Confirmado | Configuración/Dasik |
| F-14 | P2 | `SnapperAction.import_state()` devuelve `{}` y no puede reconstruir su dominio | Confirmado | Dasik |
| F-15 | P0 | `plan` y `apply` no validan con Pydantic antes de alcanzar acciones destructivas | Confirmado en código | Dasik |
| F-16 | P1 | No existe validación semántica cruzada de grupos, proveedores de unidades, discos y archivos | Confirmado por ejemplos aceptados | Dasik/modelado |
| F-17 | P2 | `warn-and-skip` permite éxito sin convergencia y reintento indefinido de nombres desconocidos | Confirmado | Dasik/política |
| F-18 | P1 | `bootctl`, `grub-install`, `grub-mkconfig` y `systemctl` pueden fallar sin excepción | Confirmado en código | Dasik |
| F-19 | P2 | El primer mkinitcpio falló dentro de pacstrap, pero el comando exterior devolvió 0 | Confirmado en log; problema de observabilidad | Herramienta/Dasik |
| F-20 | P3 | `/var/tmp` se crea como 0755 antes de instalar `filesystem`, no 1777 | Confirmado; probablemente reparable por tmpfiles al boot |
| F-21 | P2 | `lib32-gstreamer` se instaló pese a cinco suites fallidas | Confirmado; impacto funcional no probado | AUR/entorno |
| F-22 | P3 | `gdb-add-index` de `btdu` recibió `Killed`; el paquete se instaló sin ese índice | Confirmado; causa no demostrada | Entorno/build |
| F-23 | P3 | `claude-cowork-service` se declara aunque su propio paquete avisa que está obsoleto y se instala el reemplazo | Confirmado | Configuración |
| F-24 | P2 | El borrado incondicional del árbol AUR evita reanudar/inspeccionar builds fallidos costosos | Confirmado; trade-off de seguridad/diagnóstico | Dasik |
| F-25 | P2 | La suite verde prueba lógica simulada, no cierre real de la configuración ni boot | Confirmado por alcance de tests | Calidad |
| F-26 | P2 | El error final etiqueta el wrapper `su` y conserva sólo una cola irrelevante, ocultando los tres fallos reales | Confirmado en log y logger | Dasik/observabilidad |
| F-27 | P2 | La configuración de prueba contiene credenciales persistentes y una passphrase LUKS trivial, aunque está ignorada por Git | Confirmado, valores redactados | Configuración/operación |
| F-28 | P3 | Varias selecciones de proveedores se resolvieron por el valor predeterminado de pacman pese a `--noconfirm` | Confirmado; impacto concreto no probado | Arch/política |
| F-29 | P2 | El logger registra argv completos y el log no está ignorado; puede publicarse accidentalmente con futuros secretos | Confirmado; no se encontró un secreto actual en argv | Dasik/operación |
| F-30 | P3 | JDownloader exige configuración manual posterior, fuera del modelo declarativo | Confirmado por mensaje de instalación | Paquete/configuración |
| F-31 | P3 | La configuración no declara `hostname` ni bloque `network`; la red usada por el live ISO no demuestra la del sistema final | Confirmado; puede ser intención válida | Configuración |

## 9. Análisis detallado por subsistema

### 9.1 Paquetes y AUR

#### F-04: atomicidad equivocada para el objetivo de instalación

La acción es atómica sólo respecto a su excepción, no respecto a sus mutaciones: repo y AUR instalan paquetes antes de que el helper informe de los fallos. Después, el reconciliador no registra el estado. El resultado combina lo peor de dos modelos:

- no hay rollback automático de paquetes;
- no hay checkpoint declarativo del progreso;
- se impide continuar con boot y usuarios;
- el siguiente plan debe redescubrir la realidad mediante pacman.

No basta con “ignorar errores AUR”: eso violaría el contrato declarativo. La solución necesita una semántica explícita para paquetes requeridos/opcionales, una división por fases o un mecanismo de progreso que nunca marque como instalado lo ausente.

#### F-17: nombres desconocidos y no convergencia silenciosa

`PackagesAction` usa por defecto `unknown_policy = "warn-and-skip"` (`packages_action.py:73-87`). Los nombres se excluyen del manifiesto y se reintentan (`packages_action.py:401-408`, `482-496`). Esto es honesto respecto al manifiesto, pero permite que `apply` termine con código 0 si ésos son los únicos problemas, aun cuando la configuración declarada no se ha satisfecho.

La documentación del módulo todavía dice en sus primeras líneas que los desconocidos abortan antes de mutar, mientras la implementación predeterminada ahora los omite. Esa discrepancia de comentario también debe corregirse al diseñar el comportamiento final.

#### F-24: limpieza frente a reanudación

Eliminar sudoers y el usuario temporal es una propiedad de seguridad importante. El árbol de build es otro asunto: su eliminación impide conservar logs/artifacts de un fallo costoso. Cualquier cambio futuro debe evitar dejar un árbol controlado por un usuario privilegiable o reutilizar código AUR sin revalidarlo. Una caché segura tendría que tener propiedad, huella de PKGBUILD/commit y política de invalidación explícitas.

### 9.2 Usuarios y cierre de grupos

La configuración declara para `andres`: `docker`, `libvirt`, `wheel` (`config/test-config.json:22-31`). La transacción creó `libvirt`; `wheel` forma parte del sistema base. No aparece creación de `docker`.

El paquete Arch [`docker`](https://archlinux.org/packages/extra/x86_64/docker/files/) contiene `/usr/lib/sysusers.d/docker.conf`. En cambio, [`podman-docker`](https://archlinux.org/packages/extra/x86_64/podman-docker/files/) proporciona la CLI `/usr/bin/docker` y tmpfiles, no sysusers para un grupo `docker`. `docker-buildx` tampoco cierra esa necesidad.

`UsersAction.apply()` construye `useradd -G docker,libvirt,wheel ...` y usa `check=True` (`users_action.py:217-237`). En una instalación fresca sin ese grupo, `useradd` falla antes de crear al usuario. Este comportamiento estricto de `UsersAction` es correcto; el defecto está en asumir que “Packages antes de Users” garantiza por sí solo la existencia de cualquier grupo declarado.

Se necesita decidir si:

- el usuario realmente quiere Docker Engine y debe declararlo;
- el grupo `docker` es un residuo de una máquina anterior y debe retirarse;
- Dasik debe modelar grupos explícitos;
- o Dasik debe validar antes de mutar que todos los grupos suplementarios estarán presentes tras el cierre de paquetes.

### 9.3 Systemd y transición SDDM → Plasma Login Manager

La configuración habilita `sddm.service` y escribe tres fragmentos bajo `/etc/sddm.conf.d` (`config/test-config.json:353-398`, `525-537`). No declara el paquete `sddm`. El log muestra que `plasma-meta` instaló `plasma-login-manager`.

La ficha oficial de [`plasma-meta`](https://archlinux.org/packages/extra/any/plasma-meta/) incluye `plasma-login-manager` como dependencia. Su [lista de archivos](https://archlinux.org/packages/extra/x86_64/plasma-login-manager/files/) contiene `/usr/lib/systemd/system/plasmalogin.service`, no `sddm.service`.

`SystemdAction.apply()` llama a `Command.execute("systemctl", ["enable", unit], target=target)` sin `check=True` (`systemd_action.py:102-111`). Como `Command.execute()` usa `check=False` por defecto, la unidad ausente sólo produce un `CompletedProcess` fallido que nadie inspecciona. La acción puede terminar, guardar manifiesto y volver a planificar la unidad indefinidamente, mientras el sistema carece de login gráfico configurado.

Hay dos fallos independientes:

1. drift de configuración debido a una transición de Arch;
2. un error de systemd que Dasik silencia.

Corregir únicamente el nombre de servicio ocultaría el defecto genérico de propagación de errores. Corregir únicamente `check=True` haría visible el siguiente bloqueo pero no actualizaría la intención de display manager.

### 9.4 Firewall: pérdida semántica de una regla de seguridad

La configuración elimina `ssh` de los servicios permitidos por defecto y lo reintroduce mediante:

```text
rule service name="ssh" accept limit value="2/m"
```

La función `_rich_rule_to_xml()` reconoce familia, origen, destino, servicio, puerto/protocolo y acción. No analiza `limit`. La reproducción pura produjo:

```xml
<rule><service name="ssh"/><accept/></rule>
```

La [gramática oficial de firewalld](https://firewalld.org/documentation/man-pages/firewalld.richlanguage.html) permite `accept [limit value="rate/duration"]`; en XML el límite forma parte de la acción. Dasik amplía silenciosamente el conjunto de conexiones aceptadas al omitir el límite. Es una regresión de seguridad y de round-trip, no un problema cosmético.

El comportamiento correcto debe elegir entre:

- preservar completamente la regla;
- rechazar como no soportada cualquier cláusula que no pueda preservar;
- o delegar a una representación estructurada que cubra la gramática necesaria.

“Ignorar cláusulas desconocidas” no es seguro para reglas de acceso.

### 9.5 Snapper y `snap-pac`

#### Configuración incompleta

La configuración contiene:

- el paquete `snapper`;
- el paquete `snap-pac`;
- el subvolumen `@snapshots` montado en `/.snapshots`;
- `snapper-boot.timer`, `snapper-cleanup.timer` y `snapper-timeline.timer`.

No contiene la clave superior `snapper`. El registro marca esa acción como opcional con `config_key='snapper'`; por tanto, no se instancia/aplica. Incluso si `PackagesAction` hubiese terminado, los timers podrían quedar habilitados sin `/etc/snapper/configs/root` creado declarativamente.

`SnapperAction.import_state()` devuelve siempre `{}` (`snapper_action.py:139-141`). El verbo `sync` no puede reconstruir la sección a partir de una configuración real existente. Esta pérdida de round-trip contradice el objetivo general de importar la realidad y después volver a converger.

#### Orden incorrecto para la primera instalación

`SnapperAction` está registrado después de `PackagesAction`. Sin embargo, instalar `snap-pac` activa hooks para las propias transacciones de paquetes. En una instalación nueva, la configuración que esos hooks necesitan sólo podría crearse después de que todas las transacciones terminaran. Aunque se añadiese ahora la sección JSON, las primeras mutaciones seguirían sin snapshots salvo que el orden o el bootstrap cambiasen.

#### Interpretación exacta de `fatal library error, lookup self`

El texto aparece muchas veces, pero no es la excepción que aborta Dasik. El script `/usr/share/libalpm/scripts/snap-pac` calcula la descripción de la snapshot ejecutando `ps -p <PPID> -o args=`. En el chroot, `ps` emite ese mensaje. Después, el script lee `SNAPPER_CONFIGS`; al no haber configuración `root`, no aparece `==> root: <número>`.

Conclusión prudente:

- las transacciones de pacman no fallaron por esa frase;
- no hay evidencia de snapshots creadas;
- el ruido debe diagnosticarse, pero no confundirse con los tres códigos de error AUR;
- la ausencia de configuración Snapper es un problema real independiente.

### 9.6 Initramfs: orden, propiedad y falsa convergencia

#### F-10: los neutralizadores llegan demasiado tarde

`expand_initramfs()` deriva dos archivos bajo `/etc/pacman.d/hooks/` cuando se selecciona Dracut (`expand/toggles.py:157-195`). Esos archivos anulan los hooks de mkinitcpio para que Dracut sea el único generador.

Sin embargo, la expansión los añade al dominio `files`, que escribe `DropFilesAction`. El registro ejecuta `DropFilesAction` después de `PackagesAction`. Las transacciones que instalan kernel, systemd, DKMS y paquetes AUR ocurren antes de que los neutralizadores existan. El log demuestra el efecto:

1. `(7/11) Updating initramfs with dracut`;
2. `(8/11) Updating linux initcpios...`;
3. mkinitcpio escribe `/boot/initramfs-linux.img`.

El comentario del propio expansor reconoce que ambos generadores se pisan. La implementación deriva el remedio correcto, pero su fase de aplicación invalida el propósito durante el bootstrap.

#### F-09: Dracut puede converger sin imagen

`DracutBackend.apply()` hace este orden (`initramfs/dracut.py:162-204`):

1. escribe `/etc/dracut.conf.d/dasik.conf`;
2. compone/escribe `/etc/crypttab`;
3. valida que exista fstab y localiza kernels;
4. ejecuta Dracut con `check=True`.

`actual_value()` sólo lee `dasik.conf` y compara `crypttab` (`initramfs/dracut.py:139-160`). No verifica:

- que `/boot/initramfs-<pkgbase>.img` exista;
- que sea posterior a los inputs;
- que corresponda al kernel instalado;
- que contenga los módulos de cifrado/Btrfs requeridos;
- que no haya sido sobrescrita después por otro generador.

Si Dracut falla tras escribir los archivos, el siguiente `plan` puede considerar el backend satisfecho. Es falsa convergencia sobre un artefacto necesario para arrancar. El criterio de estado debe incluir la imagen o un marcador escrito únicamente después de una generación verificada; comprobar sólo intención no prueba el producto derivado.

#### F-11: detección de generador contradictoria

`InitramfsAction._detect_generator()` devuelve `dracut` únicamente si Dracut está instalado **y mkinitcpio no lo está** (`initramfs_action.py:52-63`). Pero `expand_initramfs()` dice expresamente que mkinitcpio se mantiene instalado y se neutraliza por seguridad/reversibilidad. Por tanto, el estado normal diseñado —ambos paquetes instalados, hooks de mkinitcpio anulados— se importa como `"initramfs": "mkinitcpio"`.

El test `test_import_state_detects_mkinitcpio_when_present` codifica esa misma suposición: con ambos paquetes instalados espera mkinitcpio. Es un ejemplo exacto del riesgo descrito en `AGENTS.md`: test e implementación pueden compartir la misma interpretación equivocada y pasar juntos. La detección debe basarse en propiedad/configuración efectiva, no sólo en coexistencia de paquetes.

### 9.7 `crypttab`, swap y ZRAM

La única partición de datos declarada es la raíz LUKS/Btrfs. No hay partición con etiqueta `cryptswap` y `genfstab` no registra swap en disco. Aun así, el archivo libre `/etc/crypttab` incluye una línea para `LABEL=cryptswap`.

Además, la opción es `size512`. [`crypttab(5)`](https://man.archlinux.org/man/crypttab.5) define `size=`. El token sin signo igual no expresa el tamaño de clave deseado.

Con Dracut y cifrado de raíz, `DropFilesAction` cede la propiedad de `crypttab` al backend. `DracutBackend` conserva líneas extra no correspondientes a sus mappers derivados, por lo que la línea defectuosa se incorporaría al archivo compuesto junto a `cryptroot`.

Posibles consecuencias, todavía no ejecutadas:

- error o espera durante boot buscando una etiqueta inexistente;
- opción desconocida ignorada o rechazada;
- creación destructiva de swap sobre un dispositivo equivocado si la etiqueta apareciese accidentalmente en otro disco;
- contradicción con la ZRAM ya declarada.

La advertencia de `crypttab(5)` es especialmente relevante: la opción `swap` reformatea el dispositivo indicado en cada arranque. Dasik debe validar que una entrada destructiva de este tipo referencia un dispositivo declarado de forma inequívoca o, como mínimo, no importar sin aviso una línea heredada rota.

### 9.8 Bootloader y parámetros del kernel

`BootloaderAction._install()` llama a `bootctl install` sin `check=True` y después escribe `loader.conf` y `arch.conf` aunque el comando haya fallado (`bootloader_action.py:138-152`). En la rama GRUB, la instalación del paquete, `grub-install` y `grub-mkconfig` tampoco comprueban el código de salida (`bootloader_action.py:153-160`).

Como `Command.execute()` usa `check=False` por defecto, un binario que devuelve error puede quedar seguido de archivos que hacen parecer la acción aplicada. Según la lógica concreta de `plan`, esto puede convertirse en falso éxito, bucle de reintento o configuración incoherente.

En este intento la acción ni siquiera se alcanzó. El problema es latente, pero en un instalador de discos un bootloader fallido no puede ser best-effort. Todos los comandos mutantes de boot deben abortar con contexto y la verificación debe comprobar los artefactos que el firmware/bootloader realmente consumirá.

### 9.9 Frontera de validación

`_cmd_plan()` y `_cmd_apply()` hacen `json.loads()` y `expand_config()` directamente (`__main__.py:229-283`). `JsonModel.model_validate()` sólo aparece en `_cmd_check()` (`__main__.py:428-449`). Esto tiene dos capas de problema:

1. un usuario puede omitir `dasik check` y llevar JSON sintácticamente válido a acciones destructivas;
2. el propio modelo actual no expresa todas las invariantes necesarias.

Ejemplos de invariantes no protegidas:

- cada grupo suplementario debe existir o estar explícitamente creado;
- cada unidad systemd habilitada debe tener un proveedor previsto;
- una configuración SDDM requiere SDDM, no otro display manager;
- cada entrada crypttab destructiva debe referenciar un dispositivo válido;
- las reglas de firewall deben poder representarse sin pérdida;
- Dracut, bootloader y cifrado deben formar una cadena compatible;
- `initramfs` y `bootloader` son strings con defaults, sin una restricción fuerte visible en `JsonModel` a los valores soportados;
- Snapper/timers/subvolumen deberían formar un conjunto coherente.

La validación de esquema debe ser obligatoria dentro de cada verbo consumidor, no depender de que el usuario haya ejecutado previamente otro comando. La validación semántica cruzada debe ocurrir antes de la primera mutación destructiva.

### 9.10 Permisos de `/var/tmp`

`DiskPartitionAction` crea cada punto de montaje con `Path(...).mkdir(parents=True, exist_ok=True)` y no asigna modo (`disk_partition_action.py:1195-1205`). Para `@var_tmp`, eso produjo 0755. Al instalar `filesystem`, pacman avisó:

```text
warning: directory permissions differ on /mnt/var/tmp/
filesystem: 755  package: 1777
```

Pacman preservó el directorio existente. Es probable que `systemd-tmpfiles` aplique 1777 durante el arranque, pero ese arranque no ocurrió y no debe usarse como prueba de convergencia. El mountpoint debe tener el modo correcto desde que se crea, especialmente porque `/var/tmp` necesita escritura global y sticky bit.

### 9.11 Observabilidad del error final

Para comandos en streaming, `Command.execute()` acumula toda la salida, pero al lanzar excepción toma sólo los últimos 2.000 caracteres y usa el nombre del wrapper `cmd` (`command_worker.py:133-139`). En este caso, después de los verdaderos fallos hubo salida de dependencias opcionales y hooks. La excepción visible terminó como:

```text
su failed (exit 1): — file dialogs, screen sharing [installed]
```

Eso oculta el resumen `sunshine/epson/epsonscan2` que sí existe antes en el log. Los bloques repetidos posteriores son la cola reimpresa y el traceback, no nuevas compilaciones.

La corrección debe conservar simultáneamente:

- comando lógico (`yay`, no sólo `su`);
- código de salida;
- primera/última causa relevante o un extracto inteligentemente delimitado;
- ruta al log completo;
- lista agregada de paquetes fallidos cuando el helper la proporcione;
- saneamiento de cualquier secreto.

### 9.12 Credenciales de la configuración de prueba

`config/test-config.json` está ignorado por `.gitignore`, por lo que no forma parte de `git status` normal ni del historial. Contiene, no obstante:

- dos hashes de contraseña reutilizables;
- una passphrase LUKS en texto claro y de entropía trivial.

Los valores no se reproducen aquí. Si la configuración sólo pertenece a una VM descartable, el impacto es limitado; si se reutiliza en hardware o se comparte con otro LLM/servicio, debe sanearse y rotarse. El prompt de traspaso exige explícitamente redacción de secretos.

### 9.13 Registro de comandos y riesgo de publicación

`RunLogger` registra los argumentos completos de cada comando. En esta ejecución, la passphrase LUKS se entregó por stdin mediante `--key-file -`, por lo que no aparece como argumento. Eso es una propiedad positiva de ese call site, no una garantía general: cualquier token futuro interpolado en argv quedaría en el log.

`log-llm.log` está sin seguimiento y **no** está cubierto por `.gitignore`. Un `git add .` podría incorporarlo accidentalmente. Antes de adjuntarlo a una incidencia o PR debe sanearse; una búsqueda selectiva no basta para certificar que un log de 6,7 MiB está libre de secretos.

### 9.14 Límites de la declaratividad de aplicaciones

Durante la instalación de JDownloader aparece una instrucción para ejecutar varias veces `JDownloaderHeadless` y configurarlo manualmente. Dasik puede declarar la presencia del paquete, pero no el estado interno de esa aplicación. De modo parecido, instalar `claude-cowork-service` y `claude-desktop-bin` simultáneamente conserva un componente cuyo propio post-install lo declara obsoleto.

Estos casos no bloquean boot, pero deben diferenciarse en la promesa del producto:

- **paquete presente** no implica **aplicación configurada**;
- una opción que requiera onboarding manual debe documentarse como tal o adquirir una acción declarativa específica;
- paquetes obsoletos no deberían permanecer en la configuración por inercia de `sync` sin una advertencia accionable.

### 9.15 Red y hostname no declarados

La configuración examinada no contiene un bloque superior `network` ni `hostname`. `NetworkAction` requiere ambos, por lo que se omite. NetworkManager está instalado y su servicio está en la lista deseada, pero esa acción de systemd tampoco llegó a ejecutarse.

El hecho de que pacman/AUR tuviesen red demuestra conectividad del live ISO y del chroot durante la instalación, no que el sistema instalado vaya a tener hostname, perfil de red o servicio habilitado tras boot. Puede ser una omisión intencional si otro mecanismo lo gestiona; debe decidirse antes de usar esta configuración como prueba de instalación completa.

## 10. Anomalías secundarias y causas descartadas

### 10.1 Contabilidad correcta de errores

El log contiene 62 bloques de comando, con 56 marcados `exit 0` y 6 marcados `exit 1`. Eso **no** representa seis incidentes de instalación:

- cuatro códigos 1 son probes iniciales contra `/mnt` antes de montarlo;
- otro negativo corresponde a comprobar que el usuario temporal AUR aún no existía;
- el último código 1 es el lote AUR terminal.

El tail del último fallo se imprime varias veces por el logger y el traceback. La invocación grande `yay -S` ocurrió una sola vez. Las repeticiones textuales no deben contarse como reintentos.

### 10.2 `lib32-gstreamer`

El build ejecutó 371 suites:

- 329 correctas;
- 5 fallidas;
- 37 omitidas.

Las suites fallidas fueron `libs_dsd`, `libs_libsabi`, `elements_volume`, `elements_matroskademux` y `elements_inputselector`; dentro de algunas hubo varios asserts fallidos. A pesar del resumen, el PKGBUILD entró en fakeroot, empaquetó e instaló los plugins resultantes.

Esto no demuestra que el runtime esté roto, pero tampoco permite considerar el paquete plenamente validado. Es responsabilidad primaria de la receta AUR decidir ignorar esos tests; Dasik actualmente sólo observa que makepkg/yay devolvió éxito para el artefacto.

### 10.3 `btdu` y la hipótesis de OOM

`gdb-add-index` recibió `Killed` al generar un índice para `btdu`. El empaquetado continuó y `btdu`/`btdu-debug` se instalaron. No hay en el artefacto disponible:

- `dmesg`;
- journal del host;
- eventos de cgroup;
- RAM asignada a la VM;
- código de salida de un OOM killer;
- mensaje “Out of memory”.

Por tanto, presión de memoria u OOM son hipótesis posibles, no causas probadas. El impacto observado se limita al índice de depuración fallido; no explica el código final de `yay`.

### 10.4 Advertencias no terminales

- AutoFirma emitió avisos Maven de modelo/solapamiento, pero su reactor terminó y el paquete se instaló.
- El hook de Context/TeX emitió advertencias dentro de una transacción oficial que devolvió 0.
- La ESP recibió una etiqueta FAT en minúsculas y `mkfs.fat` avisó de compatibilidad; el comando devolvió 0.
- La dependencia opcional NCCL ausente sólo afecta paralelismo multi-GPU de llama.cpp; no la instalación base.
- Algunas selecciones de proveedores de pacman aceptaron el valor predeterminado bajo `--noconfirm`, lo que hace el resultado sensible al orden actual de repositorios, pero no causó este aborto.
- Un nombre de dependencia `qt5-singlecoreapplication` terminó resolviéndose/construyéndose como `qt5-singleapplication`; puede ser normalización por `provides`, no se clasificó como defecto.

### 10.5 Causas descartadas para el aborto final

| Sospecha | Veredicto | Evidencia |
| --- | --- | --- |
| `llama.cpp-cuda` falló | Descartada | Terminó y se instaló |
| `rpcs3-git` falló | Descartada | Terminó y se instaló |
| Reaparición de `su -S` | Descartada | `yay` se ejecutó; el `--` correcto está presente |
| Disco lleno | No respaldada | Pacman comprobó espacio y no aparece `No space left on device` |
| Fallo general de red | Descartada como explicación única | Miles de descargas funcionaron; dos endpoints Epson devolvieron 403 específicos |
| Snapper abortó pacman | Descartada | Las transacciones circundantes devolvieron 0; el lote falla por tres AUR listados |
| `btdu` causó el exit 1 | Descartada | El paquete terminó e instaló pese al índice fallido |
| OOM de la VM | No demostrada | Sólo existe un proceso `Killed`, sin telemetría de memoria |
| Error inicial de `/mnt` | Descartada | Son probes anteriores al particionado; la instalación continuó |

### 10.6 Lo que sigue sin evaluarse

- boot real del guest;
- contenido final de la ESP y de las imágenes con `lsinitrd`;
- inventario `pacman -Q` posterior al fallo;
- usuarios/grupos del target desmontado;
- unidades realmente habilitadas;
- existencia real de snapshots;
- un segundo `plan` o `apply` sobre el estado parcial;
- idempotencia completa;
- rendimiento con una cantidad conocida de CPU/RAM;
- compatibilidad real del hardware del usuario;
- estado de los tres PKGBUILD en una fecha posterior al informe.

## 11. Por qué los tests no detectaron estos problemas

### 11.1 Resultado fresco

Se ejecutó de forma no destructiva:

```text
env PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider
```

Resultado:

```text
1098 passed, 17 warnings in 6.83s
```

Las 17 advertencias proceden de tests que aún ejercitan el prefijo AUR heredado `aur-`, marcado como obsoleto. No hubo fallos de pytest.

### 11.2 Por qué verde no significa integración correcta

La suite está diseñada correctamente para no tocar discos reales. Sustituye `Command.execute`, filesystem, subprocess y targets en gran parte de las acciones. Eso prueba decisiones y argumentos; no reproduce:

- la versión móvil de un PKGBUILD;
- un CDN que devuelve 403;
- 311 nombres resueltos contra repositorios de una fecha concreta;
- creación real de grupos mediante sysusers;
- disponibilidad de unidades systemd aportadas por dependencias;
- interacción temporal entre hooks de pacman;
- una imagen initramfs inspeccionable;
- firmware/bootloader;
- un boot LUKS/Btrfs;
- el segundo ciclo completo.

### 11.3 Huecos de pruebas concretos

1. **Entrada de CLI:** demostrar que `plan` y `apply` llaman a Pydantic y que una configuración inválida aborta antes de crear cualquier acción mutante.
2. **Validación semántica:** configuración real reducida donde un usuario exige un grupo sin proveedor; debe fallar en check/plan, no en `useradd`.
3. **Systemd:** `systemctl enable/disable` no cero debe propagarse y no marcar estado gestionado.
4. **Display manager:** detectar unidad declarada sin paquete/proveedor o probar el sample actual contra la transición Plasma.
5. **Firewall:** regla `accept limit value="2/m"` debe conservar el límite exactamente; cláusula no soportada debe fallar cerrada.
6. **Dracut:** simular fallo después de escribir configuración; el siguiente plan debe seguir necesitando regeneración.
7. **Artefacto initramfs:** ausencia, kernel distinto o imagen sobrescrita deben producir cambio.
8. **Orden:** los neutralizadores deben existir antes de cualquier transacción que pueda disparar mkinitcpio.
9. **Import de generador:** Dracut y mkinitcpio instalados con hooks neutralizados debe importar `dracut`; el test actual espera lo contrario.
10. **Snapper bootstrap:** configuración disponible antes del primer hook o política explícita de omisión durante bootstrap.
11. **Crypttab:** rechazar opciones mal formadas y dispositivos destructivos no declarados.
12. **Bootloader:** cada comando mutante no cero debe abortar; no se escriben markers/config posteriores.
13. **AUR parcial:** helper instala éxitos y devuelve lista de fallos; el manifiesto y el reintento deben representar la realidad sin reconstruir lo instalado.
14. **Paquetes opcionales:** demostrar que la política elegida no declara convergencia falsa.
15. **Logger:** un fallo temprano seguido de salida larga debe conservar el resumen causal, no una cola arbitraria.
16. **Secreto:** argumentos y detalles de error deben sanear valores sensibles.
17. **Propiedades:** `reconcile(current, current)` siempre vacío; una segunda aplicación simulada no repite mutaciones.
18. **Mutación:** invertir condiciones críticas de `actual_value()`, grupos, firewall y persistencia debe matar el mutant.

### 11.4 El caso ejemplar de un test incorrectamente alineado

`test_import_state_detects_mkinitcpio_when_present` espera mkinitcpio cuando ambos generadores están instalados. El expansor, sin embargo, mantiene ambos instalados cuando Dracut es activo. Test y código son verdes porque comparten el supuesto incorrecto. Los criterios de aceptación deben derivarse de la arquitectura deseada y del comportamiento de Arch, no de la implementación actual.

## 12. Orden recomendado de corrección

Este orden reduce el riesgo de volver a gastar una hora antes de descubrir el siguiente fallo. No es una autorización para ejecutar una instalación real.

### Fase 0 — preservar evidencia y fijar criterios

1. Conservar el log original fuera de commits públicos.
2. Crear una configuración saneada reproducible sin secretos.
3. Registrar SHA, fecha, versiones y recursos de la VM en cada ejecución.
4. Decidir las preguntas funcionales de la sección 15.
5. Convertir cada P0/P1 en un test de aceptación revisado por una persona.

### Fase 1 — cerrar la frontera antes de mutar

1. Hacer obligatoria la validación Pydantic en `plan`, `apply` y `sync` donde corresponda.
2. Restringir enums/valores de initramfs y bootloader.
3. Añadir validaciones cruzadas de grupos, unidades, crypttab, firewall y cadena de boot.
4. Asegurar que un error se produce antes de `wipefs`/particionado cuando puede conocerse desde la configuración y metadatos de paquetes.

### Fase 2 — hacer imposible el falso éxito crítico

1. Usar comprobación estricta para `systemctl`, `bootctl`, GRUB y cualquier mutación.
2. No escribir archivos/markers posteriores si el comando productor falla.
3. Mejorar el error de comandos en streaming para conservar la causa real.
4. Diseñar persistencia explícita de progreso parcial sin confundirla con una generación convergida.

### Fase 3 — reparar la cadena de boot

1. Instalar/escribir neutralizadores de mkinitcpio antes del primer pacman que pueda dispararlos.
2. Verificar imágenes de Dracut como parte de `actual_value()`/estado.
3. Corregir la detección de generador efectivo usada por `sync`.
4. Validar `crypttab` y retirar/representar correctamente cryptswap.
5. Probar que el bootloader consume la imagen y cmdline correctas.
6. Mantener todo test de comandos destructivos bajo mocks.

### Fase 4 — cerrar configuración de usuarios y escritorio

1. Decidir Docker Engine frente a Podman y corregir grupo/paquetes.
2. Decidir SDDM frente a Plasma Login Manager y migrar servicio/configuración de forma coherente.
3. Validar que cada unidad y socket tiene un proveedor.
4. Decidir hostname/network y autologin.

### Fase 5 — definir política AUR y paquetes no esenciales

1. Clasificar paquetes por criticidad o por etapas instalables.
2. Verificar de nuevo Sunshine/AUR; aplicar la solución en su propietario correcto.
3. Decidir si los dos drivers Epson son necesarios y cómo obtener fuentes legal/reproduciblemente.
4. Reemplazar, fijar por fuente o eliminar los tres nombres desconocidos.
5. Diseñar reintento acotado y manifiesto de parciales sin “éxito” falso.
6. Considerar caché AUR saneada sólo con huella y propiedad seguras.

### Fase 6 — Snapper, permisos y aplicaciones

1. Modelar Snapper completo o retirar paquetes/timers/subvolumen si no se desea.
2. Asegurar la configuración antes de hooks y hacer `import_state()` real.
3. Crear `/var/tmp` con 1777.
4. Retirar paquetes obsoletos y documentar aplicaciones con onboarding manual.

### Fase 7 — verificación escalonada

1. Tests unitarios rojos/verdes por hallazgo.
2. Suite completa y cobertura ≥80 %.
3. mypy y análisis estático disponibles.
4. Tests de propiedades/mutación de idempotencia.
5. Smokes CLI no destructivos.
6. Sólo con autorización separada: guest QEMU completamente descartable, disco sin relación con el host y plan manual revisado.
7. Boot, comprobación de LUKS/Btrfs, usuarios, servicios, firewall, Snapper e initramfs.
8. Segundo `plan` y segundo ciclo, que deben ser no-op.

## 13. Criterios de aceptación

### 13.1 Criterios globales

- Una configuración inválida o incoherente no puede alcanzar la primera mutación.
- Un comando mutante no cero nunca puede producir manifest/generación de éxito.
- La misma configuración sobre el estado ya convergido produce plan vacío.
- Un fallo parcial no repite formateo ni recompila paquetes ya instalados.
- El estado de paquetes ausentes se conserva como divergencia visible.
- La cadena `LUKS → Dracut → imagen → bootloader entry → cmdline` se verifica como una unidad.
- Ningún secreto aparece en salida, logs, tests, commits o PR.
- Tests simulados y prueba de boot se reportan como capas distintas.

### 13.2 Criterios por área

| Área | Criterio observable mínimo |
| --- | --- |
| CLI/schema | `apply` con JSON inválido devuelve error antes de `setup_actions()`/mutaciones |
| Grupos | Grupo ausente produce error de preflight accionable; `useradd` no se invoca |
| Systemd | Unidad inexistente propaga error; no se guarda como gestionada |
| Firewall | La regla SSH conserva `limit=2/m` en XML y tras import/export |
| AUR | Éxitos parciales no se reconstruyen; fallos quedan identificados por paquete/fase |
| Unknowns | La política elegida nunca comunica convergencia si falta un requerido |
| Snapper | Config root existe antes del primer hook o los hooks se desactivan explícitamente durante bootstrap |
| Dracut | Fallo de generación deja el siguiente plan pendiente; imagen ausente nunca converge |
| Generador | Ambos paquetes + neutralizadores activos importan `dracut` |
| Crypttab | `size512` se rechaza; cryptswap sin dispositivo declarado se rechaza o exige opt-in explícito |
| Bootloader | Fallo de `bootctl`/GRUB aborta antes de escribir estado posterior |
| `/var/tmp` | El mountpoint queda `01777` inmediatamente después de crearlo |
| Logger | La excepción menciona los tres paquetes fallidos y enlaza al log completo |
| Idempotencia | Segundo ciclo simulado y, después, segundo ciclo en guest descartable son no-op |

### 13.3 Evidencia exigida antes de afirmar “instalación conseguida”

1. `apply` completo con código 0 en un guest descartable.
2. Manifiesto y generación guardados después de la última acción.
3. ESP con bootloader/entry esperados.
4. Imagen Dracut correspondiente al kernel e inspección de módulos/cmdline necesarios.
5. Reinicio real de la VM y desbloqueo LUKS.
6. Raíz Btrfs y subvolúmenes correctos.
7. Usuarios y login gráfico funcionales.
8. Servicios y firewall verificados.
9. Snapper verificable si se declara.
10. Segundo `plan` vacío y segundo `apply` sin mutaciones.

Sin esos puntos sólo puede afirmarse “lógica verificada con mocks” o “instalación parcial”, según corresponda.

## 14. Matriz de evidencias

### 14.1 Evidencias locales principales

| Tema | Ubicación | Qué demuestra |
| --- | --- | --- |
| Disco y formato | `log-llm.log:37-201` | `/dev/vda` fue borrado, particionado, cifrado y formateado |
| Primer mkinitcpio incompleto | `log-llm.log:818-841` | Hook interno falla, pacstrap exterior devuelve 0 |
| Fstab sin swap | `log-llm.log:843-874` | Sólo raíz/subvolúmenes/ESP declarados |
| Desconocidos | `log-llm.log:16432-16433` | Tres nombres omitidos por política |
| Repo transaction | `log-llm.log:16434-22181` | Paquetes oficiales instalados; 1.718 incluyendo dependencias |
| Sunshine | `log-llm.log:62704-62724` | Import/fallback pip/ensurepip y CMake fatal |
| RPCS3 | `log-llm.log:64745`, `70121` | Build e instalación exitosos |
| Epson | `log-llm.log:65449-65472` | Dos URLs distintas devuelven 403 |
| llama.cpp | `log-llm.log:67109`, `70150` | Build e instalación exitosos |
| GStreamer | `log-llm.log:27000-27015` | 329 OK, 5 fail, 37 skip; empaquetado continúa |
| btdu | `log-llm.log:69951-69966` | `gdb-add-index` terminado; paquete se crea |
| Lote de 39 | `log-llm.log:70037-70199` | Artefactos que entran en transacción |
| Dracut pisado | `log-llm.log:70207-70232` | Dracut hook seguido de mkinitcpio sobre imagen final |
| Tres fallos finales | `log-llm.log:70237-70241` | Causa agregada exacta del exit 1 |
| Error degradado | `log-llm.log:70296`, `70382` | `su failed` muestra cola irrelevante |
| Orden de acciones | `dasik/lib/actions/actions_handler_v2.py:58-171` | Packages precede Users y todo boot |
| Persistencia | `dasik/lib/reconciler/reconciler.py:199-207` | Generación se guarda sólo al final |
| Reintento | `dasik/lib/actions/packages_action.py:321-351` | Sólo se instalan nombres ausentes |
| Limpieza AUR | `dasik/lib/actions/aur_installer.py:74-124` | finally elimina sudoers/build user/root |
| Usuario/grupos | `config/test-config.json:22-31`; `users_action.py:217-237` | `docker` requerido y `useradd` estricto |
| Systemd silencioso | `systemd_action.py:102-111`; `command_worker.py:21-40` | `check=False` implícito |
| Firewall | `firewall_action.py:28-60`; config `:551-565` | Límite no parseado |
| Dracut convergence | `initramfs/dracut.py:139-204` | Estado no incluye imagen |
| Generador importado | `initramfs_action.py:52-63`; `expand/toggles.py:157-195` | Supuestos contradictorios sobre coexistencia |
| Bootloader | `bootloader_action.py:138-160` | Comandos sin comprobación estricta |
| Validación CLI | `dasik/__main__.py:229-283`, `428-449` | `check` valida; `plan/apply` no |
| Cryptswap | `config/test-config.json:405-502`, `539-544` | No existe partición que corresponda a la línea |
| `/var/tmp` | `log-llm.log:450-451`; disk action `:1195-1205` | 0755 frente a 1777 |

### 14.2 Fuentes externas primarias

- Sunshine, lógica exacta de glad: [`glad.cmake` fijado](https://github.com/LizardByte/Sunshine/blob/14ffa6fdaa53f7b51512be2b3d24f3939695403c/cmake/dependencies/glad.cmake#L106-L149).
- Setuptools: [historial de 82.0.0](https://setuptools.pypa.io/en/stable/history.html#v82-0-0).
- Arch: [`python-pkg_resources`](https://archlinux.org/packages/extra/any/python-pkg_resources/).
- Arch: [archivos de Docker](https://archlinux.org/packages/extra/x86_64/docker/files/) y [archivos de `podman-docker`](https://archlinux.org/packages/extra/x86_64/podman-docker/files/).
- Arch: [`plasma-meta`](https://archlinux.org/packages/extra/any/plasma-meta/) y [archivos de Plasma Login Manager](https://archlinux.org/packages/extra/x86_64/plasma-login-manager/files/).
- Firewalld: [lenguaje de reglas ricas](https://firewalld.org/documentation/man-pages/firewalld.richlanguage.html).
- systemd/Arch: [`crypttab(5)`](https://man.archlinux.org/man/crypttab.5).
- AUR: [Sunshine](https://aur.archlinux.org/packages/sunshine), [driver Epson](https://aur.archlinux.org/packages/epson-inkjet-printer-escpr) y [Epson Scan 2](https://aur.archlinux.org/packages/epsonscan2). AUR estaba protegido por Anubis durante parte de la revisión; antes de arreglar debe consultarse de nuevo la revisión concreta de PKGBUILD/.SRCINFO.

### 14.3 Integridad y reproducibilidad

- El hash del log identifica el artefacto analizado.
- La configuración original no debe adjuntarse sin saneado.
- Los rangos de log son evidencia local no versionada; un PR necesitaría extractos redactados, no el archivo bruto.
- Los enlaces externos son mutables salvo el commit fijado de Sunshine.
- Una futura ejecución debe registrar SHA de Dasik, SHA/config saneada, ISO, mirror state, CPU/RAM de VM y versiones AUR.

## 15. Decisiones funcionales todavía necesarias

Estas decisiones cambian materialmente la solución. El prompt maestro obliga al futuro LLM a verificarlas y, si no puede inferirlas sin riesgo, pedir una decisión concreta.

1. **Criticidad de paquetes:** ¿todo nombre declarado es obligatorio o puede haber perfiles `essential`, `desktop` y `optional/post-boot`?
2. **Sunshine:** ¿se prefiere compilar, usar variante binaria, fijar una revisión o corregir la dependencia AUR?
3. **Epson:** ¿ese hardware debe funcionar desde la primera instalación? ¿Se permite una fuente alternativa verificable o deben quedar como bloqueo externo?
4. **Contenedores:** ¿el objetivo es Docker Engine con grupo `docker` o Podman rootless sin dicho grupo?
5. **Display manager:** ¿se quiere el nuevo Plasma Login Manager o SDDM explícito con sus configs/autologin?
6. **Snapper:** ¿se desean snapshots reales de root y hooks pacman, o sólo se heredaron paquetes/timers?
7. **Swap:** ¿ZRAM sustituye completamente a cryptswap? Si no, ¿qué dispositivo declarado debe respaldarlo?
8. **Paquetes desconocidos:** ¿nombres renombrados, recetas Git privadas/locales o entradas obsoletas?
9. **Red/hostname:** ¿deben gestionarse por Dasik o se dejan deliberadamente a DHCP/defaults/otra herramienta?
10. **AUR parcial:** ¿un paquete opcional ausente debe permitir terminar un perfil mínimo sin afirmar convergencia total?
11. **Caché AUR:** ¿prima reproducibilidad/limpieza o capacidad de reanudar builds grandes, y cómo se confía en la caché?
12. **Aplicaciones manuales:** ¿la promesa declarativa cubre sólo instalación de JDownloader y similares o también onboarding?
13. **Artefactos públicos:** ¿se publicará el informe/PR? Si sí, hay que crear config y extractos de log saneados.

Recomendación inicial, no aplicada: asegurar primero un sistema mínimo arrancable y convergente; mover hardware/applications AUR no esenciales a una fase posterior observable; mantener como error cualquier ausencia declarada, aunque un perfil parcial pueda completar su propia fase.

## 16. Prompt maestro para otro LLM

### 16.1 Cómo utilizarlo

1. Entregar al LLM acceso al repositorio y a este informe.
2. No adjuntar `config/test-config.json` sin sanear. Si el LLM no tiene acceso local, proporcionar una copia con hashes y credenciales sustituidos por marcadores.
3. Proporcionar `log-llm.log` sólo en un entorno privado; para servicios externos, usar extractos saneados o una copia redactada.
4. Pegar el bloque completo siguiente. Es neutral respecto al proveedor/modelo y concede autoridad para modificar código/tests/docs locales, no para ejecutar una instalación destructiva ni publicar/mergear sin permiso.
5. Si el futuro LLM encuentra instrucciones más recientes en el repositorio, debe obedecer su jerarquía y registrar cualquier desviación de este prompt.

### 16.2 Prompt copiable

~~~text
Actúa como responsable técnico principal de la investigación y reparación integral de Dasik en el repositorio que tienes abierto.

Tu documento de entrada obligatorio es:

docs/2026-07-19-install-failure-forensic-report.md

Los artefactos locales relacionados son:

- log-llm.log
- config/test-config.json
- AGENTS.md

Tu misión no termina en resumir o comentar el informe. Debes encargarte de absolutamente todo el trabajo seguro que permita el repositorio: validar cada hallazgo de forma independiente, corregir los errores del informe si existen, descubrir fallos relacionados omitidos, priorizar, diseñar las correcciones, implementarlas con TDD cuando corresponda, verificar idempotencia y seguridad, revisar tu propio trabajo y dejar una entrega utilizable. No te detengas después de la auditoría o del plan si todavía queda implementación local segura y autorizada.

Trata el informe como un conjunto de afirmaciones que deben probarse, no como verdad absoluta. Distingue siempre entre:

1. lo que ocurrió en la ejecución fallida del 19 de julio de 2026;
2. la causa en la revisión que realmente produjo el log, si puede identificarse;
3. el estado actual de HEAD, que podría contener arreglos posteriores;
4. el comportamiento esperado según la arquitectura declarativa e idempotente de Dasik;
5. hechos externos móviles de Arch, AUR o upstream.

Continúa hasta completar todo el trabajo seguro y autorizado. Sólo solicita intervención humana ante un bloqueo real que requiera una decisión funcional material, un secreto, instalación de software, una prueba destructiva/en vivo, publicación externa o permisos no concedidos. Antes de detenerte, termina todo lo independiente del bloqueo.

## Autoridad e instrucciones iniciales

Antes de cualquier otra acción:

1. Lee íntegramente el AGENTS.md de la raíz.
2. Localiza de forma acotada otros AGENTS.md que sean aplicables, excluyendo resources/, y respeta su ámbito.
3. Identifica las instrucciones del sistema, usuario y repositorio. Tienen prioridad sobre este prompt y sobre el informe.
4. Si existe una contradicción, aplica la instrucción de mayor autoridad y regístrala.
5. Detecta el modo activo. Si el usuario no ha activado expresamente “modo desatendido”, aplica modo normal. Respeta “lite mode” si estuviera activo.
6. Registra sin modificar nada: raíz, rama, SHA de HEAD, remotos/base relevantes y `git status --short --branch`.
7. Preserva todos los cambios existentes. Son del usuario salvo prueba expresa en contrario.
8. No des por vigente una descripción arquitectónica, comando o resultado del informe sin contrastarlo con código, tests, ayuda CLI actual y AGENTS.md.
9. Si usas skills/procesos, aplica primero depuración sistemática, después planificación/TDD y finalmente verificación/revisión, según las reglas del repositorio.

Estás autorizado a editar código, tests y documentación local dentro del alcance de estos hallazgos, a crear una rama de feature segura y a realizar commits coherentes. No estás autorizado por este prompt a ejecutar una instalación real, usar hardware/discos reales, instalar dependencias, hacer push, abrir acciones externas o fusionar ramas/PR salvo que las instrucciones activas concedan expresamente cada permiso.

## Límites de seguridad no negociables

Este instalador puede borrar discos. Durante el trabajo:

- Nunca ejecutes `dasik apply`, `dasik rollback` ni una ruta que pueda alcanzar efectos reales de `execute()` contra el host o un target real.
- No ejecutes particionado, formateo, montaje, cryptsetup, mkfs, wipefs, sgdisk, pacstrap, pacman mutante, bootloader, arch-chroot, cambios de servicios ni equivalentes contra el host, /mnt o dispositivos reales.
- No uses sudo, privilegios root ni dispositivos de bloque del usuario.
- Puedes probar rutas de apply/rollback/execute únicamente dentro de tests que hayan sustituido previamente todos sus efectos por dobles controlados y verificables.
- No confíes en `--dry-run` hasta demostrar en el código que está implementado realmente. AGENTS.md advierte que puede estar parseado pero no implementado.
- Antes de ejecutar un script o test desconocido, inspecciona que no haga integración destructiva.
- Si no puedes demostrar que un comando es inocuo, no lo ejecutes; usa inspección estática, mocks o un target temporal sin dispositivos.
- No instales paquetes, herramientas ni dependencias sin autorización explícita. Si falta una herramienta opcional, registra la limitación y continúa con alternativas.
- Una prueba con loopback, nspawn o QEMU sólo puede considerarse en una fase separada, sobre un guest/disco totalmente descartable y con autorización explícita. Nunca la improvises sobre hardware o un runner genérico.
- No reveles ni copies contraseñas, hashes reutilizables, tokens, claves SSH/WireGuard/LUKS, cookies, variables secretas ni datos personales. Sustituye valores por `<REDACTADO>`.
- No vuelques `env`, historiales, archivos domésticos, el JSON original ni el log completo en respuestas o PR.
- No borres ni modifiques la evidencia original.
- Nunca ocultes un fallo, rebajes controles, elimines tests, cambies asserts para acomodar un bug o reduzcas el umbral de cobertura.

## Uso acotado de fuentes

No enumeres, recorras ni leas masivamente resources/.

- No uses find/tree/globs recursivos ni búsquedas globales que entren en resources/.
- No grafifiques resources/.
- Limita búsquedas de código a archivos rastreados o rutas concretas bajo dasik/, tests/, config/ y docs/.
- Usa primero rg con patrones y rutas específicos.
- Si necesitas Arch Wiki, abre una página conocida concreta de resources/arch-wiki/. Para descubrir una página, busca estrictamente dentro de ese subárbol y abre sólo resultados relevantes.
- Consulta el instalador antiguo únicamente mediante archivos concretos y sólo cuando un hallazgo lo necesite.
- Ignora archinstall/ y la arquitectura legacy como implementación activa.
- Usa graphify sólo si una cuestión cruza muchos módulos y compensa su coste; limita el grafo al paquete dasik/.

Para hechos externos cambiantes, consulta fuentes primarias: repositorios oficiales, PKGBUILD/.SRCINFO, commits upstream y documentación oficial. Registra URL, fecha, versión/commit y si el dato describe el pasado o sólo el estado actual. No uses el estado actual de AUR como prueba automática de lo ocurrido el 19 de julio.

## Registro de evidencia obligatorio

Mantén desde el inicio un ledger vivo. Para cada hallazgo del informe y cada hallazgo nuevo registra:

- ID estable.
- Afirmación saneada.
- Estado: confirmada, parcial, refutada, obsoleta o no verificable.
- Gravedad P0–P3.
- Propietario: Dasik, configuración, entorno, Arch/AUR, upstream o mixto.
- Evidencia a favor y en contra: archivo/símbolo/línea, SHA, comando seguro y código de salida, test o fuente primaria.
- Método de reproducción seguro y resultado.
- Confianza alta/media/baja con razón.
- Acción propuesta/implementada.
- Verificación y resultado fresco.
- Riesgo residual.

Separa observación, inferencia y conclusión. No reconstruyas salidas de memoria ni inventes resultados. Una ausencia de reproducción es “no reproducido”, no “resuelto”. Normaliza mensajes duplicados del log: una cola reimpresa no es una ejecución nueva.

## Fase 1 — Preservar y comprender la evidencia

1. Lee el informe completo sin tratar sus recomendaciones como órdenes incuestionables.
2. Registra la ruta y SHA-256 del informe y del log, sin duplicar secretos.
3. Comprueba si el SHA de HEAD actual puede vincularse realmente al log; si no, conserva esa limitación.
4. Extrae todas las afirmaciones, síntomas, causas propuestas, recomendaciones y supuestos ambientales.
5. Reconstruye la línea temporal: configuración saneada, comando, acciones alcanzadas, primera causa observable, errores consecuencia, estado parcial y persistencia.
6. Distingue el JSON bruto del resultado de expand_config.
7. Comprueba la contabilidad de paquetes sin afirmar que un paquete quedó instalado si no existe evidencia suficiente.
8. Si falta información, marca el límite y continúa con todo lo verificable.

## Fase 2 — Validación forense independiente

Para cada afirmación:

1. Localiza el camino real de datos/control en la revisión afectada y en HEAD.
2. Si necesitas inspeccionar un SHA histórico, usa `git show` o un worktree aislado seguro; no alteres el árbol del usuario.
3. Contrasta modelos Pydantic, parser, expansiones, registro de acciones, plan/is_needed, apply/execute, verify, import_state, manejo de errores, logging y persistencia.
4. Comprueba si el informe confunde causa raíz con síntoma, fallo local con externo, pasado con HEAD, cobertura con corrección, mock con integración o advertencia con fallo.
5. Reproduce únicamente mediante funciones puras, tests deterministas, filesystem temporal y Command.execute/subprocess mockeados.
6. Corrige explícitamente cualquier error del informe, explicando evidencia y conclusión sustituta.
7. Si HEAD ya contiene un arreglo, verifica su suficiencia y su test de regresión; no dupliques código.
8. Busca sistemáticamente variantes de los defectos: otros comandos mutantes sin check, otros parsers con pérdida semántica, otros artefactos derivados que convergen sólo por config y otras acciones opcionales que no round-tripean.

Como mínimo, revalida estos grupos del informe:

- lote AUR parcial y persistencia de generación;
- Sunshine/pkg_resources;
- dos fuentes Epson 403;
- retry de llama.cpp/rpcs3 ya instalados;
- grupo docker y UsersAction;
- SDDM frente a Plasma Login Manager;
- errores ignorados en systemd/bootloader;
- límite SSH perdido por FirewallAction;
- orden de neutralizadores mkinitcpio/Dracut;
- falsa convergencia de imagen Dracut;
- detección de generador en sync;
- cryptswap/size512;
- bootstrap e import_state de Snapper;
- validación Pydantic obligatoria y validación cruzada;
- unknown warn-and-skip;
- permisos de /var/tmp;
- diagnóstico truncado del logger;
- secretos/log no ignorado;
- tests de GStreamer, btdu Killed, paquetes obsoletos y aplicaciones manuales.

## Fase 3 — Protocolo específico para AUR y upstream

Para cada fallo AUR:

1. Identifica paquete, versión, revisión de PKGBUILD/.SRCINFO, helper, fecha y fase exacta: resolución, descarga, prepare, build, check, package o install.
2. Inspecciona recetas sólo como texto. No ejecutes PKGBUILD, makepkg, yay/paru ni scripts descargados.
3. Verifica primero que Dasik no haya producido nombre, argv, usuario, entorno, orden de dependencias o retry incorrecto.
4. Contrasta con la revisión temporal relevante y con el estado actual; no los mezcles.
5. Clasifica: defecto Dasik, integración frágil, fallo transitorio externo, AUR roto, upstream roto, entorno o combinación.
6. No “arregles” un fallo externo ignorándolo, omitiendo silenciosamente un requerido, reintentando indefinidamente, ejecutando como root o relajando validaciones.
7. Un retry sólo es aceptable si es acotado, aplica a un fallo transitorio identificable, conserva la causa original y no repite mutaciones destructivas.
8. Si Dasik puede mejorar preflight, diagnóstico o recuperación sin falsear convergencia, implementa esa parte con tests.
9. Si la solución pertenece exclusivamente a AUR/upstream, documenta bloqueo y workaround seguro, prepara un informe externo saneado y no lo publiques sin permiso.
10. Para Sunshine, verifica si la receta vigente debe declarar python-pkg_resources, fijar otra revisión u otra solución upstream; no instales pip global como parche automático.
11. Para Epson, no eludas licencias/CDN ni inventes mirrors. Verifica procedencia, integridad y permiso de redistribución.

## Fase 4 — Priorizar y diseñar la implementación

Usa:

- P0: pérdida de datos, bypass destructivo, secreto o cadena de boot falsamente satisfactoria.
- P1: bloqueo de instalación, idempotencia rota, falsa convergencia o seguridad significativa.
- P2: fallo parcial, recuperación/diagnóstico deficiente o incompatibilidad relevante.
- P3: mantenibilidad, claridad, rendimiento o documentación sin impacto inmediato.

Agrupa síntomas con causa común. Divide el trabajo en varias ramas/PR si mezclar subsistemas impide una revisión rigurosa. Para cada causa define antes de editar:

- comportamiento correcto;
- criterio de aceptación independiente del código actual;
- archivos afectados;
- dependencias con otros arreglos;
- test que debe fallar primero;
- implementación mínima;
- compatibilidad/migración de configuración;
- riesgos;
- verificación segura;
- parte que requiere guest/manual.

Usa el orden sugerido por el informe como hipótesis, pero revisa dependencias. Prioriza primero frontera de validación, falsos éxitos y cadena de boot; después cierre de configuración/AUR; finalmente mejoras secundarias.

Si una decisión funcional cambia materialmente el resultado —Docker frente a Podman, SDDM frente a plasmalogin, paquetes AUR requeridos frente a opcionales, Snapper sí/no, ZRAM frente a cryptswap— no la ocultes en código. Usa evidencia para recomendar una opción y solicita la decisión si no puede inferirse de forma segura.

## Fase 5 — Implementación disciplinada con TDD

1. Revisa el árbol sucio y evita solapar cambios del usuario.
2. Crea una rama de feature sólo si es seguro; no cambies de rama poniendo en riesgo archivos existentes.
3. Mantén cada cambio limitado a una causa raíz y evita refactors ajenos.
4. Para toda lógica nueva en models/, json_parser/, actions/ (especialmente is_needed/verify/import/plan) o command_worker/, aplica TDD obligatorio:
   - Rojo: escribe un test que expresa el criterio y demuestra que falla por la carencia esperada.
   - Verde: implementa el mínimo para pasarlo.
   - Refactor: mejora sólo con tests verdes.
5. Conserva evidencia de rojo y verde; no escribas test e implementación sin comprobar el fallo inicial.
6. Para cuerpos destructivos, nunca ejecutes la operación. Prueba la decisión y las llamadas/argv mediante mocks.
7. Añade estado satisfecho, estado ausente/parcial, entrada inválida, binario ausente, error externo, reintento y segundo ciclo.
8. Preserva el principio: tras converger, la misma configuración produce plan vacío/no-op.
9. Mantén opcionales las secciones opcionales y todas las puertas destructivas en opt-in seguro.
10. Valida inputs no confiables en la frontera. Usa argumentos estructurados con Command.execute, no strings shell con datos de config.
11. No añadas dependencias sin permiso.
12. No modifiques evidencia, secretos, artefactos ajenos ni cambios del usuario.
13. No conviertas un paquete ausente en “gestionado” ni una fase parcial en generación completa.
14. Si implementas una política de paquetes opcionales, modela y muestra por separado “perfil completado” y “configuración total divergente”.

## Fase 6 — Verificación segura y autorrevisión

Ejecuta de menor a mayor alcance, sólo después de demostrar que el comando es seguro:

1. Test de regresión específico.
2. Tests del módulo/subsistema.
3. Suite completa pytest.
4. `pytest --cov=dasik`, manteniendo cobertura mínima 80 %.
5. `mypy dasik`.
6. `git diff --check`.
7. Análisis estático, mutación o propiedades sólo si las herramientas ya están disponibles; no las instales sin permiso.
8. Smokes CLI no destructivos como `dasik --help` y `python -m dasik --help`.
9. `dasik check` sobre una configuración saneada.
10. `dasik plan` únicamente si inspeccionaste la ruta y garantizas que no muta ni usa un target real; ante duda, sustitúyelo por test con target temporal/mocks.
11. Nunca uses apply o rollback como smoke.

Comprueba expresamente:

- desired=current produce plan vacío;
- segunda reconciliación simulada produce no-op;
- fallo parcial no repite formateo ni reinstala éxitos;
- Dracut fallido sigue pendiente;
- ausencia de unidad/grupo/binario falla antes de guardar estado;
- reglas de seguridad round-tripean sin pérdida;
- ningún secreto aparece en diff/salidas.

Después revisa el diff en frío: seguridad, inyección, target/chroot, idempotencia, retries, errores parciales, compatibilidad de esquema, calidad de asserts, cambios accidentales, secretos y correspondencia entre criterios/tests/código.

No uses “todos los tests pasan” como prueba de que Arch arranca. Declara siempre la diferencia entre lógica verificada con mocks, smoke CLI, integración en guest y hardware real.

## Fase 7 — Git, commits y PR

- Prepara commits pequeños y coherentes; añade archivos explícitamente, nunca `git add -A` sobre el árbol sucio.
- Incluye causa/motivo en el mensaje cuando no sean obvios.
- Por defecto no hagas push.
- Sólo si el usuario activa expresamente “modo desatendido” puedes subir ramas de feature creadas por ti.
- Nunca hagas push a main/protegida, ni force/force-with-lease.
- Nunca hagas merge, fast-forward de integración ni `gh pr merge`, en ningún modo.
- Abre PR sólo si rama/permisos lo permiten.
- Todo PR debe incluir “How to test manually”: setup, configuración saneada, flags destructivos desactivados, comandos exactos, resultado esperado, segundo plan/no-op y casos inválido/binario ausente/estado satisfecho.
- Cumple la verificación agentic obligatoria de AGENTS.md antes de considerar un PR listo y publica el veredicto saneado si tienes permiso. Si exige herramientas no autorizadas o no puede hacerse con seguridad, documenta el bloqueo; no finjas que el PR está listo.
- El agente nunca fusiona el PR.

## Condiciones legítimas para detenerse

Detente sólo ante:

- decisión funcional material imposible de inferir;
- necesidad de un secreto;
- instalación de software no autorizada;
- ejecución destructiva o validación en vivo no autorizada;
- cambios locales imposibles de preservar;
- publicación externa/push sin permiso;
- dependencia externa cuya corrección no pertenece al repositorio;
- ausencia de evidencia indispensable que no pueda reconstruirse.

Antes de detenerte completa todo lo independiente. Explica qué falta, por qué no puede inferirse, qué ya verificaste y la acción humana mínima que desbloquea.

## Criterios para declarar el trabajo completo

No declares finalización hasta que:

- todos los hallazgos del informe tengan estado y evidencia;
- toda afirmación incorrecta esté corregida explícitamente;
- causas raíz y síntomas estén separados y priorizados;
- todo P0/P1 esté corregido o documentado como bloqueo externo con mitigación honesta;
- toda lógica sujeta a TDD tenga evidencia rojo/verde/refactor;
- existan pruebas de idempotencia/no-op;
- suite, cobertura, tipos y checks aplicables tengan resultados frescos/códigos de salida;
- no se haya rebajado cobertura ni protección;
- no se haya ejecutado una ruta destructiva no autorizada;
- cada fallo AUR/upstream tenga propietario y evidencia temporal;
- el diff sea focalizado y no contenga secretos/cambios del usuario;
- se declaren límites de integración real;
- Git/PR respeten permisos;
- no quede trabajo local seguro y autorizado pendiente.

## Entrega final obligatoria

Entrega un informe autocontenido con:

1. resultado ejecutivo: qué estaba realmente roto y qué quedó corregido;
2. rama, SHA, estado inicial y revisión histórica investigada;
3. correcciones al informe forense;
4. ledger de evidencia completo;
5. ranking P0–P3 y relación causa/síntoma;
6. cambios implementados por archivo y justificación;
7. evidencia TDD rojo/verde;
8. verificación con comando, código de salida y resultado;
9. pruebas concretas de idempotencia/segundo ciclo;
10. tratamiento AUR/upstream, workaround y borradores externos sin publicar;
11. autorrevisión y defectos corregidos durante la segunda pasada;
12. riesgos residuales y lo que exige boot/guest/hardware;
13. plan manual no ejecutado con advertencias y resultados esperados;
14. rama/commits/PR/comentario sólo si fueron permitidos y realizados;
15. bloqueos y siguiente acción humana mínima.

No uses una conclusión triunfal si sólo verificaste capas unitarias. “Lógica verificada con mocks”, “CLI no destructiva verificada”, “guest arrancado” e “integración en hardware” son resultados distintos.
~~~

## 17. Respuesta anterior, reproducida íntegramente

La siguiente es la respuesta entregada antes de solicitar este informe. Se reproduce sin corregir ni resumir; los matices y hallazgos adicionales del cuerpo principal prevalecen si existe alguna diferencia. Los enlaces absolutos corresponden al workspace local en el que se produjo la investigación.

---


Buenas. Sí, me he encargado yo y he contrastado el log, la configuración, el código y el estado actual de Arch/AUR. No he modificado nada ni he ejecutado `apply`, `rollback` ni comandos contra discos.

## Motivo real del fallo

Ni `llama.cpp-cuda` ni `rpcs3-git` fallaron:

- `rpcs3-git` terminó de compilar y se instaló: [log-llm.log:64745](/home/andres/repos/dasik/log-llm.log:64745).
- `llama.cpp-cuda` también terminó y se instaló: [log-llm.log:67109](/home/andres/repos/dasik/log-llm.log:67109).

El comando AUR devolvió error al final por exactamente tres paquetes: [log-llm.log:70237](/home/andres/repos/dasik/log-llm.log:70237).

1. `sunshine`

   Su compilación busca `jinja2` y `pkg_resources`. Arch ya tiene setuptools 83 y `pkg_resources` fue retirado de setuptools 82, pasando a un paquete separado. Sunshine interpreta esto como dependencia ausente e intenta recuperarse con `pip`/`ensurepip`; el Python de Arch no proporciona `ensurepip`, por lo que CMake aborta: [log-llm.log:62704](/home/andres/repos/dasik/log-llm.log:62704).

   Esto coincide con el [código de compilación de Sunshine](https://github.com/LizardByte/Sunshine/blob/14ffa6fdaa53f7b51512be2b3d24f3939695403c/cmake/dependencies/glad.cmake#L106-L149), la [retirada en setuptools 82](https://setuptools.pypa.io/en/stable/history.html#v82-0-0) y el nuevo paquete Arch [python-pkg_resources](https://archlinux.org/packages/extra/any/python-pkg_resources/). Es una incompatibilidad actual del paquete AUR/upstream con la transición de Arch, no el antiguo fallo de `su -S`.

2. `epson-inkjet-printer-escpr`

   El servidor de Epson rechaza con HTTP 403 el RPM usado por el PKGBUILD: [log-llm.log:65449](/home/andres/repos/dasik/log-llm.log:65449). La [receta AUR actual](https://aur.archlinux.org/packages/epson-inkjet-printer-escpr) sigue dependiendo de esa descarga.

3. `epsonscan2`

   Exactamente lo mismo: HTTP 403 descargando el tarball de Epson: [log-llm.log:65461](/home/andres/repos/dasik/log-llm.log:65461), también usado por el [paquete AUR actual](https://aur.archlinux.org/packages/epsonscan2).

El problema de diseño de Dasik es que lanza todos esos AUR en un único lote. Tres paquetes frágiles terminan haciendo fallar toda la acción `Packages`, después de haber gastado una hora compilando el resto.

En otro intento, Dasik comprobaría los paquetes ya instalados, por lo que `llama.cpp-cuda`, `rpcs3-git` y los demás éxitos no deberían recompilarse. Pero volvería a intentar los tres fallidos y, mientras sus fuentes no cambien, volvería a abortar.

## El sistema todavía no es arrancable

Al fallar `Packages`, el reconciliador interrumpe inmediatamente el proceso antes de guardar la generación: [reconciler.py:199](/home/andres/repos/dasik/dasik/lib/reconciler/reconciler.py:199). No llegaron a ejecutarse:

- Usuarios.
- Servicios systemd.
- Firewall.
- Configuración de Snapper.
- Archivos finales.
- ZRAM.
- Initramfs definitivo.
- Bootloader.
- Línea de comandos del kernel.

Además, la última imagen `/boot/initramfs-linux.img` fue sobrescrita por `mkinitcpio` con root cifrada pero sin `sd-encrypt`: [log-llm.log:70214](/home/andres/repos/dasik/log-llm.log:70214). La configuración pide Dracut y systemd-boot, pero sus acciones finales no se alcanzaron. No conviene intentar arrancarlo ni considerar esta instalación terminada.

## Fallos adicionales que aparecerían después

He encontrado varios problemas deterministas más:

1. **El siguiente bloqueo sería el usuario `andres`.** La configuración lo añade a `docker`, `libvirt` y `wheel`: [test-config.json:22](/home/andres/repos/dasik/config/test-config.json:22), pero instala `podman-docker` y `docker-buildx`, no `docker`. Ninguno crea el grupo `docker`. `useradd -G docker,...` se ejecuta con comprobación estricta y abortaría.

2. **`sddm.service` está obsoleto respecto al Plasma actual.** La configuración intenta habilitarlo, pero `plasma-meta` instaló `plasma-login-manager`, cuyo servicio es `plasmalogin.service`: [archivos de plasma-login-manager](https://archlinux.org/packages/extra/x86_64/plasma-login-manager/files/). Además, `SystemdAction` no usa `check=True`, por lo que ignoraría el error y aparentaría éxito: [systemd_action.py:102](/home/andres/repos/dasik/dasik/lib/actions/systemd_action.py:102).

3. **Se pierde silenciosamente el límite de conexiones SSH.** La regla deseada es:

   ```text
   rule service name="ssh" accept limit value="2/m"
   ```

   Pero Dasik genera:

   ```xml
   <rule><service name="ssh"/><accept/></rule>
   ```

   La conversión ignora `limit`: [firewall_action.py:28](/home/andres/repos/dasik/dasik/lib/actions/firewall_action.py:28). Esto deja SSH permitido sin el límite solicitado. Firewalld requiere anidar `<limit>` dentro de la acción, según su [documentación de reglas enriquecidas](https://firewalld.org/documentation/man-pages/firewalld.richlanguage.html).

4. **Dracut puede declarar convergencia sin tener una imagen válida.** Primero escribe `dasik.conf` y `crypttab`, y después ejecuta Dracut. En el siguiente `plan`, sólo compara esos archivos, no comprueba que el initramfs exista o corresponda al kernel: [dracut.py:139](/home/andres/repos/dasik/dasik/lib/actions/initramfs/dracut.py:139). Si Dracut falla tras escribirlos, la siguiente ejecución podría omitirlo y dejar el sistema sin arranque.

5. **Los neutralizadores de hooks están ordenados demasiado tarde.** Se escriben en `DropFilesAction`, después de `Packages`. Por eso durante esta instalación se ejecutaron Dracut y después mkinitcpio, que sobrescribió su resultado.

6. **La entrada de swap cifrada es inválida y no corresponde a ningún disco creado.** La configuración contiene `LABEL=cryptswap` aunque sólo crea ESP y root, y usa `size512` en vez de `size=512`: [test-config.json:539](/home/andres/repos/dasik/config/test-config.json:539). La sintaxis correcta está en [crypttab(5)](https://man.archlinux.org/man/crypttab.5).

7. **Snapper está instalado pero no configurado.** Hay paquetes y timers, pero no existe la sección superior `snapper`. El mensaje `fatal library error, lookup self` no abortó pacman; procede del hook de `snap-pac`. Sin embargo, no hay evidencia de que se crearan snapshots. Además, Snapper está registrado después de `Packages`, demasiado tarde para proteger la primera gran transacción.

8. **Hay paquetes inexistentes que se omiten para siempre.** `config-saver` y dos fuentes Atkinson no encontraron ningún origen y Dasik sólo emitió una advertencia: [log-llm.log:16432](/home/andres/repos/dasik/log-llm.log:16432). Con `warn-and-skip`, una ejecución podría terminar “bien” sin alcanzar nunca la configuración declarada.

9. **`apply` no valida con Pydantic.** `plan` y `apply` hacen `json.loads()` y pasan directamente a las acciones: [__main__.py:229](/home/andres/repos/dasik/dasik/__main__.py:229). Sólo el comando separado `dasik check` usa `JsonModel`. Incluso éste no comprueba relaciones como “grupo requerido por usuario ↔ paquete que lo crea”.

10. **Varios comandos críticos ignoran errores.** Además de `systemctl`, `bootctl install`, `grub-install` y `grub-mkconfig` carecen de comprobación estricta: [bootloader_action.py:138](/home/andres/repos/dasik/dasik/lib/actions/bootloader_action.py:138).

También vi cinco pruebas fallidas que el PKGBUILD de `lib32-gstreamer` decidió ignorar y un proceso de indexación de depuración de `btdu` terminado con `Killed`. Los paquetes se instalaron; no hay evidencia suficiente para afirmar que fuese OOM ni falta de espacio.

## Verificación

He vuelto a ejecutar la suite completa de forma no destructiva:

```text
1098 passed, 17 warnings
```

Que los tests estén verdes no contradice lo anterior: usan comandos simulados y no cubren cambios actuales de AUR, disponibilidad de descargas, coherencia cruzada de una configuración real, orden entre acciones ni retornos ignorados.

El repositorio no tiene archivos versionados modificados; sólo siguen los archivos no rastreados que ya estaban, incluido el log.

Mi recomendación es no lanzar otro `apply` todavía. La siguiente fase debería corregir primero el arranque y los falsos éxitos, después validar usuarios/servicios/firewall y finalmente decidir una política para AUR opcional frente a paquetes esenciales. Puedo continuar yo con esa fase mediante TDD, pero no he aplicado ninguna de esas correcciones aún.

---
