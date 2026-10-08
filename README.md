# Simulador de blockchain: Proof of Work y Proof of Stake

Aplicación web en **Python + Flask** que simula una blockchain de **10 a 20 nodos** en
dos versiones: **Proof of Work** (los nodos minan compitiendo por un hash válido) y
**Proof of Stake** (los validadores apuestan, se sortea un proponente y el bloque se
vota). Cada nodo guarda su propia copia de la cadena y valida lo que recibe antes de
aceptarlo; las transacciones van firmadas con Ed25519.

Proyecto del examen de MT · Universidad Anáhuac México, Facultad de Ingeniería ·
Dr. José de Jesús Ángel Ángel · Santiago Cavazos Anduaga y Emilio Martinez.

## Requisitos

- Python 3.10 o más reciente (probado con 3.13 y 3.14), con `pip` y `venv`.
- Un navegador actual. La página no usa CDN: después de instalar funciona sin internet.

## Instalación en una máquina limpia

Desde la carpeta del proyecto (la que tiene `app.py`):

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS y Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Cómo correr la aplicación

Con el entorno activado:

```bash
flask run
```

Abre **http://127.0.0.1:5000**. Para detener el servidor: `Ctrl+C`.

- También se puede arrancar con `python app.py` (mismo puerto). Con esta forma, incluso
  las peticiones HTTP mal formadas a nivel de protocolo se responden en JSON.
- En macOS el puerto 5000 suele estar ocupado por AirPlay: usa `flask run --port 5001`
  y abre http://127.0.0.1:5001.
- El estado vive en la memoria del servidor: recargar la página no pierde nada; al
  reiniciar el servidor empieza una red nueva (PoW, 10 nodos, semilla `anahuac`).

## Cómo correr las pruebas

```bash
python -m pytest
```

872 pruebas automatizadas (unos 10 s). Cada caso de la sección 5 de la guía tiene
pruebas cuyo nombre empieza con su código, por ejemplo:

```bash
python -m pytest -k "test_w4 or test_s6"
```

## Uso rápido

La pantalla tiene una barra «Ahora» (qué está pasando y el siguiente paso sugerido),
la tira de nodos con su altura y si están sincronizados, cinco pasos y la bitácora de
eventos a la derecha:

1. **Configurar**: modo PoW o PoS, número de nodos (10–20), saldo inicial, recompensa
   y dificultad; lo demás en «Opciones avanzadas» (semilla, regla de castigo, etc.).
2. **Transacciones**: enviar monedas entre nodos (se firman) o generarlas al azar.
3. **Minar** (PoW) o **Votar** (PoS): la carrera de mineros con sus nonces, o la ronda
   de apuestas, sorteo, bloque propuesto y votación con el umbral de 2/3.
4. **Cadena y nodos**: la copia de la cadena de cada nodo, bloque por bloque.
5. **Pruebas y ataques**: un botón por cada caso de la guía (entradas inválidas, firmas
   alteradas, doble gasto, cadena manipulada, empate, recompensa antes de 6
   confirmaciones, proponente tramposo, votación exacta en 2/3, etc.).

## Estructura del proyecto

```
app.py                 servidor Flask: la página y la API JSON (sólo valida y llama al núcleo)
requirements.txt       dependencias: flask, cryptography, pytest
pytest.ini             configuración de pytest
nucleo/                todo el consenso; se usa y se prueba sin Flask
  simulador.py         fachada única: candado, operaciones atómicas e instantánea del estado
  config.py            parámetros de la simulación y sus rangos válidos
  entradas.py          lectura estricta de los valores que llegan del usuario
  errores.py           errores con su código HTTP (400, 404, 409, 500)
  cripto.py            claves Ed25519, firmar y verificar
  bloque.py            transacciones, bloques, hash y estructura
  reglas.py            dificultad, sorteo ponderado, umbral de 2/3, castigo y votos
  libro.py             saldos derivados de la cadena (recompensas, castigos)
  validacion.py        validación de bloques, cadenas y transacciones nuevas
  red.py               nodos, difusión, sincronización y transacciones pendientes
  pow.py               minería por rondas con nonces disjuntos
  pos.py               ronda PoS como máquina de estados
  trampas.py           comportamiento de los nodos tramposos
  bitacora.py          eventos que muestra la interfaz
  reloj.py             reloj simulado (reproducible) o real
  motor.py             hilo que avanza la minería y las rondas automáticas
templates/index.html   la página
static/estilos.css     estilos (tema claro y oscuro)
static/js/             la interfaz, en módulos (un archivo por zona y por paso)
tests/                 pruebas pytest (núcleo, API y los casos de la guía)
```

## Solución de problemas

- **`flask` no se reconoce:** el entorno no está activado; actívalo o usa
  `python -m flask run`.
- **PowerShell no deja ejecutar `Activate.ps1`:** ejecuta una vez
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` y vuelve a activar.
- **`Port 5000 is in use`:** usa otro puerto, por ejemplo `flask run --port 5001`.
- **Error `unsupported operand type(s) for |` al arrancar:** la versión de Python es
  menor que 3.10; instala una más reciente y vuelve a crear el entorno.
- **La página dice «Sin conexión con el servidor; reintentando…»:** el servidor se
  detuvo; vuelve a correr `flask run`.
