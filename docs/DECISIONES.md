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
- **Qué:** `LLM_PROVIDER ∈ {anthropic, mock}`. El modelo se elige con `LLM_MODEL` (por defecto `claude-opus-5-5`). Sin `.env`, todo corre en `mock`.
- **Por qué:** es la decisión del equipo. El modo mock permite correr tests y CI sin costo.
- **Nota de costo:** con Opus 5.5 ($4 / $20 por MTok), una consulta típica (~3k tokens de entrada y ~400 de salida) cuesta ≈ 0.02 USD, **por encima** de la meta de 0.01 USD. Claude Haiku 5.5 ($0.10 / $0.50) la cumple con mucho margen. La elección del modelo queda a criterio del equipo y se reporta en el benchmark.

### D-005 · "Temperatura 0" → reproducibilidad por otras vías
- **Qué:** no se envía `temperature`. La versión actual de la API de Anthropic rechaza los parámetros de muestreo (`temperature`, `top_p`, `top_k`) en los modelos actuales.
- **Por qué:** la intención del anteproyecto (respuestas reproducibles) se cumple con: prompt fijo, salida estructurada validada con Pydantic, `effort` bajo y contexto ordenado de forma determinista. La variabilidad residual se mide en el benchmark.
- **Alternativa:** usar un modelo antiguo que acepte `temperature`. Se descartó porque son modelos obsoletos.
