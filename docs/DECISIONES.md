# Decisiones técnicas

Registro de decisiones que no estaban fijadas en el anteproyecto. Formato: **qué**, **por qué** y **alternativa**.

---

### D-001 · Python 3.11 en `.venv`
- **Qué:** el proyecto fija `requires-python = ">=3.11,<3.12"` y usa un `.venv` local.
- **Por qué:** es la versión más estable para torch CPU, chromadb, sentence-transformers y langgraph. Python 3.13/3.14 todavía da problemas con algunas ruedas.
- **Alternativa:** 3.12, cuando todas las dependencias tengan ruedas estables.

### D-002 · `tasks.py` como runner y `Makefile` como fachada
- **Qué:** las tareas (`setup`, `test`, `ingest`, `bench`, `run`…) viven en `tasks.py`. El `Makefile` solo delega en él.
- **Por qué:** en Windows no suele haber `make`. Así existe una sola fuente de verdad y se cumple el requisito de "un comando".
- **Alternativa:** `just` o `invoke`, pero sería una dependencia extra.

### D-003 · Dependencias: `requirements.txt` (directas) + `requirements-lock.txt` (freeze completo)
- **Qué:** las dependencias directas van fijadas con `==` en `requirements.txt`. El entorno exacto resuelto queda en `requirements-lock.txt`, que es lo que instala `setup`.
- **Por qué:** reproducibilidad sin añadir herramientas como poetry o uv. torch se instala desde el índice CPU de PyTorch para no bajar CUDA.
- **Alternativa:** `uv lock` o `pip-tools`.

### D-004 · Proveedor de LLM: Anthropic, con modo `mock` por defecto
- **Qué:** `LLM_PROVIDER ∈ {anthropic, mock}`. El modelo se elige con `LLM_MODEL`; por defecto es **`claude-haiku-5-5`**. Sin `.env`, todo corre en `mock`.
- **Por qué:** es la decisión del equipo. El modo mock permite correr tests y CI sin costo. Se eligió Haiku 5.5 ($0.10 / $0.50 por MTok) porque una consulta típica (~3k tokens de entrada y ~400 de salida) cuesta ≈ 0.0005 USD, muy por debajo de la meta de 0.01 USD. Además, la tarea (redactar con contexto dado, citar y devolver JSON) no requiere un modelo grande.
- **Alternativa:** `claude-opus-5-5` ($4 / $20), que sale ≈ 0.02 USD por consulta y no cumple la meta. Se puede comparar en el benchmark cambiando solo `LLM_MODEL` y las tarifas.

### D-005 · "Temperatura 0" → reproducibilidad por otras vías
- **Qué:** no se envía `temperature`. La versión actual de la API de Anthropic rechaza los parámetros de muestreo (`temperature`, `top_p`, `top_k`) en los modelos actuales.
- **Por qué:** la intención del anteproyecto (respuestas reproducibles) se cumple con: prompt fijo, salida estructurada validada con Pydantic, `effort` bajo y contexto ordenado de forma determinista. La variabilidad residual se mide en el benchmark.
- **Alternativa:** usar un modelo antiguo que acepte `temperature`. Se descartó porque son modelos obsoletos.

### D-006 · Selección del corpus semilla (`data/seed_titles.txt`)
- **Qué:** 83 artículos con títulos canónicos verificados contra la API: los 17 reinos, 16 listas de Power Moons por reino (Darker Side no tiene lista propia), jefes, mecánicas (Cappy, Capture, Power Moon, Moon Rock…), tienda (Crazy Cap), atuendos y capturas y habitantes propios del juego.
- **Por qué:** cubre la "cobertura mínima por reino" del anteproyecto. Se **excluyen** artículos multi-juego muy largos (Bowser, Goomba, Chain Chomp, Warp Pipe, Pauline, Peach's Castle) porque más del 90 % de su texto trata de otros juegos y metería ruido en la recuperación.
- **Alternativa:** incluirlos y filtrar solo sus secciones de *Super Mario Odyssey*. Queda como mejora si el benchmark muestra huecos.

### D-007 · Se guarda wikitexto crudo **y** expandido
- **Qué:** cada página guarda `wikitext` (la revisión exacta, con su `revid`) y `expanded_wikitext` (resultado de `action=expandtemplates`).
- **Por qué:** las listas de Power Moons son solo una plantilla (`{{SMO Power Moons|...}}`). La descripción de cada luna (cómo conseguirla y su disponibilidad) solo aparece al expandirla. El crudo se conserva para infoboxes y tablas.
- **Limitación:** `expandtemplates` usa la versión *actual* de las plantillas, no la vigente en la fecha del `revid`. Para los snapshots históricos (`--as-of`) esto puede introducir diferencias en el contenido que viene de plantillas. Se documenta en el informe.

### D-008 · Formato del snapshot
- **Qué:** `data/raw/<AAAAMMDD>[-asof]/manifest.json` + `pages/<pageid>.json`. El ID usa la fecha de corte en UTC. El manifest guarda el `revid`, el timestamp, la URL y el SHA-256 del wikitexto de cada página. Al cargar se verifica el hash.
- **Por qué:** cada snapshot es una "foto completa" versionada e inmutable (no se sobreescribe sin `--overwrite`). `--as-of <fecha>` descarga la revisión vigente en esa fecha (`rvstart` + `rvdir=older`); así se generan los dos snapshots de los casos dependientes de versión con datos reales.
- **Alternativa:** guardar todo en un solo JSONL. Se descartó porque un archivo por página es más fácil de inspeccionar y de comparar entre versiones.
