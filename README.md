# Compresor Matricial "MTXC"
### Demostración de una de las tantas aplicaciones del Álgebra lineal en casos reales
---
## Archivos del proyecto
| Archivo | Qué hace |
|---|---|
| `motor.py` | **Toda la matemática y la lógica.** No tiene interfaz. Lo usa el archivo GUI. |
| `mtxc_gui.py` | **Interfaz gráfica** (tkinter). Llama a `motor.py`; no contiene matemática. |
| `requirements.txt` | Dependencias del proyecto. |
---

## Instalación
```
pip install -r requirements.txt
```
Dependencias: `numpy`. No se usa ninguna librería de compresión externa.

El resto son módulos de la biblioteca estándar de Python (`tkinter`, `multiprocessing`, `concurrent.futures`, `struct`, etc.). `tkinter` viene incluido con Python

Probado con Python 3.12
---

## Cómo usar la GUI

```bash
python mtxc_gui.py
```
Se abre una ventana con tres botones:

1. **Seleccionar archivos**: uno o varios, de cualquier tipo.
2. **Comprimir**: genera un `.mtxc` junto al primer archivo seleccionado.
3. **Descomprimir**: pide un `.mtxc` y una carpeta de destino.

Mientras trabaja, la ventana sigue respondiendo y la barra muestra el **porcentaje real** de avance en los modos por bloques (ver sección 9). En los demás modos la barra se anima hasta que termina. Al finalizar se muestran el modo detectado, los tamaños, la reducción y la transformación usada.

## Uso desde código

```python
import motor

if __name__ == "__main__":      # necesario porque se usan procesos hijos
    stats = motor.comprimir(["foto.bmp"], "foto.mtxc")       # devuelve un dict con estadísticas
    rutas = motor.descomprimir("foto.mtxc", "salida/")        # devuelve la lista de archivos escritos
```

Ambas funciones aceptan un parámetro opcional `progreso`: una función que recibe un número de 0.0 a 1.0.
---

## Qué hace el programa por dentro

### 1. El archivo como bytes

Todo archivo es una secuencia de bytes (números de 0 a 255). El programa lee el archivo y trabaja con esos números directamente.

```
"AAAB"  -> [65, 65, 65, 66]
```

### 2. Los bytes como vectores (bloques)

Los bytes se agrupan de **N en N** (N=8 por defecto). Cada grupo es un **vector columna**. Todos los vectores forman la **matriz X**.

```
x = [65]     ← vector columna de 4 bytes (bloque de tamaño N=4)
    [65]
    [65]
    [66]
```

### 3. La transformación matricial: Y = D1·X (mod 256)

Se construye la **matriz D1** (diferencias de primer orden):

```
D1 = [  1   0   0   0 ]
     [ -1   1   0   0 ]    (-1 se escribe 255 porque -1 mod 256 = 255)
     [  0  -1   1   0 ]
     [  0   0  -1   1 ]
```

Se multiplica D1 por el bloque x:

```
y = D1*x = [65, 0, 0, 1]
```

**¿Por qué?** Cuando los bytes consecutivos son parecidos (como en imágenes), las diferencias son pequeñas y se repiten mucho. Eso es lo que Huffman puede comprimir mejor.

### 4. La inversa: X = D1⁻¹·Y

D1 siempre tiene inversa (su determinante es 1, que es impar, condición de invertibilidad en Z/256Z). La inversa es la "suma acumulada":

```
D1-1 = [ 1  0  0  0 ]
       [ 1  1  0  0 ]
       [ 1  1  1  0 ]
       [ 1  1  1  1 ]
```

Se verifica que D1·D1-1 = Identidad (el programa lo comprueba con `verificar_inversa()`).

### 5. La decisión: ¿aplicar D1 o no?

Se mide la **entropía de Shannon** antes y después de D1:

```
H = -Σ p(v) · log₂(p(v))
```

