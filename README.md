# Compresor Matricial "MTXC"
### Demostracion de una de las tantas aplicaciones del Álgebra lineal en casos reales 
---
## Archivos del proyecto
| Archivo | Qué hace |
|---|---|
| `motor.py` | **Toda la matemática y la lógica.** No tiene interfaz. Lo usa el archivo Gui. |
---

## Instalación
```
pip install -r requirements.txt
```
Dependencias: `numpy`. No se usa ninguna librería de compresión externa.
---

## Cómo usar la GUI

```bash
python mtxc_gui.py
```
Se abre una ventana con la que interactuar.
---

## Qué hace el programa por dentro

### 1. El archivo como bytes

Todo archivo es una secuencia de bytes (números de 0 a 255). El programa lee el archivo y trabaja con esos números directamente.

```
"AAAB"  →  [65, 65, 65, 66]
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
y = D1·x = [65, 0, 0, 1]
```

**¿Por qué?** Cuando los bytes consecutivos son parecidos (como en imágenes), las diferencias son pequeñas y se repiten mucho. Eso es lo que Huffman puede comprimir mejor.

### 4. La inversa: X = D1-1·Y

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

### 6. Para imágenes: Y = D·X·Dt (transformación 2D)

Las imágenes BMP de 24 bits sin comprimir se tratan como matrices 2D de píxeles. Para cada bloque de 8×8 píxeles de cada canal de color:

```
Y = D · X · Dt    (mod 256)
```

`D·X` aplica las diferencias a las **filas** del bloque.
`·Dt` aplica las diferencias a las **columnas**.

Esto captura la correlación espacial en ambas dimensiones. Resultado: la entropía puede bajar de 7.7 a 3.0 bits/byte (más del 80% de reducción).

### 7. Huffman: la compresión real

Una vez decidida la representación (con o sin D1), se codifica con Huffman: los bytes más frecuentes reciben los códigos binarios más cortos. Este paso es el que realmente reduce el tamaño del archivo.

> **Nota importante:** las matrices no comprimen. Reorganizan los datos para hacer más eficiente la compresión de Huffman.

### 8. El formato .mtxc
El archivo comprimido guarda todo lo necesario para reconstruir el original:
- Firma `MTXC` (para identificar el formato)
- Modo (genérico, imagen 2D, paquete, almacenado)
- Si se aplicó D1 o no
- `MTXC` Extención "Inventada" para el proyecto, surge de la palabra MaTriCes 

---
## Resultados de las pruebas
| Caso | Original | Comprimido | Resultado |
|---|---|---|---|
| Texto normal | 3,000 B | 1,546 B | Idéntico |
| Texto repetitivo | 8,000 B | 1,028 B | Idéntico |
| Archivo vacío | 0 B | 6 B |  Idéntico |
| 13 bytes (pequeño) | 13 B | 19 B | Idéntico (creció por la firma que deja en el archivo) |
| UTF-8 con acentos | 1,760 B | 1,153 B | Idéntico |
| Paquete de 3 archivos | 28,814 B | 25,333 B |  Todos idénticos |
---
