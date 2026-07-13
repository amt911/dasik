> **Nota (2026-07):** este documento es en parte aspiracional. `tests/test_disk_integration.py`
> **no existe**, y `--dry-run` **no está implementado** — el equivalente real es
> `dasik plan <config>` (solo lectura). Para probar el particionado y una
> instalación completa de forma funcional y segura, usa el harness nuevo:
> [`docs/vm-testing.md`](vm-testing.md) (capa loopback + capa QEMU) y
> [`docs/testing-without-a-vm.md`](testing-without-a-vm.md).

# Cómo Probar el Sistema de Particionado

## ✅ Validación de Configuración (Seguro)

Puedes validar tu configuración sin tocar ningún disco:

```bash
cd /home/andres/repos/archlinux-script-installer/new

# Probar la configuración de ejemplo
python tests/test_disk_integration.py

# O probar tu propia configuración
python tests/test_disk_integration.py config/mi-configuracion.json
```

Esto valida:
- ✅ JSON sintácticamente correcto
- ✅ Todos los campos requeridos presentes
- ✅ Tipos de datos correctos
- ✅ Tamaños de particiones válidos
- ✅ Labels únicos
- ✅ LUKS configurado correctamente si encrypt=true

## 🧪 Probar con dasik (Modo Simulación)

Para ver qué haría sin ejecutar nada:

```bash
# Instalar el paquete en modo desarrollo
pip install -e .

# Ver la ayuda
dasik --help

# Ejecutar con la configuración (sin hacer cambios reales)
dasik config/test-config-with-disks.json --verbose --dry-run
```

**NOTA**: Actualmente `--dry-run` está preparado pero aún no implementado completamente en DiskPartitionAction. Se ejecutará pero SÍ hará cambios.

## ⚠️ Ejecutar Particionado Real

**¡PELIGRO! Esto BORRARÁ datos si wipe_disk=true o format=true**

```bash
# Solo en una VM o disco de prueba
dasik config/test-config-with-disks.json --verbose
```

### Flujo de Ejecución

1. **Carga configuración** → Valida con Pydantic
2. **Muestra layout actual** → `lsblk`
3. **Crea tabla de particiones** → `parted mklabel`
4. **Crea particiones** → `parted mkpart`
5. **Formatea particiones** → `mkfs.*`
6. **Encripta si necesario** → `cryptsetup`
7. **Monta todo** → `mount`
8. **Muestra resultado** → Mapa de particiones

### Después del Particionado

El `ActionsHandler` almacena el mapeo de particiones:

```python
handler = ActionsHandler("config.json")

# Obtener dispositivos específicos
boot_device = handler.get_partition("boot")  # "/dev/sda1"
root_device = handler.get_partition("root")  # "/dev/sda3" o "/dev/mapper/cryptroot"

# Ver todas
all_partitions = handler.partition_map
# {"boot": "/dev/sda1", "swap": "/dev/sda2", "root": "/dev/sda3"}
```

## 📋 Archivos de Configuración Disponibles

### `config/test-config-with-disks.json`
Configuración completa con discos, locales, timezone, etc.
- **Propósito**: Testing completo del sistema
- **Discos**: `/dev/sda` - EXT4 simple sin encriptación
- **Formato**: No formatea (format=false) - seguro para testing

### `config/disk-example.json`
Solo configuración de discos - Setup avanzado
- **Propósito**: Producción con máxima seguridad
- **Discos**: Encriptado LUKS + BTRFS con subvolúmenes
- **Formato**: SÍ formatea (format=true)

### `config/disk-simple-ext4.json`
Solo configuración de discos - Setup simple
- **Propósito**: Instalación básica
- **Discos**: EXT4 sin encriptación
- **Formato**: SÍ formatea (format=true)

## 🛠️ Herramientas Requeridas

Verifica que tienes las herramientas instaladas:

```bash
# En Arch Linux live ISO, todas estas están disponibles
which parted mkfs.ext4 mkfs.btrfs mkfs.fat mkswap cryptsetup btrfs mount lsblk

# Si falta alguna (poco probable en Arch ISO):
pacman -S parted dosfstools e2fsprogs btrfs-progs cryptsetup util-linux
```

## 🐛 Debugging

Si algo falla:

```bash
# Ver qué está pasando con verbose
dasik config.json --verbose

# Si falla, ver los logs del sistema
journalctl -xe

# Ver estado actual de discos
lsblk -f
fdisk -l

# Ver dispositivos de mapper (encriptados)
ls -la /dev/mapper/
```

## 🎯 Próximos Pasos

1. **Implementar dry-run real** en `DiskPartitionAction`
2. **Añadir rollback** si algo falla a mitad del proceso
3. **Crear snapshots** antes de particionar (si es re-particionado)
4. **Logging detallado** de cada comando ejecutado
5. **Tests unitarios** para cada método

## 📚 Documentación Completa

- `docs/SUMMARY-DISK-SYSTEM.md` - Resumen ejecutivo
- `docs/DISK-PARTITIONING-EXPLAINED.md` - Explicación detallada
- `docs/disk-partitioning.md` - Referencia técnica
- `examples/disk_partitioning_example.py` - Ejemplos de código