- **H menor** = distribución más desigual = Huffman puede comprimir mejor.
- Si `H_con_D1 < H_sin_D1`: se aplica D1.
- Si no: se usan los datos originales (identidad).

Para **texto normal**, casi siempre gana la identidad (los caracteres no tienen relación numérica con sus vecinos). Para **imágenes**, casi siempre gana D1 (los píxeles vecinos sí son similares).

En los modos por bloques (sección 9) esta decisión se toma **de forma independiente en cada bloque**, y la interfaz muestra en cuántos bloques se aplicó D1.

### 6. Para imágenes: Y = D·X·Dᵀ (transformación 2D)

Las imágenes BMP de 24 bits sin comprimir se tratan como matrices 2D de píxeles. Para cada bloque de 8×8 píxeles de cada canal de color:

```
Y = D * X * Dt    (mod 256)
```

`D*X` aplica las diferencias a las **filas** del bloque.
`·Dt` aplica las diferencias a las **columnas**.

Esto captura la correlación espacial en ambas dimensiones. Resultado: la entropía puede bajar de 7.7 a 3.0 bits/byte (más del 80% de reducción).

Este modo se usa cuando se comprime **un solo** BMP de 24 bits. Un BMP dentro de un paquete de varios archivos se trata como un archivo genérico.

### 7. Huffman: la compresión real

Una vez decidida la representación (con o sin D1), se codifica con Huffman: los bytes más frecuentes reciben los códigos binarios más cortos. Este paso es el que realmente reduce el tamaño del archivo.

> **Nota importante:** las matrices no comprimen. Reorganizan los datos para hacer más eficiente la compresión de Huffman.

La implementación está optimizada con numpy, sin cambiar el algoritmo ni el resultado:
- **Compresión:** los códigos se convierten a bits con tablas de búsqueda y se empacan con `np.packbits`, en vez de un ciclo de Python por cada byte. Produce exactamente los mismos bytes que la versión con ciclo, entre 4 y 10 veces más rápido.
- **Descompresión:** se usa una tabla de búsqueda con un ciclo más ajustado (unas 4 veces más rápido). Si algún código supera los 16 bits, se usa el camino general.

### 8. El formato .mtxc
El archivo comprimido guarda todo lo necesario para reconstruir el original:
- Firma `MTXC` (para identificar el formato), seguida de un byte de versión y un byte de **modo** (6 bytes en total).
- Si se aplicó D1 o no, la tabla de frecuencias de Huffman y los datos comprimidos.
- La extensión original del archivo (por ejemplo `.txt`), para reconstruirlo con el mismo tipo.
- `MTXC` es una extensión "inventada" para el proyecto; surge de la palabra MaTriCes.

Modos del formato:

| Modo | Nombre | Cuándo se usa |
|---|---|---|
| 0 | Vacío | Archivo(s) sin contenido |
| 1 | Genérico | Un archivo pequeño o mediano (D1 en 1D + Huffman) |
| 2 | Imagen | Un solo BMP de 24 bits sin comprimir (D·X·Dᵀ + Huffman) |
| 3 | Paquete | Varios archivos, todos menores de 16 MB |
| 4 | Almacenado | Si comprimir agrandaría el archivo, se guarda tal cual |
| 5 | Bloques | Un solo archivo de 16 MB o más (no BMP 24-bit) |
| 6 | Paquete por bloques | Varios archivos, alguno de 16 MB o más |

Los `.mtxc` creados con versiones anteriores siguen abriéndose. Lo contrario no se cumple: una versión antigua del programa no puede abrir los modos 5 y 6, estas versiones ya no se usan en esta ultima version porque se cambiaron muchos de los procesos que usaban, En este repositorio solo hay registro de unas cuantas de ellas por lo que eata es aproximadamente las version 8 del proyecto. Hubo muchos problemas en el repositorio original (confusion en las ramas y commits que no llegaban a git hub por x motivos) por lo que se opto por hacer uno nuevo.

