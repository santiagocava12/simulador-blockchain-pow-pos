# Simulador de blockchain: Prueba de trabajo (PoW) y Prueba de participación (PoS)

Aplicación web en Python + Flask que simula una red de 10 a 20 nodos, cada uno con su
propia copia de la cadena. Las transacciones van firmadas con Ed25519. En PoW los nodos
minan con dificultad y la recompensa madura tras 6 confirmaciones; en PoS apuestan, un
sorteo ponderado elige al proponente, se vota con umbral de 2/3 y el proponente tramposo
pierde su apuesta. Trae un laboratorio de pruebas y ataques y una bitácora de todo lo que
pasa en la red.

| | |
|---|---|
| Materia | Examen de MT · Universidad Anáhuac México, Facultad de Ingeniería |
| Profesor | Dr. José de Jesús Ángel Ángel |
| Integrantes | Santiago Cavazos Anduaga y Emilio Martinez |
| Fecha de entrega | 8 de octubre de 2026 |
| Video (6 min 29 s, con subtítulos, sin audio) | [https://github.com/santiagocava12/simulador-blockchain-pow-pos/releases/tag/v1.0](https://github.com/santiagocava12/simulador-blockchain-pow-pos/releases/tag/v1.0) ([descargar mp4](https://github.com/santiagocava12/simulador-blockchain-pow-pos/releases/download/v1.0/Video_Simulador_Blockchain.mp4)) · guion en [`docs/GUION_VIDEO.md`](docs/GUION_VIDEO.md) |
| Repositorio | https://github.com/santiagocava12/simulador-blockchain-pow-pos |
| Reporte (máx. 4 páginas) | [`docs/reporte/Reporte_Simulador_Blockchain.pdf`](docs/reporte/Reporte_Simulador_Blockchain.pdf) |

![Simulador en PoW: barra «Ahora», pasos, carrera de mineros y bitácora](docs/reporte/img/fig_pow.png)

## Contenido

1. [Requisitos](#requisitos)
2. [Instalación en una máquina limpia](#instalación-en-una-máquina-limpia)
3. [Cómo correr la aplicación](#cómo-correr-la-aplicación)
4. [Cómo correr las pruebas](#cómo-correr-las-pruebas)
5. [Recorrido rápido de la interfaz](#recorrido-rápido-de-la-interfaz)
6. [Casos de la guía y dónde se prueban](#casos-de-la-guía-y-dónde-se-prueban)
7. [Estructura del proyecto](#estructura-del-proyecto)
8. [Decisiones clave](#decisiones-clave)
9. [Solución de problemas](#solución-de-problemas)

## Requisitos

- **Python 3.10 o más reciente**, con `pip` y `venv`. Todas las pruebas pasan con
  Python 3.13.15 y 3.14.2 (Windows 11).
- Un navegador actual (Edge, Chrome, Firefox o Safari). La página no usa CDN ni
  bibliotecas externas: después de instalar, todo funciona sin internet.
- Dependencias (`requirements.txt`): `flask>=3.0`, `cryptography>=42.0`, `pytest>=8.0`.

## Instalación en una máquina limpia

Descarga o clona el repositorio y abre una terminal en su carpeta (la que tiene
`app.py`). Se crea un entorno virtual `.venv` dentro del proyecto; Git lo ignora.

**Windows (PowerShell)**

```powershell
cd C:\ruta\a\mtbc
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`py` usa la versión más reciente instalada; `py -0` lista las instaladas y
`py -3.13 -m venv .venv` elige una en particular. Si el comando `py` no existe, usa
`python -m venv .venv` después de comprobar con `python --version` que es 3.10 o más
reciente. Si PowerShell no deja ejecutar `Activate.ps1`, ve a
[Solución de problemas](#solución-de-problemas).

**macOS y Linux (Terminal)**

```bash
cd ~/ruta/a/mtbc
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Comprueba antes con `python3 --version` que es 3.10 o más reciente (si no, usa por
ejemplo `python3.13 -m venv .venv`).

Con el entorno activado, la línea de la terminal empieza con `(.venv)`. La instalación
tardó 32 s en un entorno nuevo (Windows 11, incluida la descarga).

## Cómo correr la aplicación

Con el entorno activado y desde la carpeta del proyecto, cualquiera de las dos:

```bash
python app.py     # recomendado
flask run         # la forma que pide la guía
```

Abre **http://127.0.0.1:5000** en el navegador. Para detener el servidor: `Ctrl+C`.

- **Por qué se recomienda `python app.py`:** arranca el servidor con el manejador
  `ManejadorHTTP` de `app.py`, que responde en JSON incluso las peticiones mal formadas
  a nivel del protocolo HTTP (línea de petición basura, `HTTP/9.9`, URL o cabeceras
  gigantes). Con `flask run` esos casos extremos los contesta Werkzeug con una página
  HTML. Todo lo demás es igual: las rutas y los errores 400, 404, 405, 409 y 413 de la
  API son JSON en ambos casos.
- `flask run` encuentra la aplicación solo porque el archivo se llama `app.py`
  (equivale a `flask --app app run`). Si `flask` no se reconoce, `python -m flask run`
  hace lo mismo.
- **macOS:** el puerto 5000 suele estar ocupado por el «Receptor AirPlay» y Flask dice
  `Port 5000 is in use by another program`. Usa otro puerto y abre
  http://127.0.0.1:5001:

  ```bash
  flask run --port 5001
  python -c "import app; app.servir(puerto=5001)"   # equivalente a python app.py en el 5001
  ```

- El estado vive en la memoria del servidor. Recargar la página no pierde nada; detener
  el servidor sí, y al arrancar hay una red nueva (PoW, 10 nodos, semilla `anahuac`).
  El servidor sólo escucha en `127.0.0.1` (tu propia máquina).
- Un hilo en segundo plano (`nucleo/motor.py`) avanza la minería y las rondas PoS
  automáticas; arranca con la primera petición.

## Cómo correr las pruebas

Con el entorno activado y desde la carpeta del proyecto:

```bash
python -m pytest
```

Resultado medido el 8 oct 2026 (Windows 11): **872 pruebas en unos 10 s**, iguales con
Python 3.13.15 y 3.14.2 (`872 passed in 10.19s`). `pytest.ini` fija `testpaths = tests` y
`pythonpath = .`, por eso se corre desde la raíz.

```bash
python -m pytest -k test_w4               # sólo un caso de la guía
python -m pytest -k "test_s3 or test_s6"  # varios casos
python -m pytest tests/test_casos_guia.py # un archivo
python -m pytest -k test_w4 -vv           # con el nombre de cada prueba
```

Toda prueba de un caso de la guía en `test_casos_guia.py` y `test_api.py` empieza con
su código (`test_e1_…`, `test_w4_…`, `test_s6_…`). Las pruebas usan dificultad 1 para
ser rápidas, no arrancan el hilo motor (llaman `sim.paso()` en un bucle con tope) y la
mayoría de los escenarios terminan con `assert_invariantes(sim)`
(`sim.verificar_invariantes() == []`): cadenas válidas, saldos no negativos y
conservación del dinero.

| Archivo | Qué prueba |
|---|---|
| `tests/test_casos_guia.py` | Los casos E1–A4 de la guía (sección 5) directamente sobre `Simulador`, sin Flask; además nonces disjuntos, 10 bloques seguidos, sorteo reproducible y conservación del dinero |
| `tests/test_api.py` | Los mismos casos por HTTP con el cliente de pruebas de Flask y el ataque de datos basura A4 (JSON roto, tipos incorrectos, 2 MB, rutas y métodos inexistentes, NaN) |
| `tests/test_bloque_tramposo.py` | `POST /api/ataques/bloque-tramposo`: un minero difunde un bloque con cada trampa y todos lo rechazan (W5, T1, T3, T4 dentro de un bloque) |
| `tests/test_integracion.py` | Fachada, motor y API juntos (rondas encadenadas, cadena recibida desde fuera, fallas imprevistas) |
| `tests/test_regresion_fase2.py` | Regresiones de la revisión de la fase 2: pestaña atrasada, reorganización de la cadena, errores del protocolo HTTP en JSON |
| `tests/test_unidad_base.py` | Unidades base: errores, entradas, config, cripto, reloj, bloque, reglas, bitácora y trampas |
| `tests/test_unidad_cadena.py` | `libro.py`, `validacion.py` y `red.py` |
| `tests/test_unidad_pow.py` | Minería por rondas, nonces disjuntos y desempate |
| `tests/test_unidad_pos.py` | La ronda PoS como máquina de estados |
| `tests/conftest.py` | Ayudas; reimplementa por separado el hash, la firma y el sorteo de la guía para comprobar el formato, no sólo que el código coincida consigo mismo |

## Recorrido rápido de la interfaz

La pantalla tiene cuatro zonas fijas:

- **Encabezado:** el conmutador **⛏ Prueba de trabajo · PoW | ⚖ Prueba de
  participación · PoS** (cambiarlo crea una red nueva, con confirmación), el
  **Glosario** y el **Tema** (automático, claro u oscuro).
- **Barra «Ahora»:** una frase con lo que pasa («Los 10 nodos están sincronizados en el
  bloque 5. Hay 3 transacciones esperando a ser minadas.»), un botón con el siguiente
  paso sugerido y la **tira de nodos** (✓ sincronizado, ✗ atrasado, ⛏ minando,
  ⏻ desconectado, «!» tramposo).
- **Pasos 1 a 5** en pestañas, con un resumen vivo bajo cada nombre.
- **Bitácora** a la derecha: cada evento con icono, etiqueta y frase completa, con
  filtros (Todo, Bloques, Transacciones, Recompensas, Votación, Rechazos y ataques, Red).

Cada paso empieza con una caja «¿Qué es esto?» y los términos técnicos llevan un botón
«?» con su definición.

**1 · Configurar la red.** Tipo de consenso, número de nodos (10–20), dinero inicial,
recompensa y, en PoW, la dificultad (con la vista previa «El hash debe empezar con 000 ·
en promedio ≈ 4,096 intentos»). Semilla, intentos por ronda, ritmo de la animación,
reloj y regla de castigo están en **Opciones avanzadas**. «Crear red nueva» pide
confirmación porque borra la cadena, las transacciones y la bitácora.

![Paso 1: crear una red nueva](docs/reporte/img/readme_paso1.png)

**2 · Crear transacciones.** «De», «Para» y «Monto» → **Firmar y enviar** (la firma se
hace con la clave del emisor y la red la verifica), o **Generar 3 al azar**. La tabla
«En espera» muestra las pendientes con su firma ✓.

![Paso 2: transacción firmada y lista «En espera»](docs/reporte/img/readme_paso2.png)

**3 · Minar (PoW) o Votar (PoS).**
- *PoW:* **Minar 1 bloque**, **Minar 10 seguidos** o **Detener**. La **carrera de
  mineros** muestra en vivo el nonce de cada nodo (N01 prueba 0, 10, 20…; N02 prueba 1,
  11, 21…), su último hash y cuántos ceros lleva. Al haber ganador aparece el aviso
  «🏆 N10 ganó el bloque 1…» y, si hubo empate, quién ganó por hash menor. **Recompensas
  por madurar** dibuja 6 puntitos por recompensa: se llenan con cada confirmación.
- *PoS:* la línea **① Apuestas — ② Sorteo — ③ Bloque propuesto — ④ Votación —
  ⑤ Resultado** y un botón que nombra la transición («Hacer el sorteo →», «Pedir los
  votos →», «Contar los votos →», «Nuevo sorteo sin N07 →»), además de «Avanzar solo»
  y «Cancelar ronda». La barra del **sorteo** reparte boletos según la apuesta; la barra
  de **votación** marca 2/3 y escribe la regla en números (3 × V ≥ 2 × A).

![Paso 3 en PoW: carrera de mineros y recompensas por madurar](docs/reporte/img/figura4_pow.png)
![Paso 3 en PoS: rechazo de un proponente tramposo, castigo y nuevo sorteo](docs/reporte/img/fig_pos.png)

**4 · Cadena y nodos.** Tabla de nodos (estado, bloque, último hash, sincronizado,
disponible, por madurar o apuesta) con **Ver cadena** y **Desconectar / Reconectar**.
Debajo, la cadena del nodo elegido bloque por bloque, con «hash anterior ✓ coincide» y
**Ver JSON completo**.

![Paso 4: tabla de nodos con N10 desconectado y atrasado](docs/reporte/img/fig_general.png)

**5 · Pruebas y ataques.** Cada tarjeta dice **Qué hace** y **Debería pasar**, tiene un
botón **▶ Probar** y muestra el resultado: verde «Correcto» si la red se comportó como
debía (un rechazo esperado es éxito) y «Ver detalle técnico» con cada llamada HTTP. El
filtro **★ Para el video** deja a mano C5, W4, R1 y T4 (PoW) o S6, S3, S5 y R1 (PoS).
La tarjeta **Nodos tramposos** vuelve tramposo a un nodo con una trampa elegida.

![Paso 5: pruebas y ataques](docs/reporte/img/readme_paso5.png)

## Casos de la guía y dónde se prueban

«Pruebas» = archivos `tests/test_<nombre>.py` que prueban el caso; en `casos_guia` y
`api` el nombre de la prueba empieza con `test_<código>_`
(`python -m pytest -k test_<código>`); `bloque_tramposo` (T1, T3, T4 y W5 dentro de un
bloque) y `regresion_fase2` (A3, pestaña atrasada) usan nombres descriptivos.
«Escenario» = tarjeta del paso 5 (★ = filtro «Para el video»).

| Código | Caso | Pruebas | Escenario del paso 5 |
|---|---|---|---|
| E1 | N fuera de 10–20 (9, 21, «abc», vacío, 10.5) | casos_guia, api | Entradas inválidas › «Red con 25 nodos» |
| E2 | Monto negativo | casos_guia, api | Entradas inválidas › «Monto negativo» |
| E3 | Monto cero | casos_guia, api | Entradas inválidas › «Monto cero» |
| E4 | Monto no numérico | casos_guia, api | Entradas inválidas › «Monto con letras» |
| E5 | Monto vacío o ausente | casos_guia, api | Entradas inválidas › «Monto vacío» |
| E6 | Emisor igual a receptor | casos_guia, api | Entradas inválidas › «Enviarse a sí mismo» |
| E7 | Nodos inexistentes | casos_guia, api | Entradas inválidas › «Nodo que no existe» |
| E8 | Dificultad fuera de rango | casos_guia, api | Entradas inválidas › «Dificultad 9» |
| T1 | Firma alterada | casos_guia, api, bloque_tramposo | Transacciones › «Enviar una transacción con la firma alterada» |
| T2 | Firmada por otra clave | casos_guia, api (`test_t1_t5_…`) | Transacciones › «Firmar con la clave de otro nodo» |
| T3 | Saldo insuficiente | casos_guia, api, bloque_tramposo | Transacciones › «Gastar más de lo que se tiene» |
| T4 | Doble gasto en el mismo bloque | casos_guia, bloque_tramposo | Transacciones › ★ «Gastar dos veces el mismo dinero» |
| T5 | Doble gasto en bloques distintos | casos_guia, api (`test_t1_t5_…`) | Transacciones › «Reenviar una transacción ya registrada» |
| T6 | Minar sin pendientes | casos_guia, api | Minería › «Minar sin transacciones en espera» |
| T7 | Proponer sin pendientes (PoS) | casos_guia, api | Votación › «Iniciar ronda sin transacciones en espera» |
| C1 | Bloque con hash alterado | casos_guia, api | Cadena › ★ «Alterar un bloque del medio…» (qué cambiar: el hash) |
| C2 | Bloque con hash_anterior alterado | casos_guia | Cadena › ★ «Alterar un bloque del medio…» (el hash anterior) |
| C3 | Cadena recibida más corta | casos_guia | Cadena › «Difundir una cadena más corta» |
| C4 | Cadena inválida o mal formada | casos_guia, api | Cadena › «Enviar una cadena mal formada» |
| C5 | Bloque intermedio manipulado | casos_guia | Cadena › ★ «Alterar un bloque del medio…» (el monto) |
| W1 | Dos ganadores en la misma ronda | casos_guia | Minería › «Provocar un empate entre mineros» |
| W2 | Minar mientras ya se mina (409) | casos_guia, api | Minería › «Pedir minar mientras ya se mina» |
| W3 | Dificultad que no se resuelve | casos_guia | Minería › «Dificultad que no se resuelve» |
| W4 | Recompensa antes de 6 confirmaciones | casos_guia | Minería › ★ «Gastar una recompensa antes de 6 confirmaciones» |
| W5 | Recompensa falsa | casos_guia, bloque_tramposo | Minería › «Minero tramposo: bloque con recompensa falsa (u otra trampa)» |
| S1 | Ningún validador con saldo | casos_guia | Votación › «Iniciar ronda sin validadores con saldo» |
| S2 | Apuesta mayor al saldo, cero o negativa | casos_guia, api | Votación › «Apostar más de lo que se tiene (y cero)» |
| S3 | Votación exactamente en 2/3 y apenas debajo | casos_guia | Votación › ★ «Votación exactamente en 2/3» |
| S4 | Voto de un nodo que no es validador | casos_guia, api | Votación › «Votar sin ser validador» |
| S5 | Voto doble | casos_guia, api (`test_s4_s5_…`) | Votación › ★ «Votar dos veces» |
| S6 | Proponente deshonesto: castigo y nuevo sorteo | casos_guia | Votación › ★ «Proponente tramposo: rechazo y castigo» |
| S7 | Todos castigados hasta quedar sin saldo | casos_guia | Votación › «Castigar a todos hasta dejarlos sin saldo» |
| A1 | Reiniciar (también a mitad de minería) | casos_guia, api | Red y robustez › «Reiniciar a mitad de la operación» |
| A2 | Recargar a mitad de una ronda | casos_guia, api | Red y robustez › «Recargar la página a mitad de una ronda» |
| A3 | Dos pestañas a la vez | casos_guia, api, regresion_fase2 | Red y robustez › «Dos pestañas a la vez» |
| A4 | Peticiones con datos mal formados | casos_guia, api | Red y robustez › «Enviar datos mal formados» |
| — | Sincronización de un nodo atrasado | casos_guia (`test_nodo_desconectado_no_mina_y_se_sincroniza_al_reconectar`) | Red y robustez › ★ «Desconectar un nodo y ver cómo se pone al día» (R1) |

## Estructura del proyecto

```
mtbc/
├── app.py                  Flask: la página y la API JSON; python app.py responde en JSON hasta los errores de protocolo
├── requirements.txt        flask, cryptography, pytest
├── pytest.ini              testpaths = tests, pythonpath = .
├── README.md               este archivo
├── nucleo/                 todo el consenso; no depende de Flask
│   ├── __init__.py         marca el paquete
│   ├── errores.py          excepciones con su código HTTP (400, 404, 409, 500)
│   ├── entradas.py         lectura estricta de valores no confiables (enteros, montos, ids de nodo, texto)
│   ├── config.py           parámetros de la simulación (Config) y validar_config
│   ├── cripto.py           Ed25519: claves derivadas de la semilla, firmar y verificar
│   ├── reloj.py            reloj simulado (reproducible) o real, en milisegundos
│   ├── bloque.py           transacciones y bloques: serializar, hash_bloque, revisión de estructura
│   ├── reglas.py           reglas puras: dificultad, sorteo, umbral de 2/3, castigo y votos
│   ├── libro.py            saldos derivados de la cadena y recompensas por madurar (Libro)
│   ├── validacion.py       validar_bloque, validar_cadena, evaluar_cadena_recibida, transacción nueva
│   ├── bitacora.py         eventos que muestra la interfaz
│   ├── trampas.py          lo que proponen o votan los nodos tramposos
│   ├── red.py              Nodo y Red: difundir, sincronizar, transacciones pendientes
│   ├── pow.py              minería por rondas: nonces disjuntos y desempate
│   ├── pos.py              ronda PoS como máquina de estados
│   ├── simulador.py        fachada única: candado, atomicidad, acciones y ataques
│   └── motor.py            hilo que avanza la minería y las rondas automáticas
├── templates/
│   └── index.html          la página (una sola)
├── static/
│   ├── estilos.css         estilos, tema claro y oscuro
│   └── js/                 interfaz en módulos de JavaScript, sin dependencias externas
│       ├── main.js         punto de entrada (lo carga index.html)
│       ├── api.js          llamadas a las rutas de la API (DISENO §18)
│       ├── estado.js       la instantánea del servidor y el sondeo de /api/estado
│       ├── ahora.js        barra «Ahora»: qué pasa, qué sigue y la tira de nodos
│       ├── pasos.js        las cinco pestañas numeradas
│       ├── navegacion.js   ir a un paso desde cualquier módulo
│       ├── paso1_config.js paso 1 y conmutador PoW/PoS
│       ├── paso2_transacciones.js  paso 2: enviar monedas y transacciones en espera
│       ├── paso3_pow.js    paso 3 en PoW: carrera de mineros y recompensas por madurar
│       ├── paso3_pos.js    paso 3 en PoS: ronda, sorteo, votación e historial
│       ├── pos_comun.js    textos de la ronda PoS que comparten «Ahora» y el paso 3
│       ├── paso4_cadena.js paso 4: tabla de nodos y explorador de la cadena
│       ├── paso5_pruebas.js paso 5: filtros, tarjetas «▶ Probar», «Nodos tramposos» y petición a mano
│       ├── escenarios.js   los escenarios del paso 5 (UX §7): llamadas a la API y veredicto
│       ├── trampas.js      nombres en pantalla de las trampas
│       ├── bitacora.js     la bitácora con filtros
│       ├── glosario.js     botones «?» y cajón «Glosario»
│       ├── ui.js           piezas comunes: línea de resultado, confirmación en línea
│       └── util.js         ayudas pequeñas
├── tests/                  pruebas pytest (detalle en «Cómo correr las pruebas»)
│   ├── conftest.py         ayudas y reimplementación independiente del hash, la firma y el sorteo
│   ├── test_api.py         casos de la guía por HTTP y datos basura (A4)
│   ├── test_bloque_tramposo.py  ruta del bloque tramposo de un minero (W5)
│   ├── test_casos_guia.py  casos E1–A4 sobre el Simulador
│   ├── test_integracion.py fachada, motor y API juntos
│   ├── test_regresion_fase2.py  regresiones de la revisión de la fase 2
│   ├── test_unidad_base.py errores, entradas, config, cripto, reloj, bloque, reglas, bitácora, trampas
│   ├── test_unidad_cadena.py    libro, validación y red
│   ├── test_unidad_pos.py  ronda PoS como máquina de estados
│   └── test_unidad_pow.py  minería por rondas, nonces disjuntos y desempate
└── docs/
    ├── DISENO.md           contrato de diseño: módulos, reglas, API y decisiones (§1, §21, §22)
    ├── UX.md               especificación de la interfaz
    ├── GUION_VIDEO.md      guion del video y lista de verificación para grabar
    ├── reporte/
    │   ├── reporte.html    fuente del reporte
    │   ├── Reporte_Simulador_Blockchain.pdf   el reporte (máx. 4 páginas)
    │   └── generar_pdf.py  regenera el PDF con Chrome o Edge (sólo biblioteca estándar)
    └── ux/
        └── maqueta.html    maqueta navegable con datos de ejemplo (se abre con doble clic)
```

## Decisiones clave

El detalle y su porqué están en [`docs/DISENO.md`](docs/DISENO.md) (§1 decisiones,
§21 y §22 cambios aprobados tras cada revisión).

- **Desempate en PoW.** En cada ronda todos los mineros conectados prueban k nonces
  (50 por omisión) y el minero i prueba sólo i, i+N, i+2N…, así nadie repite trabajo.
  Si varios encuentran un hash válido en la misma ronda, **gana el hash menor**
  (comparación de la cadena hexadecimal); en un empate exacto, el de menor índice. El
  empate queda en la bitácora. `nucleo/pow.py`: `ronda_pow`, `elegir_ganador`
  (DISENO §1 y §14).
- **Recompensa con 6 confirmaciones.** La recompensa del bloque h queda pendiente y se
  acredita cuando la cadena llega a la altura h + 6; mientras tanto se ve, pero no
  cuenta en el saldo disponible. Un monto distinto al establecido invalida el bloque
  («recompensa falsa»). `Libro.madurar` y `Libro.aplicar_bloque` (§9).
- **Castigo en PoS.** Regla A (por omisión): el proponente cuyo bloque se rechaza pierde
  **toda su apuesta**. Regla B: pierde `min(apuesta, ⌈α · valor de las transacciones⌉)`.
  Lo castigado se **quema**, el castigado sale de la ronda y hay un **nuevo sorteo** con
  los demás; si no queda nadie, la ronda termina sin bloque. El castigo se registra en
  el campo `castigos` del siguiente bloque aceptado. `reglas.calcular_castigo`,
  `pos.castigar_proponente` (§1, §8, §15).
- **Sorteo y votación verificables.** El sorteo es el de la guía:
  `sha256(hash_anterior|numero|intento) mod A` sobre las apuestas ordenadas por id. Cada
  voto va firmado sobre el hash del bloque sin votos y pesa lo apostado; se acepta si
  `3 · V ≥ 2 · A` (exactamente 2/3 se acepta). El proponente debe votar a favor de su
  propio bloque: ese voto es su firma (§1, §8, §21.4).
- **Firmas e identidad.** Ed25519 con claves derivadas de la semilla; el génesis publica
  las claves públicas y los saldos iniciales, y toda firma se verifica contra ese
  directorio. Limitación conocida: quien conozca la semilla puede firmar por cualquier
  nodo; en una red real cada cartera generaría su clave en secreto (§1, §21.9).
- **Cada nodo valida antes de agregar.** Los saldos no se guardan: se obtienen
  reproduciendo la cadena (`Libro`). Una cadena recibida se adopta sólo si es válida
  (génesis idéntico, hashes, firmas, saldos, consenso) **y** más larga que la propia
  (`evaluar_cadena_recibida`). Un nodo tramposo sólo hace trampa en lo que propone o
  vota; ninguna copia de la cadena se corrompe (§1, §10, §13).
- **Reproducible.** Reloj simulado (desde el 1 ene 2026, +1 s por transacción o bloque)
  y azar derivado de la semilla y del contexto, nunca un `random` global: misma
  semilla + mismos pasos ⇒ mismos hashes. Montos enteros para no tener errores de
  punto flotante (§1).
- **Robustez.** Toda acción pasa por la fachada `Simulador`: candado (`RLock`),
  validación estricta de cada valor (`nucleo/entradas.py`) y atomicidad (si algo falla
  no se aplica nada). La API responde siempre JSON (400, 404, 405, 409, 413), nunca un
  traceback; dos pestañas que avanzan la misma ronda reciben 409 (§1, §16, §18, §22).

## Solución de problemas

**`flask` no se reconoce, `flask: command not found` o
`ModuleNotFoundError: No module named 'flask'`.** El entorno no está activado o no se
instalaron las dependencias. Actívalo (la línea debe empezar con `(.venv)`) y repite
`python -m pip install -r requirements.txt`. También puedes usar el Python del entorno
sin activarlo:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
.venv\Scripts\python.exe app.py
```
```bash
.venv/bin/python -m pip install -r requirements.txt           # macOS y Linux
.venv/bin/python app.py
```

**PowerShell: «la ejecución de scripts está deshabilitada en este sistema» al correr
`Activate.ps1`.** Usa el Python del entorno sin activarlo (arriba) o permite scripts
sólo en esa ventana y vuelve a activar:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

**Puerto ocupado.** En macOS y Linux Flask dice `Port 5000 is in use by another
program`. En macOS suele ser el Receptor AirPlay (Ajustes del Sistema › General ›
AirDrop y Handoff). Usa otro puerto: `flask run --port 5001` o
`python -c "import app; app.servir(puerto=5001)"`, y abre http://127.0.0.1:5001. En
Windows, un segundo servidor en el mismo puerto puede arrancar **sin avisar** (lo
comprobamos) y no se sabe cuál de los dos contesta: si la página se comporta raro,
cierra las otras terminales que tengan el servidor. Para ver quién usa el puerto:
`netstat -ano | findstr :5000` (Windows) o `lsof -i :5000` (macOS y Linux).

**Versión de Python.** Revisa con `python --version` (en Windows también
`py --version`). Con Python 3.9 o anterior la aplicación no arranca: el código usa
anotaciones `str | None`, que fallan al importar con
`TypeError: unsupported operand type(s) for |`. Instala Python 3.10 o más reciente,
borra la carpeta `.venv` y vuelve a crear el entorno con esa versión
(`py -3.13 -m venv .venv` o `python3.13 -m venv .venv`, por ejemplo). En Windows, si
`python` abre la Microsoft Store, instala Python desde python.org marcando «Add
python.exe to PATH», o usa `py`.

**Ubuntu o Debian: `ensurepip is not available` al crear el entorno.** Falta el
paquete de `venv`: `sudo apt install python3-venv` (o el que indique el mensaje, por
ejemplo `python3.13-venv`), borra la carpeta `.venv` a medio crear y vuelve a crearla.

**La página dice «Sin conexión con el servidor; reintentando…».** El servidor se
detuvo. Arráncalo otra vez: empieza con una red nueva.

**Quiero repetir exactamente una demostración.** Misma semilla (Paso 1 › Opciones
avanzadas › Semilla) y mismos pasos ⇒ mismos hashes, ganadores y sorteos. El ritmo de
la animación no cambia los resultados, sólo la velocidad.