### 9. Archivos grandes: bloques y procesamiento en paralelo

El cálculo matricial es muy barato. El cuello de botella estaba en Huffman, que procesa un byte por vuelta de ciclo y en un solo núcleo. Con archivos grandes, eso es más del 95 % del tiempo.

Para resolverlo, los archivos grandes se parten en **bloques independientes de 8 MB**. Cada bloque se comprime (D1 + Huffman) y se descomprime por separado, y los bloques se reparten entre los núcleos del procesador con `ProcessPoolExecutor`.

- Se activa a partir de **16 MB** (`UMBRAL_BLOQUES`); el tamaño de bloque es `TAM_BLOQUE`. Ambas son constantes en `motor.py`.
- Cada proceso lee solo su bloque del disco y los resultados se escriben en orden, así que la memoria usada no depende del tamaño del archivo.
- Cada bloque guarda su propia tabla de frecuencias (hasta ~1.3 KB), un costo despreciable frente a 8 MB.
- Los archivos y paquetes pequeños siguen generando exactamente el mismo `.mtxc` que antes.

Otras mejoras de rendimiento en la versión final :
- **Interfaz fluida:** la compresión corre en un **proceso aparte** (no en un hilo), porque un hilo comparte el GIL con la ventana y la congelaba durante los ciclos pesados.
- **Recolector de basura pausado** durante la compresión y la descompresión (se reactiva siempre al terminar, incluso si hay un error).
- **Prioridad de proceso elevada** al iniciar la GUI. En Windows funciona sin permisos especiales; en Linux/macOS puede requerirlos y, si no se puede, se ignora.
---

## Resultados de las pruebas

Pruebas funcionales (todas reconstruyen el original byte a byte):

| Caso | Original | Comprimido | Resultado |
|---|---|---|---|
| Texto normal | 3,000 B | 1,546 B | Idéntico |
| Texto repetitivo | 8,000 B | 1,028 B | Idéntico |
| Archivo vacío | 0 B | 6 B | Idéntico |
| 13 bytes (pequeño) | 13 B | 19 B | Idéntico (creció por la firma que deja en el archivo) |
| UTF-8 con acentos | 1,760 B | 1,153 B | Idéntico |
| Paquete de 3 archivos | 28,814 B | 25,333 B | Todos idénticos |

Pruebas de rendimiento con archivos grandes (modo por bloques; los tiempos dependen de la cantidad de núcleos y del disco del equipo):

| Caso | Tiempo al comprimir | Tiempo al descomprimir | Reducción |
|---|---|---|---|
| Texto repetitivo, 1 GB | 14.7 s | 32.7 s | 87 % |
| PNG, 100 MB | 4.6 s | 15.9 s | 27 % |
|Paquete de archivos, 1.1 GB | 39.8s | 44.3 | 80 % |
La descompresión sigue siendo más lenta que la compresión porque el decodificador de Huffman es un ciclo de Python.
---

## Limitaciones conocidas

- **Descompresión:** es la parte más lenta (ver arriba). Ganaría mucho con un compilador JIT como Numba, a costa de agregar una dependencia.
- **Modos por bloques (5 y 6):** no tienen el respaldo de "almacenado". Un archivo incompresible puede quedar algo más grande que el original.
- **Paquetes con archivos medianos:** si todos los archivos miden menos de 16 MB, el paquete usa el modo 3, que descomprime los archivos uno tras otro en un solo núcleo.
- **BMP dentro de un paquete:** se comprimen como archivos genéricos, sin la transformación 2D, así que pueden comprimir menos que solos.
- **Compatibilidad:** los `.mtxc` con modo 5 o 6 no se pueden abrir con versiones anteriores del programa.
- **Scripts propios en Windows:** el código que llame a `motor.comprimir` o `motor.descomprimir` debe ir dentro de `if __name__ == "__main__":`, porque se crean procesos hijos.